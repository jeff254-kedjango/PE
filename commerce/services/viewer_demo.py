"""Live-viewer demo seeder — keep a rotating population of LIVE viewers on every real shop.

LOCAL / DEMO ONLY. This process fabricates shop-view heartbeats so the seller console's Viewing
Card (§8 Chunk C+) has something to show in a development stack. It must NEVER run in production:
it is double-gated off (``viewer_demo_enabled`` defaults False AND ``run_forever`` hard-refuses
when ``settings.is_production()``), mirroring ``services/trending_demo.py``.

WHY A LOOPING PROCESS AND NOT A SEED SCRIPT. ``shop_views.LIVE_WINDOW_SECONDS`` is 60: a viewer is
"live" only while its last heartbeat is inside that window. A one-shot script would populate the
card for under a minute and then leave it empty — the surface would look broken. So this re-sends
heartbeats every ``viewer_demo_refresh_seconds`` (default 20s, comfortably under the window so the
population never flickers), exactly as a real browser does at 30s.

WHY REAL WEESPAS UUIDS, NOT FABRICATED ONES. Commerce stores only an opaque ``viewer_uuid``; the
Viewing Card resolves names and avatars through the S2S bridge to weespas
(``services/weespas_client``). A made-up uuid resolves to nothing, so every row would render as
"Guest" — and seeding viewers whose whole purpose is to show faces and names would be pointless.
The identity pool is therefore drawn from ``sellers.user_uuid``, which ARE real weespas user ids
(verified: all 9 non-demo seller uuids exist in the weespas users table).

This deliberately does NOT open a second database connection. Commerce and weespas have separate
databases (``commerce`` / ``commercial``); a seeder reaching into another service's schema would
couple them in a way nothing else in the codebase does, and would need that service's credentials.
Reusing uuids already present in commerce's own rows keeps the process single-DB.

ROTATION. Each tick, ``churn_per_tick`` shifts which identity occupies each shop's viewer slots,
so the card demonstrates arrivals and departures instead of a frozen list. Rotation is
DETERMINISTIC (a tick counter, no RNG) so a given tick is reproducible when debugging.

WHY THE SESSION ID ENCODES THE IDENTITY. ``viewer_uuid`` is STICKY in ``record_heartbeat``: it is
captured on the first heartbeat of a session and deliberately never overwritten (a signed-in reload
of an anonymous session must stay anonymous). Verified empirically — re-heartbeating a session with
a different uuid leaves the original in place. So a session id that is stable per (shop, slot)
CANNOT rotate occupants: the slot would keep its first face forever and ``churn_per_tick`` would be
a silent no-op. The identity index is therefore part of the session id, which makes "a different
person arrived in this slot" a genuinely different session — exactly what it is in real traffic.

HYGIENE (no leaks):
  * Bounded footprint. Rows are keyed ``(shop_id, session_id)`` and session ids are a pure function
    of (shop, slot, identity) — ``record_heartbeat`` upserts, so once rotation has cycled through
    the pool every subsequent tick REUSES rows. Total rows are capped at
    ``shops x viewers_per_shop x pool_size`` however long the process runs, and the LIVE subset is
    capped at ``shops x viewers_per_shop``.
  * Each tick backdates the demo rows it is not currently occupying, in ONE bulk UPDATE. Without
    that, a rotated-away session would stay inside the 60s live window and the card's viewer count
    would climb every tick instead of holding steady.
  * On shutdown it EXPIRES its rows (backdates them outside the live window) so the card drains
    to empty instead of showing viewers that will never move again. The rows are left in place —
    they are the same bounded set the next run reuses, and they keep the History tab populated.

Run:
    python -m PE.commerce.services.viewer_demo             # loop forever (the dev process)
    VIEWER_DEMO_ENABLED=true python -m ...                 # enable (the dev launcher exports this)
"""
from __future__ import annotations

import logging
import signal
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from PE.commerce.core.config import settings
from PE.commerce.core.database import SessionLocal
from PE.commerce.models.listing import Listing
from PE.commerce.models.seller import Seller, Shop
from PE.commerce.models.shop_view import ShopViewEvent
from PE.commerce.services import shop_views

logger = logging.getLogger(__name__)

# Every session id this seeder writes starts with this, so its rows are trivially identifiable for
# teardown/inspection and can never collide with a real browser session id (a uuid4 hex).
DEMO_SESSION_PREFIX = "demo-viewer-"

# The trending seeder's synthetic sellers. Their shops are excluded: they exist only to populate
# the trending rail, nobody logs in as them, and putting viewers on 50 fake shops would bury the
# real ones in the History tab.
_TRENDING_PREFIX = "demo-trending-"

# Session ids are capped at 64 chars by the column and by record_heartbeat.
_MAX_SESSION_ID_LEN = 64


