"""Hydrated live-viewers service (§8 Chunk C+).

Turns a shop's LIVE rows in ``shop_view_events`` into human-facing rows for the seller-
console Viewing Card. One purpose: `get_hydrated_live_viewers(db, shop_id)` — everything
else in this module supports that call.

Orchestration:
  1. Fetch the live viewer rows for this shop (last heartbeat within LIVE_WINDOW_SECONDS).
  2. Batch-lookup viewer identity from weespas (one bridge call for every signed-in viewer).
  3. Reverse-geocode each viewer's coarse coord to a Nairobi neighbourhood label.
  4. Batch-fetch the current `viewing_listing` titles (one IN() query for all listings).
  5. Apply the followers-only phone rule: a viewer's phone is exposed to this seller ONLY
     IF (viewer follows this shop AND the bridge returned a phone). Non-followers see no
     phone even when weespas has one.
  6. Assemble hydrated rows.

Anonymous viewers (viewer_uuid = NULL) show up as 'Guest' with no phone, no avatar. They
still populate the area + product-viewed columns when the client sent coords / a listing id.
That's deliberate: an anonymous browsing shopper is still valuable signal for the seller,
even without a name.

Bounded work:
  * O(live) — the input is capped by the freshness window (~seconds) not all-time volume.
  * ONE bridge call (batched), ONE reverse-geocode batch, ONE listing lookup, ONE follows
    membership check. Never N+1.
  * Bridge failure degrades gracefully: viewer is labelled 'Guest', card still renders.

The endpoint layer (routers/shop_views.py) is thin: ownership check → this service → shape
into the response schema. All the composition lives here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from PE.commerce.models.listing import Listing
from PE.commerce.models.seller import ShopSubscription
from PE.commerce.models.shop_view import ShopViewEvent
from PE.commerce.services import reverse_geocode as geo
from PE.commerce.services import weespas_client
from PE.commerce.services.shop_views import LIVE_WINDOW_SECONDS, _ensure_aware


@dataclass(frozen=True)
class HydratedViewer:
    """One row on the Viewing Card's live tab. Every field is optional at the display
    layer — a viewer might be anonymous, might have denied geolocation, might not be on a
    listing, might have blocked showing their phone. The card renders whatever's present."""
    session_id: str                     # stable key so React can list-key without leaking uuid
    viewer_uuid: Optional[str]          # None for anonymous
    display_name: str                   # 'Guest' when no identity available
    avatar_url: Optional[str]
    phone: Optional[str]                # ONLY populated when viewer follows this shop
    area_label: Optional[str]           # 'Kilimani', 'CBD', ...; None outside Nairobi metro
    viewing_listing_id: Optional[str]
    viewing_listing_title: Optional[str]
    last_heartbeat_at: datetime


# Label shown to the seller for viewers we can't identify (anonymous, or the bridge is
# unavailable). Deliberately generic — never a fake name.
_GUEST_LABEL = "Guest"


def get_hydrated_live_viewers(
    db: Session, *, shop_id: str, now: datetime,
) -> list[HydratedViewer]:
    """Return the LIVE viewers for the shop, one hydrated row each.

    Ordered newest-heartbeat first — the freshest visitor is at the top of the seller's
    card. Callers don't need to sort.

    Returns [] when the shop has no live viewers (the endpoint returns 200 with an empty
    list, not a 404 — an empty live-viewers set is the normal case for a quiet shop).
    """
    if not shop_id:
        return []
    now = _ensure_aware(now)
    cutoff = now - timedelta(seconds=LIVE_WINDOW_SECONDS)

    # 1) Fetch the live rows. Range scan on (shop_id, last_heartbeat_at DESC).
    rows = (
        db.query(ShopViewEvent)
        .filter(
            ShopViewEvent.shop_id == shop_id,
            ShopViewEvent.last_heartbeat_at > cutoff,
        )
        .order_by(ShopViewEvent.last_heartbeat_at.desc())
        .all()
    )
    if not rows:
        return []

    # 2) Batch-lookup identities. Only signed-in viewers have a uuid to look up. Anonymous
    # rows contribute nothing to the batch — they'll be labelled 'Guest' regardless.
    signed_in_uuids = [r.viewer_uuid for r in rows if r.viewer_uuid]
    summaries = weespas_client.lookup_user_summaries(signed_in_uuids) if signed_in_uuids else {}

    # 3) Reverse-geocode. Called once per row (the neighbourhood table is small — one
    # BETWEEN query returns the row with the lowest priority match). If we later find this
    # showing up in a profile we can lift to a batch query keyed by rounded coords, but at
    # ~30 live viewers × ~30 rows it's negligible.
    area_by_session: dict[str, Optional[str]] = {}
    for r in rows:
        area_by_session[r.session_id] = geo.reverse_geocode(db, r.last_lat, r.last_lng)

    # 4) Batch-fetch listing titles. De-duplicated so a shop with 20 viewers all on the
    # same PDP still gets one query, not 20.
    listing_ids = list({r.viewing_listing_id for r in rows if r.viewing_listing_id})
    title_by_id: dict[str, str] = {}
    if listing_ids:
        for row in (
            db.query(Listing.id, Listing.title)
            .filter(Listing.id.in_(listing_ids))
            .all()
        ):
            title_by_id[row.id] = row.title

    # 5) Followers-only phone rule. Pull the follower set for this shop RESTRICTED to the
    # signed-in viewers we're rendering — one indexed range on (user_uuid IN (...), shop_id).
    followers: set[str] = set()
    if signed_in_uuids:
        followers = {
            row.user_uuid
            for row in db.query(ShopSubscription.user_uuid).filter(
                ShopSubscription.shop_id == shop_id,
                ShopSubscription.user_uuid.in_(signed_in_uuids),
            )
        }

    # 6) Assemble.
    out: list[HydratedViewer] = []
    for r in rows:
        summary = summaries.get(r.viewer_uuid) if r.viewer_uuid else None
        # Followers-only phone: even if the bridge returned one, we withhold unless the
        # viewer explicitly follows this shop (implicit consent via follow).
        phone: Optional[str] = None
        if summary and summary.phone and r.viewer_uuid in followers:
            phone = summary.phone
        # Fall through to 'Guest' when: anonymous row, OR the bridge failed / had no record.
        # We never expose the bare uuid as a name.
        display_name = (summary.display_name if summary else "") or _GUEST_LABEL
        out.append(HydratedViewer(
            session_id=r.session_id,
            viewer_uuid=r.viewer_uuid,
            display_name=display_name,
            avatar_url=summary.avatar_url if summary else None,
            phone=phone,
            area_label=area_by_session.get(r.session_id),
            viewing_listing_id=r.viewing_listing_id,
            viewing_listing_title=(
                title_by_id.get(r.viewing_listing_id) if r.viewing_listing_id else None
            ),
            last_heartbeat_at=_ensure_aware(r.last_heartbeat_at),
        ))
    return out
