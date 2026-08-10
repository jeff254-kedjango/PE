"""Trending demo seeder — keep a FIXED pool of boosted PRODUCTS alive for the dev rail.

LOCAL / DEMO ONLY. This process fabricates a small, FIXED set of synthetic products and keeps their
boosts alive so the §8 trending rail is populated in a development stack — instead of an operator
hand-driving a seed script. It must NEVER run in production: it is double-gated off
(``trending_demo_enabled`` defaults False AND ``run_forever`` hard-refuses when
``settings.is_production()``).

WHY A STANDALONE PROCESS (not Celery, not a FastAPI background task). It mirrors
``services/expiry_sweeper.py`` exactly — the codebase's established "lean sync background loop"
pattern: isolated from request serving (a seeder hiccup can never touch checkout), operated by
running one process, zero broker. It uses the REAL service functions (``catalog.create_shop`` /
``catalog.create_listing`` / ``boost.grant_boost``), never raw SQL, so the demo data flows through
the same validation + write paths a real seller would.

FIXED POOL — the whole point of this design (and the fix for the old one). ``pool_size`` demo
products are created ONCE and then merely kept boosted. This is NOT a rolling generation loop:
  * **Created once, reused forever.** Each pool slot ``i`` has a STABLE ``user_uuid``
    (``demo-trending-pool-{i}``). ``_ensure_pool`` is idempotent — on the first run it creates the
    shop+listing for each slot; on every subsequent run (and every restart) it finds them already
    present and creates NOTHING. The DB footprint is bounded at ~``pool_size`` rows, permanently.
    (The OLD design created a fresh seller/shop/listing every cycle and only ever revoked the BOOST,
    so live boosts stayed capped while the underlying rows grew without limit — the flood this
    rewrite removes.)
  * **Boosts topped up, not churned.** Re-granting the SAME (listing, tier, business-day) replays
    the existing grant (``boost.grant_boost`` is idempotent) — it spends no new allowance and writes
    no new row. So keeping ~50 boosts alive across days costs almost nothing.
  * **The visible cycling is CLIENT-side.** ``useTrendingRotation`` decays each rail slot and pulls
    the next queued product, so the backend only needs the pool's boosts to EXIST, not to rotate.
  * **Category per slot** is drawn deterministically across ``SHOP_CATEGORIES`` so the pool spans a
    mixed market (a real interleaved rail) rather than clustering one category.

HYGIENE (no leaks):
  * On shutdown (SIGTERM/SIGINT) it revokes the boosts it holds so the rail drains to baseline. The
    fixed pool's shops/listings are left in place (they are reused on the next run) — they are the
    bounded, intentional demo footprint, not a leak.

Run:
    python -m PE.commerce.services.trending_demo          # loop forever (the dev process)
    TRENDING_DEMO_ENABLED=true python -m ...               # enable (the dev launcher exports this)
"""
from __future__ import annotations

import logging
import signal
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from PE.commerce.core.categories import SHOP_CATEGORIES
from PE.commerce.core.config import settings
from PE.commerce.core.database import SessionLocal
from PE.commerce.models.boost import BOOST_MTAA, BoostGrant
from PE.commerce.models.listing import Listing
from PE.commerce.models.seller import Seller, Shop
from PE.commerce.schemas import catalog as schemas
from PE.commerce.services import boost, catalog, proximity

logger = logging.getLogger(__name__)

# A clearly-fake prefix on every synthetic seller's user_uuid, so demo data is trivially
# identifiable (self-heal / teardown target it without touching real sellers). Each fixed pool slot
# ``i`` gets the STABLE identity ``{DEMO_UUID_PREFIX}pool-{i}`` so re-runs reuse the same rows.
DEMO_UUID_PREFIX = "demo-trending-"
_POOL_UUID = DEMO_UUID_PREFIX + "pool-{i}"

# Human-ish product names per category so the rail reads like a market, not "P3-7". Kept inline
# (demo-only data; no need to bloat a shared module).
_NAMES: dict[str, tuple[str, ...]] = {
    "restaurant": ("Nyama Choma Platter", "Pilau Special", "Ugali & Fish", "Chicken Tikka"),
    "greengrocer": ("Sukuma Bunch", "Ripe Mangoes", "Avocado Crate", "Tomato Kilo"),
    "bakery": ("Sourdough Loaf", "Mandazi Dozen", "Birthday Cake", "Croissants x6"),
    "butchery": ("Goat Ribs 1kg", "Beef Fillet", "Boerewors", "Mutton Chops"),
    "electronics": ("Bluetooth Speaker", "Phone Charger", "LED Bulb 4pk", "Earbuds Pro"),
    "boutique": ("Linen Blazer", "Ankara Dress", "Denim Jacket", "Silk Scarf"),
    "shoes": ("Leather Loafers", "Running Trainers", "Leather Sandals", "School Shoes"),
    "pharmacy": ("Vitamin C 30s", "First-Aid Kit", "Hand Sanitizer", "Pain Relief"),
    "beauty": ("Shea Body Butter", "Matte Lipstick", "Argan Hair Oil", "Face Serum"),
    "hardware": ("Cordless Drill", "Paint 4L", "Tool Set 40pc", "Heavy Padlock"),
    "general": ("Storage Box", "Reusable Bags", "Phone Stand", "Notebook 3pk"),
}