def _session_id(shop_id: str, slot: int, ident_index: int) -> str:
    """Session id for (shop, slot, identity) — a pure function of its inputs, no RNG.

    ``ident_index`` is in the key because ``viewer_uuid`` is sticky (set on insert, never
    overwritten), so reusing one session id per slot would freeze that slot's face and make
    ``churn_per_tick`` do nothing. Including the identity makes each new occupant a distinct
    session — which is what it is in reality — while staying deterministic, so the same
    (shop, slot, identity) always upserts the same row rather than leaking a new one.

    Truncation cuts the SHOP id from the LEFT: the ``-{slot}-{ident}`` suffix is what carries
    uniqueness, and two slots collapsing to one id would silently halve the population via upsert.
    """
    suffix = f"-{slot}-{ident_index}"
    sid = DEMO_SESSION_PREFIX + shop_id + suffix
    if len(sid) > _MAX_SESSION_ID_LEN:
        keep = _MAX_SESSION_ID_LEN - len(DEMO_SESSION_PREFIX) - len(suffix)
        sid = DEMO_SESSION_PREFIX + shop_id[-keep:] + suffix
    return sid


def _real_shops(db: Session) -> list[Shop]:
    """Active shops belonging to REAL sellers, oldest first for a stable order across runs.

    Excludes the trending seeder's synthetic pool. Ordering by id (indexed PK) keeps slot->viewer
    assignment reproducible between ticks and restarts.
    """
    return (
        db.query(Shop)
        .join(Seller, Seller.id == Shop.seller_id)
        .filter(~Seller.user_uuid.like(_TRENDING_PREFIX + "%"))
        .order_by(Shop.id)
        .all()
    )


def _identity_pool(db: Session) -> list[str]:
    """Real weespas user uuids to use as viewers, in a stable order.

    Drawn from ``sellers.user_uuid`` because those are genuine weespas ids that the S2S bridge can
    resolve to a name and avatar. Filters out the trending seeder's fake uuids (which look like
    ``demo-trending-pool-7`` and would resolve to nothing).
    """
    rows = (
        db.query(Seller.user_uuid)
        .filter(~Seller.user_uuid.like(_TRENDING_PREFIX + "%"))
        .order_by(Seller.user_uuid)
        .all()
    )
    return [r[0] for r in rows if r[0]]


def _listings_by_shop(db: Session, shop_ids: list[str]) -> dict[str, str]:
    """{shop_id: one active listing id} for every given shop, in ONE query.

    Some viewers must read as "viewing <product>" rather than just "browsing storefront" so the
    card exercises both row shapes. Fetched as a single grouped query rather than one SELECT per
    shop: a per-shop lookup would be N+1 round trips every tick, growing with the shop count.
    MIN(id) picks a stable listing per shop, so a given tick stays reproducible.
    """
    if not shop_ids:
        return {}
    rows = (
        db.query(Listing.shop_id, func.min(Listing.id))
        .filter(Listing.shop_id.in_(shop_ids), Listing.is_active.is_(True))
        .group_by(Listing.shop_id)
        .all()
    )
    return {shop_id: listing_id for shop_id, listing_id in rows if listing_id}


def seed_tick(db: Session, *, tick: int, now: datetime) -> int:
    """One pass: refresh every (shop, slot) viewer heartbeat. Returns rows written.

    Deterministic assignment — no RNG, so a given ``tick`` reproduces exactly:
      * identity for (shop, slot) advances by ``churn_per_tick`` each tick, so a slot's occupant
        changes over time and the card shows arrivals/departures;
      * every OTHER slot keeps its occupant within a tick, so the card isn't a full reshuffle.

    Writes go through ``shop_views.record_heartbeat`` — the same service function the real
    heartbeat endpoint uses — so seeded rows are validated and shaped identically to real traffic.
    Never raw SQL.
    """
    shops = _real_shops(db)
    pool = _identity_pool(db)
    if not shops or not pool:
        logger.warning(
            "viewer demo seeder: nothing to seed (shops=%d, identities=%d)", len(shops), len(pool)
        )
        return 0

    per_shop = max(0, settings.viewer_demo_viewers_per_shop)
    churn = max(0, settings.viewer_demo_churn_per_tick)
    listings = _listings_by_shop(db, [s.id for s in shops])
    # The sessions this tick wants LIVE. Everything else of ours gets backdated below, so the
    # rotated-away occupants leave the card instead of accumulating inside the 60s window.
    live_now: set[str] = set()
    written = 0

    for shop_index, shop in enumerate(shops):
        listing_id = listings.get(shop.id)
        for slot in range(per_shop):
            # Offsetting by shop_index stops every shop from showing the SAME faces in the same
            # order, which would look obviously synthetic. The churn term rotates occupants.
            ident_index = (shop_index + slot + tick * churn) % len(pool)
            session_id = _session_id(shop.id, slot, ident_index)
            live_now.add(session_id)
            # Alternate which slots are "viewing a product" so the card exercises both row shapes
            # (product line vs plain storefront browse) at once.
            viewing = listing_id if (slot + tick) % 2 == 0 else None
            try:
                shop_views.record_heartbeat(
                    db,
                    shop_id=shop.id,
                    session_id=session_id,
                    viewer_uuid=pool[ident_index],
                    now=now,
                    viewing_listing_id=viewing,
                    # Coords near the shop so the card's neighbourhood label resolves to something
                    # sensible instead of blank. Small fixed offsets per slot — deterministic, and
                    # inside the same reverse-geocode bounding box as the shop itself.
                    last_lat=(shop.lat + 0.0004 * slot) if shop.lat is not None else None,
                    last_lng=(shop.lng + 0.0004 * slot) if shop.lng is not None else None,
                )
                written += 1
            except shop_views.HeartbeatError:
                # A single bad slot must not abort the tick. Logged at warning because with
                # deterministic inputs this should be unreachable.
                logger.warning(
                    "viewer demo seeder: heartbeat rejected for shop=%s slot=%d", shop.id, slot
                )

    # Retire the occupants rotation moved off. One bulk UPDATE, and only over OUR rows (the prefix
    # filter) — a real browser's session is never touched. Without this the live count would grow
    # by `churn` every tick, because a rotated-away session's last heartbeat is still recent.
    _expire_demo_rows_except(db, keep=live_now, now=now)
    db.commit()
    return written


