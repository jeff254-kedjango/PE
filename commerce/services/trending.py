"""Trending rail — the §8 board of BOOSTED PRODUCTS near the buyer.

The rail (a fixed, right-of-feed card, frontend) shows boosted LISTINGS as category-colored product
cards: title + price + a category icon. Its membership is a PURE FUNCTION of the ``location_bucket``
— the server returns the full bounded QUEUE of eligible product cards and there is no per-viewer
state, no push, no sweep:

  * **location bucket** — the buyer's (lat,lng) is snapped to a fixed ~``trending_bucket_m`` grid
    cell. Every viewer in the same cell gets the SAME queue (and the same bucket-centre distances),
    so the router caches one payload per bucket in Redis (TTL = ``poll_seconds``) and every nearby
    buyer shares it. O(1) per viewer after the first.

  * **per-slot decay (client-owned)** — the slate carries ``visible_slots`` (how many cards show at
    once) and ``slot_seconds`` (each card's lifetime). The CLIENT renders that many slots, decays
    each on its own staggered timer, and pulls the next queued product into a freed slot — so every
    queued product gets fair airtime without the server materializing a rotation. ``slot_seconds``
    shrinks toward ``min_slot`` under contention (more products than the cap) and is always > 5 s.

Eligibility + scope reuse the §8.3 Boost machinery (``boost.eligible_grants`` with
``target_type='listing'`` so the candidate cap counts only listing grants) — the bounded,
GiST-indexed pull. **Shop-level boosts are deliberately excluded** from trending (they appear only
in the in-feed sponsored lane); the rail is product-first. The queue is filtered to LIVE, in-stock
listings so a sold-out / inactive product never advertises a price.

NOTE — contract change vs the prior shop rail: there is no longer a server-side time-window slice or
cross-viewer lock-step rotation. Co-located viewers share the same QUEUE but their decay phases are
client-local (seeded at load), so the visible cards may differ moment-to-moment. This is intended.

Output carries only opaque ids + seller-PUBLISHED fields (title, price, category, property_uuid) —
no PII (S6). ``now`` is always injected by the caller so the slate is deterministic and testable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from PE.commerce.core.config import settings
from PE.commerce.models.boost import TARGET_LISTING, TIER_WEIGHT
from PE.commerce.models.listing import Listing
from PE.commerce.models.seller import Shop
from PE.commerce.services import boost
from PE.commerce.services.quick_buys import first_image_url

_EARTH_RADIUS_M = 6_371_000.0


@dataclass(frozen=True)
class TrendingCard:
    listing_id: str
    seller_id: str
    title: str
    price_cents: int
    currency: str
    category: str | None
    property_uuid: str | None
    distance_m: float
    boost_tier: str
    # The PRODUCT's own lead image (first non-video media URL) — so a promoted card shows the actual
    # item for sale, not a shop logo or a bare category tint. None ⇒ the client falls back to the
    # category glyph. Derived from Listing.media_urls in the same batch as category (no N+1), via the
    # shared quick_buys.first_image_url so the video-skip rule lives in one place.
    image_url: str | None = None


@dataclass(frozen=True)
class Slate:
    # The FULL ordered queue of boosted product cards in this bucket (bounded by the eligibility
    # cap). The client renders `visible_slots` of them and cycles the rest through freed slots.
    cards: list[TrendingCard]
    # How many cards show at once (the client animates this many slots).
    visible_slots: int
    # Per-card lifetime in seconds (client decay timer). Always > 5; shrinks under contention.
    slot_seconds: int
    # The client's re-poll cadence AND the server cache TTL (queue membership changes slowly).
    poll_seconds: int
    bucket: str
    # Total boosted products eligible in this bucket (= len(cards); ≥ visible_slots under contention).
    active_count: int


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in metres between two (lat,lng) points (pure Python — the rail
    computes distances from the bucket centre to each product in app code, not SQL)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlng = math.radians(lng2 - lng1)
    # Clamp the acos argument against float drift just over 1.0 (identical points).
    cos_c = min(1.0, math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dlng))
    return _EARTH_RADIUS_M * math.acos(cos_c)


def bucket_for(lat: float, lng: float, *, bucket_m: float | None = None) -> tuple[float, float, str]:
    """Snap (lat,lng) to the centre of its ~``bucket_m`` grid cell and return
    ``(bucket_lat, bucket_lng, bucket_key)``. Snapping makes every viewer in the cell share one
    queue (and identical distances). The cell size in DEGREES is derived from metres: latitude is
    ~111.32 km/deg everywhere; longitude is scaled by cos(lat) so cells stay roughly square moving
    north/south. ``bucket_key`` is a stable string for the Redis cache key."""
    bucket_m = bucket_m or settings.trending_bucket_m
    lat_deg = bucket_m / 111_320.0
    blat = round(round(lat / lat_deg) * lat_deg, 6)
    # Derive the longitude cell width from the BUCKETED latitude (blat), NOT the raw lat. Two points
    # at slightly different latitudes must share one longitude grid once they fall in the same
    # lat-cell — keying lng_deg off the raw lat would give them different cell widths and split the
    # bucket (the shared-queue guarantee would break for points a few metres apart). Guard cos()
    # near the poles so the cell never explodes / divides by ~0.
    cos_lat = max(math.cos(math.radians(blat)), 0.01)
    lng_deg = bucket_m / (111_320.0 * cos_lat)
    blng = round(round(lng / lng_deg) * lng_deg, 6)
    return blat, blng, f"{blat}:{blng}"


def build_slate(
    db: Session, lat: float, lng: float, *, now: datetime | None = None,
) -> Slate:
    """Compute the trending product queue for the buyer's locality at ``now`` (deterministic).

    Steps (all O(bounded)):
      1. snap to the location bucket (shared cache key + shared distances);
      2. pull eligible LIVE LISTING grants whose scope reaches the bucket centre (capped, GiST,
         listing-only so the cap isn't consumed by shop grants);
      3. dedup per listing, keeping the WIDEST tier (a listing with two grants collapses to one);
      4. load product display-meta + Shop.category in ONE batch (no N+1), filtered to LIVE,
         in-stock listings (a sold-out / inactive product never advertises a price);
      5. order deterministically (widest tier, then earliest start, then listing id);
      6. derive the contention slot lifetime; the client owns the per-slot decay over the queue.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    blat, blng, bkey = bucket_for(lat, lng)

    grants = boost.eligible_grants(db, blat, blng, now=now, target_type=TARGET_LISTING)

    # Widest tier (+ its started_at) per listing — a listing boosted at mtaa AND sovereign collapses
    # to one card represented by its widest reach.  listing_id -> (tier, started_at)
    listing_best: dict[str, tuple[str, datetime]] = {}
    for g in grants:
        cur = listing_best.get(g.target_id)
        if cur is None or TIER_WEIGHT[g.tier] > TIER_WEIGHT[cur[0]]:
            listing_best[g.target_id] = (g.tier, g.started_at)

    poll = settings.trending_poll_seconds
    cap = max(1, settings.trending_visible_cap)

    if not listing_best:
        # Empty queue — a quiet locality with no live product boosts. slot_seconds is the base
        # lifetime (irrelevant with no cards, but a stable value), active_count 0.
        return Slate(cards=[], visible_slots=cap, slot_seconds=settings.trending_base_slot_s,
                     poll_seconds=poll, bucket=bkey, active_count=0)

    # Batch product meta + Shop.category in one join (no N+1). Filter to LIVE, in-stock listings so a
    # boost that outlived the product's availability never surfaces a buyable-looking dead card.
    listing_ids = list(listing_best.keys())
    meta_rows = (
        db.query(
            Listing.id, Listing.seller_id, Listing.title, Listing.price_cents, Listing.currency,
            Listing.property_uuid, Listing.lat, Listing.lng, Shop.category, Listing.media_urls,
        )
        .join(Shop, Listing.shop_id == Shop.id)
        .filter(
            Listing.id.in_(listing_ids),
            Listing.is_active.is_(True),
            Listing.stock_qty > 0,
        )
        .all()
    )

    ordered: list[TrendingCard] = []
    for lid, seller_id, title, price_cents, currency, property_uuid, llat, llng, category, media_urls in meta_rows:
        tier, _started = listing_best[str(lid)]
        ordered.append(TrendingCard(
            listing_id=str(lid),
            seller_id=str(seller_id),
            title=title,
            price_cents=price_cents,
            currency=currency,
            category=category,
            property_uuid=property_uuid,
            distance_m=round(_haversine_m(blat, blng, llat, llng), 2),
            boost_tier=tier,
            image_url=first_image_url(media_urls),
        ))

    # Deterministic order: widest reach first, then earliest started (longest-running leads), then
    # stable listing id. (started_at pulled from listing_best to tiebreak fairly.)
    ordered.sort(key=lambda c: (-TIER_WEIGHT[c.boost_tier], listing_best[c.listing_id][1], c.listing_id))

    # A live listing may have been filtered out (inactive/out of stock) after its grant matched, so
    # active_count is the count of SERVABLE product cards, not the raw grant count.
    active = len(ordered)
    if active == 0:
        return Slate(cards=[], visible_slots=cap, slot_seconds=settings.trending_base_slot_s,
                     poll_seconds=poll, bucket=bkey, active_count=0)

    return Slate(
        cards=ordered,
        visible_slots=cap,
        slot_seconds=_contention_slot(active, cap),
        poll_seconds=poll,
        bucket=bkey,
        active_count=active,
    )


def _contention_slot(active: int, cap: int) -> int:
    """Per-card lifetime (seconds) for the current contention. Quiet (active ≤ cap) → full base slot
    (a lone product persists, nothing churns). Busy → base shrinks by the number of cap-sized windows
    needed to show everyone, floored at min_slot (kept > 5 s so a card is always readable). O(1)."""
    base = settings.trending_base_slot_s
    floor = settings.trending_min_slot_s
    windows = math.ceil(active / cap)  # 1 when quiet
    return max(floor, base // max(1, windows))