def _slot_spec(i: int) -> tuple[str, str, str, float, float]:
    """Deterministic (no RNG) spec for pool slot ``i`` → (user_uuid, category, product_name, lat,
    lng). Category cycles through SHOP_CATEGORIES so the pool spans a mixed market; the product name
    is chosen from that category's ``_NAMES`` by a stable index; the position is a fixed offset
    within ``jitter_deg`` of the centre so distances vary but are reproducible across runs (stable
    identity ⇒ re-runs reuse the same rows, never duplicate them)."""
    category = SHOP_CATEGORIES[i % len(SHOP_CATEGORIES)]
    names = _NAMES[category]
    name = names[(i // len(SHOP_CATEGORIES)) % len(names)]
    jitter = settings.trending_demo_jitter_deg
    # Deterministic spiral-ish scatter from the index (no Math.random): two coprime strides across
    # the [-jitter, +jitter] box so successive slots don't stack on one point.
    span = max(1, settings.trending_demo_pool_size)
    lat = settings.trending_demo_center_lat + jitter * (((i * 7) % span) / span * 2 - 1)
    lng = settings.trending_demo_center_lng + jitter * (((i * 13) % span) / span * 2 - 1)
    return _POOL_UUID.format(i=i), category, name, lat, lng


def _ensure_pool(db: Session) -> list[str]:
    """Idempotently ensure the fixed pool of ``pool_size`` demo listings exists. Returns the list of
    pool ``(user_uuid)`` — actually returns each slot's listing id — for the caller to boost.

    Idempotent by STABLE identity: slot ``i`` owns seller ``demo-trending-pool-{i}``; if that seller
    already has a listing we reuse it and create NOTHING. Only a genuinely-missing slot is created
    (first run, or a manually-deleted row). This is what bounds the DB footprint at ~pool_size rows
    no matter how long or how many times the seeder runs — the core fix over the old per-cycle
    creation. Best-effort per slot: one failure is logged and skipped."""
    listing_ids: list[str] = []
    for i in range(settings.trending_demo_pool_size):
        user_uuid, category, name, lat, lng = _slot_spec(i)
        try:
            existing = (
                db.query(Listing.id)
                .join(Seller, Listing.seller_id == Seller.id)
                .filter(Seller.user_uuid == user_uuid)
                .first()
            )
            if existing is not None:
                listing_ids.append(existing[0])
                continue
            shop = catalog.create_shop(db, user_uuid, schemas.ShopCreate(
                name=f"{name.split()[0]} Shop", lat=lat, lng=lng,
                display_name=f"Demo Seller {i}", category=category,
            ))
            listing = catalog.create_listing(db, user_uuid, shop.id, schemas.ListingCreate(
                title=name, price_cents=5000 + (i * 1900) % 95000, stock_qty=99,
            ))
            if listing is not None:
                listing_ids.append(listing.id)
        except Exception:  # one bad slot must not abort building the rest of the pool
            logger.warning("demo seeder: failed to ensure pool slot %d", i, exc_info=True)
            db.rollback()
    return listing_ids


# Positions are compared to their target with this tolerance (degrees ≈ 1 cm) before a move: a row
# already at (or written to) its slot target reads back bit-identical, so this makes the relocate a
# clean no-op on every run after the first — never a churning re-write of rows that already sit right.
_POSITION_EPSILON_DEG = 1e-7


def _relocate_pool(db: Session) -> int:
    """One-time, idempotent IN-PLACE relocation of existing pool rows to their current ``_slot_spec``
    position (used to move the pool off the CBD default onto the Kilimani AOI centre without a
    delete). Returns how many slots were moved this pass.

    Why relocate in place and NOT delete+reseed: demo listings already carry live engagement
    (saves/comments/inquiries) and the project bans hard-deleting listings — a delete would orphan
    the immutable settlement §6/§7 history (there are no FK cascades). Moving the rows preserves all
    of that; only the coordinates change, and the listing ids never do.

    For each slot whose stored shop/listing position differs from the slot target by more than
    ``_POSITION_EPSILON_DEG``:
      * move BOTH the shop and its listing via ``proximity.set_location`` (the single writer that
        keeps lat/lng and the geography point in sync — the organic feed reads ``Listing.geog``), and
      * revoke the slot's live boost grant(s): the grant snapshots the target's location at grant
        time (the sponsored lane reads ``BoostGrant.center_geog``, not the live listing), and
        ``grant_boost`` is idempotent per (target, tier, day) so a plain re-grant would REPLAY the
        stale-location grant. Revoking lets the immediately-following ``_refresh_boosts`` re-grant
        with a fresh Kilimani snapshot. Each slot has its own seller (10 free mtaa chances/day), so
        the one re-grant is always within allowance.

    Best-effort + committed per slot (one bad slot can't abort the rest, and a crash mid-pass leaves
    already-moved slots durably relocated). Scoped strictly to the ``demo-trending-`` prefix — it can
    never touch a real seller. Idempotent: once every row sits on its target it moves nothing."""
    moved = 0
    for i in range(settings.trending_demo_pool_size):
        user_uuid, _category, _name, lat, lng = _slot_spec(i)
        try:
            row = (
                db.query(Shop, Listing)
                .join(Seller, Shop.seller_id == Seller.id)
                .join(Listing, Listing.shop_id == Shop.id)
                .filter(Seller.user_uuid == user_uuid)
                .first()
            )
            if row is None:
                continue  # slot not built yet (fresh run) — _ensure_pool seeds it at target already
            shop, listing = row
            if (
                abs(shop.lat - lat) <= _POSITION_EPSILON_DEG
                and abs(shop.lng - lng) <= _POSITION_EPSILON_DEG
                and abs(listing.lat - lat) <= _POSITION_EPSILON_DEG
                and abs(listing.lng - lng) <= _POSITION_EPSILON_DEG
            ):
                continue  # already on target — no-op (idempotent)
            proximity.set_location(shop, lat, lng)
            proximity.set_location(listing, lat, lng)
            # Drop stale-location grants so the following refresh re-snapshots at the new position.
            grant_ids = [
                g.id for g in db.query(BoostGrant.id)
                .filter(BoostGrant.target_type == "listing", BoostGrant.target_id == listing.id)
                .all()
            ]
            db.commit()  # persist the move before touching boosts (revoke_boost commits on its own)
            for grant_id in grant_ids:
                boost.revoke_boost(db, user_uuid, grant_id)
            moved += 1
        except Exception:  # one bad slot must not abort relocating the rest
            logger.warning("demo seeder: failed to relocate pool slot %d", i, exc_info=True)
            db.rollback()
    return moved


# Re-grant a pool boost when it has less than this fraction of its duration left (or is gone), so a
# boost never lapses between refresh ticks. Comfortably covers refresh_seconds « boost duration.
_REFRESH_MARGIN = timedelta(hours=1)


def _refresh_boosts(db: Session, listing_ids: list[str]) -> int:
    """Ensure every pool listing has a live mtaa boost, re-granting any that is missing or within
    ``_REFRESH_MARGIN`` of expiry. Returns how many grants were (re)issued this pass.

    Cheap by construction: re-granting the SAME (listing, tier, business-day) REPLAYS the existing
    grant (``boost.grant_boost`` is idempotent) — no new allowance spent, no new row — so a healthy
    pool re-grants ~nothing until the day rolls over or a boost nears expiry. Each pool slot has its
    OWN seller, so the per-seller daily allowance is never the constraint."""
    now = datetime.now(timezone.utc)
    issued = 0
    for i, listing_id in enumerate(listing_ids):
        user_uuid = _POOL_UUID.format(i=i)
        try:
            live = (
                db.query(BoostGrant)
                .filter(
                    BoostGrant.target_type == "listing",
                    BoostGrant.target_id == listing_id,
                    BoostGrant.expires_at > now + _REFRESH_MARGIN,
                )
                .first()
            )
            if live is not None:
                continue
            grant = boost.grant_boost(
                db, user_uuid, target_type="listing", target_id=listing_id, tier=BOOST_MTAA,
            )
            if grant is not None:
                issued += 1
        except boost.QuotaExceeded:
            # The slot's own daily allowance is spent (only happens if a day churns many re-grants);
            # its existing grant still stands, so this is harmless — skip until tomorrow's reset.
            db.rollback()
        except Exception:  # a single slot's failure must not stall the pool
            logger.warning("demo seeder: failed to refresh boost for slot %d", i, exc_info=True)
            db.rollback()
    return issued


def _revoke_pool(db: Session) -> int:
    """Revoke all live demo-pool boosts (shutdown teardown) so the rail drains to baseline. Targets
    ONLY the pool sellers by prefix — never a real seller. The pool's shops/listings are left in
    place (reused next run). One bounded query; best-effort per grant."""
    rows = (
        db.query(Seller.user_uuid, BoostGrant.id)
        .join(BoostGrant, BoostGrant.seller_id == Seller.id)
        .filter(Seller.user_uuid.like(f"{DEMO_UUID_PREFIX}%"))
        .all()
    )
    revoked = 0
    for user_uuid, grant_id in rows:
        try:
            if boost.revoke_boost(db, user_uuid, grant_id):
                revoked += 1
        except Exception:  # pragma: no cover - teardown is best-effort
            logger.warning("demo seeder: failed to revoke grant %s", grant_id, exc_info=True)
            db.rollback()
    return revoked


def run_forever(stop: threading.Event | None = None) -> None:
    """Keep the fixed demo pool's boosts alive until SIGTERM/SIGINT (or ``stop`` is set).

    DOUBLE prod-gate: refuses to start under production, and idles (no-op) when the feature flag is
    off — so an accidental launch in the wrong environment fabricates nothing. On a normal dev run
    it ensures the fixed pool exists (created once, reused thereafter), grants their boosts so the
    rail is populated immediately, then every ``trending_demo_refresh_seconds`` tops up any boost
    that is missing or near expiry. It creates NO new rows per tick. On shutdown it revokes the
    pool's boosts (leaving the reusable pool rows in place)."""
    if settings.is_production():
        # Hard stop — the demo seeder fabricates data and must never run in production.
        raise RuntimeError(
            "trending_demo refuses to run in production (COMMERCE_ENV=production): it fabricates "
            "synthetic sellers/listings/boosts. This is a local/demo helper only."
        )

    stop = stop or threading.Event()

    def _handle(signum, _frame):
        logger.info("trending demo seeder: received signal %s, shutting down", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    if not settings.trending_demo_enabled:
        logger.warning(
            "trending demo seeder started but TRENDING_DEMO_ENABLED is false — idling as a no-op"
        )
        stop.wait()
        return

    refresh = settings.trending_demo_refresh_seconds
    logger.info(
        "trending demo seeder started (fixed pool=%d products, refresh every %ds, centre %.4f,%.4f)",
        settings.trending_demo_pool_size, refresh,
        settings.trending_demo_center_lat, settings.trending_demo_center_lng,
    )

    # The pool is created once and reused across ticks/restarts; hold the listing ids so each tick
    # only tops up boosts (no re-query of the pool unless it needs rebuilding).
    listing_ids: list[str] = []

    # Initial: ensure the pool exists, relocate any existing rows to the current slot centre, then
    # grant boosts so the rail is populated at once. The relocate MUST precede the boost refresh so a
    # re-granted boost snapshots the NEW position (see _relocate_pool). It runs only here, not in the
    # steady loop: rows never move again once on target, so it's a no-op on every subsequent boot.
    db = SessionLocal()
    try:
        listing_ids = _ensure_pool(db)
        relocated = _relocate_pool(db)
        if relocated:
            logger.info("trending demo seeder: relocated %d pool slot(s) to the current centre "
                        "(%.4f,%.4f)", relocated,
                        settings.trending_demo_center_lat, settings.trending_demo_center_lng)
        issued = _refresh_boosts(db, listing_ids)
        logger.info("trending demo seeder: pool ready (%d products, %d boosts issued)",
                    len(listing_ids), issued)
    except Exception:
        logger.exception("trending demo seeder: initial pool build failed")
        db.rollback()
    finally:
        db.close()

    try:
        # Steady loop: each tick, re-ensure the pool (cheap — a no-op once built) and top up boosts.
        # stop.wait makes a shutdown signal interrupt the sleep immediately.
        while not stop.wait(refresh):
            db = SessionLocal()
            try:
                listing_ids = _ensure_pool(db)  # heals a manually-deleted slot; else a no-op
                issued = _refresh_boosts(db, listing_ids)
                if issued:
                    logger.info("trending demo seeder: refreshed %d boost(s)", issued)
            except Exception:
                logger.exception("trending demo seeder: refresh tick failed; will retry next cycle")
                db.rollback()
            finally:
                db.close()
    finally:
        # Teardown: revoke the pool's boosts so the rail drains to baseline. The pool rows stay.
        db = SessionLocal()
        try:
            n = _revoke_pool(db)
            logger.info("trending demo seeder: stopped — revoked %d outstanding pool boost(s)", n)
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