def _expire_demo_rows_except(db: Session, *, keep: set[str], now: datetime) -> int:
    """Backdate this seeder's rows outside the live window, except the sessions in ``keep``.

    Rows are kept, not deleted: they are the same bounded set the next run reuses, and they keep
    the History tab populated (which is where a stopped demo's data is still useful). Deleting
    them would also be needless write amplification every tick and every restart.

    Scoped to ``DEMO_SESSION_PREFIX`` so a REAL browser's row can never be backdated by the demo.
    One bulk UPDATE — no per-row round trips.
    """
    cutoff = now - timedelta(seconds=shop_views.LIVE_WINDOW_SECONDS * 2)
    q = db.query(ShopViewEvent).filter(
        ShopViewEvent.session_id.like(DEMO_SESSION_PREFIX + "%"),
        ShopViewEvent.last_heartbeat_at > cutoff,
    )
    if keep:
        q = q.filter(ShopViewEvent.session_id.notin_(keep))
    return q.update(
        {ShopViewEvent.last_heartbeat_at: cutoff, ShopViewEvent.viewing_listing_id: None},
        synchronize_session=False,
    )


def expire_demo_viewers(db: Session, *, now: datetime) -> int:
    """Drain the card completely — every demo row goes outside the live window. Shutdown path."""
    return _expire_demo_rows_except(db, keep=set(), now=now)


def run_forever(stop: threading.Event | None = None) -> None:
    """Keep a live viewer population on every real shop until SIGTERM/SIGINT (or ``stop`` is set).

    DOUBLE prod-gate: refuses to start under production, and idles as a no-op when the feature flag
    is off — so an accidental launch in the wrong environment fabricates nothing.
    """
    if settings.is_production():
        # Hard stop — this fabricates viewer traffic and must never run in production.
        raise RuntimeError(
            "viewer_demo refuses to run in production (COMMERCE_ENV=production): it fabricates "
            "synthetic shop-view heartbeats. This is a local/demo helper only."
        )

    stop = stop or threading.Event()

    def _handle(signum, _frame):
        logger.info("viewer demo seeder: received signal %s, shutting down", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    if not settings.viewer_demo_enabled:
        logger.warning(
            "viewer demo seeder started but VIEWER_DEMO_ENABLED is false — idling as a no-op"
        )
        stop.wait()
        return

    refresh = settings.viewer_demo_refresh_seconds
    if refresh >= shop_views.LIVE_WINDOW_SECONDS:
        # Not fatal, but the card would visibly empty between ticks, which looks like a bug in the
        # feature rather than a misconfigured seeder. Say so loudly.
        logger.warning(
            "viewer demo seeder: refresh %ds >= live window %ds — the viewer list WILL flicker "
            "empty between ticks; lower VIEWER_DEMO_REFRESH_SECONDS",
            refresh, shop_views.LIVE_WINDOW_SECONDS,
        )

    logger.info(
        "viewer demo seeder started (%d viewers/shop, churn %d/tick, refresh every %ds)",
        settings.viewer_demo_viewers_per_shop, settings.viewer_demo_churn_per_tick, refresh,
    )

    tick = 0
    try:
        # Seed immediately, then on every refresh interval. stop.wait makes a shutdown signal
        # interrupt the sleep at once instead of waiting out the interval.
        while True:
            db = SessionLocal()
            try:
                written = seed_tick(db, tick=tick, now=datetime.now(timezone.utc))
                logger.info("viewer demo seeder: tick %d — %d heartbeat(s)", tick, written)
            except Exception:
                logger.exception("viewer demo seeder: tick failed; will retry next cycle")
                db.rollback()
            finally:
                db.close()
            tick += 1
            if stop.wait(refresh):
                break
    finally:
        # Teardown: expire our rows so the Viewing Card drains to empty rather than showing a
        # frozen population that no longer moves.
        db = SessionLocal()
        try:
            n = expire_demo_viewers(db, now=datetime.now(timezone.utc))
            db.commit()
            logger.info("viewer demo seeder: stopped — expired %d demo viewer row(s)", n)
        except Exception:
            logger.exception("viewer demo seeder: teardown failed")
            db.rollback()
        finally:
            db.close()


def main() -> None:  # pragma: no cover - process entrypoint
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
