"""Storefront view tracking service (§8, Chunk C) — the Viewing Card's data layer.

Three surfaces:
  * ``record_heartbeat`` — the 30-second ping from a browser watching a storefront. Upserts on
    (shop_id, session_id): first ping INSERTS, subsequent pings UPDATE ``last_heartbeat_at``.
    Runs on both anonymous and signed-in viewers; ``viewer_uuid`` is None for anons.
  * ``count_live_viewers`` — the "big number on the card". A viewer is LIVE iff their last
    heartbeat is within ``LIVE_WINDOW_SECONDS`` (default 60s — 2 missed 30s pings). O(live rows).
  * ``list_view_history`` — the History tab. Keyset-paginated over (viewed_at DESC, id) with
    optional ``since`` / ``until`` filters for the calendar picker.

The heartbeat contract:
  * The client generates a stable-per-visit ``session_id`` (short random string in localStorage).
  * The server treats ``session_id`` as opaque — it's a bucketing token, not identity. A rogue
    client submitting a fresh id every ping would fragment their own history, not ours.
  * A signed-in viewer's ``sub`` is captured on FIRST insert only. If the same session_id later
    signs in mid-visit we don't backfill — the row started anonymous and stays anonymous. That
    prevents an anonymous-then-authed session from stealing a de-anonymized history slot.

O(1) complexity notes:
  * Upsert is a UNIQUE-index lookup (ux_shop_view_events_shop_session) → SELECT + INSERT-or-UPDATE.
  * Live count is a range scan on (shop_id, last_heartbeat_at DESC) cut by the freshness window.
  * History is a range scan on (shop_id, viewed_at DESC) with a keyset cursor.
None of these are proportional to all-time view volume.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from PE.commerce.core.config import settings
from PE.commerce.models.listing import Listing, PROMO_EVERGREEN
from PE.commerce.models.seller import Seller, Shop
from PE.commerce.models.shop_view import ShopViewEvent, VIEW_SOURCE_STOREFRONT


# A viewer is LIVE if we've heard from them in the last 60 seconds. That's TWO 30-second pings
# of tolerance for a flaky mobile connection — a single dropped packet doesn't drop them off
# the count. If we ever move to a 15s heartbeat this stays valid (a 45s window is still 3x the
# heartbeat interval, which is safe).
LIVE_WINDOW_SECONDS = 60

# Cap on the session_id length. Mirrors the DB column so a rogue client can't pad — validating
# here means the router doesn't need to; and the service's caller (endpoint) doesn't need to
# duplicate the check either.
_MAX_SESSION_ID_LEN = 64
# Cap on how many history rows a single query can return. A polite ceiling that keeps the
# response bounded regardless of what the caller asks for.
_MAX_HISTORY_LIMIT = 200
_DEFAULT_HISTORY_LIMIT = 50


class HeartbeatError(ValueError):
    """Bad heartbeat input (empty session_id, oversized session_id, unknown source).
    Router → 422."""


@dataclass(frozen=True)
class HeartbeatOutcome:
    """The service reports which side of the upsert fired so the router / tests can assert on
    'this was the first heartbeat of a new visit' vs 'this was a same-session refresh'."""
    event: ShopViewEvent
    was_new_visit: bool         # True when we INSERTed a new row; False when we UPDATEd.


def _ensure_aware(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; the caller may pass either. Compare in UTC always."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def record_heartbeat(
    db: Session,
    *,
    shop_id: str,
    session_id: str,
    viewer_uuid: Optional[str],
    now: datetime,
    source: str = VIEW_SOURCE_STOREFRONT,
    viewing_listing_id: Optional[str] = None,
    last_lat: Optional[float] = None,
    last_lng: Optional[float] = None,
) -> HeartbeatOutcome:
    """Upsert a view row for (shop_id, session_id). First heartbeat inserts; subsequent
    heartbeats refresh ``last_heartbeat_at`` on the existing row.

    Args:
      shop_id, session_id: the composite key. Neither may be empty. session_id is capped at
        64 chars to match the DB column and prevent a rogue client from padding.
      viewer_uuid: the weespas ``sub`` if signed in, else None. Captured ONLY on the first
        heartbeat; a signed-in reload of an anonymous session keeps the row anonymous.
      now: caller-supplied clock. Tests pass a fixed instant; the endpoint passes `datetime.
        now(timezone.utc)`.
      viewing_listing_id: §8 Chunk C+. The listing the visitor is looking at RIGHT NOW.
        LATEST WINS: every heartbeat overwrites this column with the caller-supplied value —
        including None, which clears it (a visitor who leaves a PDP and returns to the
        storefront index sends a null and stops being "viewing product X"). NOT sticky like
        viewer_uuid: the whole point is the seller sees which product the visitor is CURRENTLY
        on, not what they opened earlier in the session.

    Raises: HeartbeatError on bad input (never IntegrityError — the race is caught internally).
    """
    if not shop_id:
        raise HeartbeatError("shop_id must not be empty")
    if not session_id:
        raise HeartbeatError("session_id must not be empty")
    if len(session_id) > _MAX_SESSION_ID_LEN:
        raise HeartbeatError(f"session_id too long (max {_MAX_SESSION_ID_LEN})")
    now = _ensure_aware(now)
    # Normalize empty string → None so a client that sends viewing_listing_id="" doesn't
    # store the empty string (it would break equality checks against real ids).
    normalized_listing = viewing_listing_id or None
    # Drop bogus coords silently — a client that ends up with NaN/out-of-range values gets
    # the same treatment as one that didn't send coords at all. The service is the last
    # line of defense here (the router should validate too, but this makes the service
    # safe to call directly).
    if last_lat is not None and (last_lat != last_lat or not (-90.0 <= last_lat <= 90.0)):
        last_lat = None
    if last_lng is not None and (last_lng != last_lng or not (-180.0 <= last_lng <= 180.0)):
        last_lng = None
    # If one coord is set and the other isn't, drop both — a half-coord is meaningless.
    if last_lat is None or last_lng is None:
        last_lat = last_lng = None

    existing = (
        db.query(ShopViewEvent)
        .filter(ShopViewEvent.shop_id == shop_id, ShopViewEvent.session_id == session_id)
        .one_or_none()
    )
    if existing is not None:
        existing.last_heartbeat_at = now
        # Overwrite unconditionally — including with None. This is the "latest wins" contract.
        existing.viewing_listing_id = normalized_listing
        existing.last_lat = last_lat
        existing.last_lng = last_lng
        db.commit()
        db.refresh(existing)
        return HeartbeatOutcome(event=existing, was_new_visit=False)

    row = ShopViewEvent(
        shop_id=shop_id,
        viewer_uuid=viewer_uuid or None,   # normalize empty string → NULL
        session_id=session_id,
        viewed_at=now,
        last_heartbeat_at=now,
        source=source,
        viewing_listing_id=normalized_listing,
        last_lat=last_lat,
        last_lng=last_lng,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # Race: two heartbeats from the same session hit `SELECT ... one_or_none` before either
        # committed. The unique index catches the second INSERT; roll back and treat the second
        # arrival as a refresh (the FIRST committed insert now IS the row). Bounded retry
        # (a single retry is enough — a third racing insert would have to lose the unique-index
        # coin toss again).
        db.rollback()
        existing = (
            db.query(ShopViewEvent)
            .filter(ShopViewEvent.shop_id == shop_id, ShopViewEvent.session_id == session_id)
            .one()
        )
        existing.last_heartbeat_at = now
        existing.viewing_listing_id = normalized_listing
        existing.last_lat = last_lat
        existing.last_lng = last_lng
        db.commit()
        db.refresh(existing)
        return HeartbeatOutcome(event=existing, was_new_visit=False)

    db.refresh(row)
    return HeartbeatOutcome(event=row, was_new_visit=True)


def count_live_viewers(db: Session, *, shop_id: str, now: datetime) -> int:
    """How many distinct viewers are currently on the storefront (last heartbeat within
    LIVE_WINDOW_SECONDS). Range scan on (shop_id, last_heartbeat_at DESC) → O(live)."""
    if not shop_id:
        return 0
    now = _ensure_aware(now)
    cutoff = now - timedelta(seconds=LIVE_WINDOW_SECONDS)
    # last_heartbeat_at is always the newest ping for each session (upsert semantics), so a
    # simple COUNT is correct — no DISTINCT session_id needed, the unique index already made
    # (shop_id, session_id) one-row-per-session.
    return (
        db.query(ShopViewEvent)
        .filter(
            ShopViewEvent.shop_id == shop_id,
            ShopViewEvent.last_heartbeat_at > cutoff,
        )
        .count()
    )


@dataclass(frozen=True)
class ViewHistoryRow:
    """A single past visit — for the History tab list. Includes ``last_heartbeat_at`` so the
    UI can show 'they stayed 4 minutes' if the caller wants a duration display."""
    event_id: str
    viewer_uuid: Optional[str]
    session_id: str
    viewed_at: datetime
    last_heartbeat_at: datetime


@dataclass(frozen=True)
class ViewHistoryPage:
    rows: list[ViewHistoryRow]
    next_cursor: Optional[str]  # opaque; pass back to `list_view_history` to fetch the next page


def _decode_cursor(cursor: Optional[str]) -> Optional[tuple[datetime, str]]:
    """Decode an opaque keyset cursor into (viewed_at, event_id). Format: 'ISO|id'. Returns
    None for None or malformed cursors (a malformed cursor is treated as "start from the top"
    rather than raising — a stale cursor from a client is a normal event, not a bug)."""
    if not cursor:
        return None
    try:
        iso, event_id = cursor.split("|", 1)
        dt = datetime.fromisoformat(iso)
        return _ensure_aware(dt), event_id
    except (ValueError, AttributeError):
        return None


def _encode_cursor(row: ShopViewEvent) -> str:
    """Encode the keyset cursor for the next page: the (viewed_at, id) of the LAST row on this
    page. The caller passes this back verbatim."""
    return f"{row.viewed_at.isoformat()}|{row.id}"


def list_view_history(
    db: Session,
    *,
    shop_id: str,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    cursor: Optional[str] = None,
    limit: int = _DEFAULT_HISTORY_LIMIT,
) -> ViewHistoryPage:
    """Newest-first history of visits for this shop, optionally within [since, until] and
    optionally continuing from a prior page's cursor.

    Keyset pagination on (viewed_at DESC, id DESC) — the id tiebreaks the (very unlikely)
    two-visits-in-the-same-microsecond case. Returns at most ``limit`` rows (server-capped at
    200) and a next_cursor when there's more.
    """
    if not shop_id:
        return ViewHistoryPage(rows=[], next_cursor=None)
    limit = max(1, min(limit, _MAX_HISTORY_LIMIT))

    q = db.query(ShopViewEvent).filter(ShopViewEvent.shop_id == shop_id)
    if since is not None:
        q = q.filter(ShopViewEvent.viewed_at >= _ensure_aware(since))
    if until is not None:
        q = q.filter(ShopViewEvent.viewed_at <= _ensure_aware(until))

    cur = _decode_cursor(cursor)
    if cur is not None:
        cur_dt, cur_id = cur
        # Keyset step: rows strictly OLDER than the last row seen, or same-timestamp rows with
        # a smaller id. The ORDER BY below matches this predicate exactly.
        q = q.filter(
            (ShopViewEvent.viewed_at < cur_dt)
            | ((ShopViewEvent.viewed_at == cur_dt) & (ShopViewEvent.id < cur_id))
        )

    q = q.order_by(ShopViewEvent.viewed_at.desc(), ShopViewEvent.id.desc()).limit(limit + 1)
    events = q.all()

    has_more = len(events) > limit
    page = events[:limit]
    next_cursor = _encode_cursor(page[-1]) if has_more and page else None
    return ViewHistoryPage(
        rows=[
            ViewHistoryRow(
                event_id=e.id,
                viewer_uuid=e.viewer_uuid,
                session_id=e.session_id,
                viewed_at=_ensure_aware(e.viewed_at),
                last_heartbeat_at=_ensure_aware(e.last_heartbeat_at),
            )
            for e in page
        ],
        next_cursor=next_cursor,
    )


# ---------------------------------------------------------------------------
# Promote-all: the Viewing Card's "boost my shop back into the feed" button
# ---------------------------------------------------------------------------


class PromoteAllError(ValueError):
    """Bad promote-all input (bad duration, not the caller's shop). Router → 4xx."""


@dataclass(frozen=True)
class PromoteAllResult:
    """Summary of a shop-wide promotion."""
    shop_id: str
    promoted_count: int
    skipped_ids: list[str]      # active listings we tried to promote but skipped for a reason
                                # (today: none, but the field is here so we can surface e.g. flash-sale-conflicts later)
    expires_at: datetime


def promote_all_active_listings(
    db: Session,
    *,
    shop_id: str,
    user_uuid: str,
    duration_seconds: int,
    now: datetime,
    mode: str = PROMO_EVERGREEN,
) -> PromoteAllResult | None:
    """Open (or refresh) an evergreen promotion window on EVERY active listing in the shop.

    Returns the summary, or ``None`` if the caller doesn't own the shop (router → 404 with the
    uniform 'shop or listing not found' message — no cross-owner existence leak, matches the
    catalog service's discipline).

    Duration is bounded by the same ``promo_min/max_duration_seconds`` as the single-listing
    promote — a shop-wide promote is just N single promotes, not a new mechanic. Runs in ONE
    transaction so a stray listing can't leave the shop half-promoted.

    Out-of-stock listings and inactive listings are SKIPPED — a promoted item that can't be
    bought would be a false-precision boost. Their ids don't appear in ``skipped_ids`` (that
    field is reserved for listings the caller might reasonably expect to be promoted but
    aren't; out-of-stock is by-design, not surprising).
    """
    if mode not in (PROMO_EVERGREEN,):
        # For now the shop-wide button only supports evergreen — a future "story" bulk mode
        # would break the "everything just fades back to normal" contract.
        raise PromoteAllError(f"mode must be 'evergreen', got {mode!r}")
    if not (settings.promo_min_duration_seconds <= duration_seconds <= settings.promo_max_duration_seconds):
        raise PromoteAllError(
            f"duration_seconds must be between {settings.promo_min_duration_seconds} and "
            f"{settings.promo_max_duration_seconds}"
        )
    now = _ensure_aware(now)

    # Ownership check via join — one indexed lookup, no cross-owner leak.
    shop = (
        db.query(Shop)
        .join(Seller, Shop.seller_id == Seller.id)
        .filter(Shop.id == shop_id, Seller.user_uuid == user_uuid)
        .one_or_none()
    )
    if shop is None:
        return None

    # Pull every active + in-stock listing in one query. Not eager-loading; we only need the
    # rows themselves (the promotion fields live directly on Listing).
    listings = (
        db.query(Listing)
        .filter(
            Listing.shop_id == shop_id,
            Listing.is_active == True,   # noqa: E712 — SQLAlchemy comparison
            Listing.stock_qty > 0,
        )
        .all()
    )

    expires_at = now + timedelta(seconds=duration_seconds)
    promoted_count = 0
    for li in listings:
        li.promo_mode = mode
        li.promo_started_at = now
        li.promo_expires_at = expires_at
        promoted_count += 1
    db.commit()

    return PromoteAllResult(
        shop_id=shop_id,
        promoted_count=promoted_count,
        skipped_ids=[],
        expires_at=expires_at,
    )
