"""trending_demo seeder — the LOCAL/DEMO fixed-pool product seeder for the §8 rail.

These tests exercise the seeder's service-level helpers directly against the SQLite session (no
process loop, no network). The contract they lock is the FIXED POOL: the pool is created ONCE and
reused (a second ensure creates no new rows), boosts are topped up in place (re-refresh issues
nothing while grants are live), the pool spans multiple categories, and the created products surface
on the trending slate. We also assert the production safety gate hard-refuses and the disabled flag
idles as a no-op.

The key regression this guards is the old leak: the seeder must NOT create a new seller/shop/listing
on every tick — ``_ensure_pool`` is idempotent, so repeated calls hold the row count flat.
"""
import threading

import pytest

from PE.commerce.core.categories import SHOP_CATEGORIES
from PE.commerce.models.boost import BoostGrant
from PE.commerce.models.listing import Listing
from PE.commerce.models.seller import Seller, Shop
from PE.commerce.services import trending, trending_demo


def test_ensure_pool_creates_fixed_pool_via_real_services(db_session):
    """The first ensure stands up ``pool_size`` shops + listings, each under its own stable demo
    seller (``demo-trending-pool-{i}``), via the real catalog services."""
    ids = trending_demo._ensure_pool(db_session)
    assert len(ids) == trending_demo.settings.trending_demo_pool_size

    sellers = db_session.query(Seller).all()
    assert len(sellers) == trending_demo.settings.trending_demo_pool_size
    assert all(s.user_uuid.startswith(trending_demo.DEMO_UUID_PREFIX + "pool-") for s in sellers)
    # One listing per pool slot.
    assert db_session.query(Listing).count() == trending_demo.settings.trending_demo_pool_size


def test_ensure_pool_is_idempotent_no_new_rows_on_rerun(db_session):
    """The core anti-flood invariant: a SECOND ensure creates nothing — same rows reused. This is
    what bounds the DB footprint no matter how long/often the seeder runs."""
    first = trending_demo._ensure_pool(db_session)
    sellers_after_first = db_session.query(Seller).count()
    listings_after_first = db_session.query(Listing).count()

    second = trending_demo._ensure_pool(db_session)
    assert second == first  # identical listing ids returned
    assert db_session.query(Seller).count() == sellers_after_first    # no new sellers
    assert db_session.query(Listing).count() == listings_after_first  # no new listings


def test_pool_spans_multiple_categories(db_session):
    """Category is assigned per slot by cycling SHOP_CATEGORIES, so the pool spans a mixed market
    (a real interleaved rail) rather than clustering one category."""
    trending_demo._ensure_pool(db_session)
    cats = {c for (c,) in db_session.query(Shop.category).all() if c is not None}
    assert len(cats) > 1
    assert cats.issubset(set(SHOP_CATEGORIES))


def test_refresh_boosts_grants_then_is_a_noop_while_live(db_session):
    """First refresh grants a live mtaa boost to every pool listing and they surface on the slate;
    an immediate second refresh issues NOTHING (grants are still live) — boosts are topped up in
    place, never duplicated."""
    ids = trending_demo._ensure_pool(db_session)
    issued = trending_demo._refresh_boosts(db_session, ids)
    assert issued == len(ids)
    assert db_session.query(BoostGrant).count() == len(ids)

    # Slate near the demo centre carries the pool as product cards.
    slate = trending.build_slate(
        db_session, trending_demo.settings.trending_demo_center_lat,
        trending_demo.settings.trending_demo_center_lng,
    )
    assert slate.active_count == len(ids)

    # Re-refresh while every grant is live: no new grants issued, count unchanged.
    again = trending_demo._refresh_boosts(db_session, ids)
    assert again == 0
    assert db_session.query(BoostGrant).count() == len(ids)


def test_revoke_pool_clears_boosts_but_keeps_pool_rows(db_session):
    """Teardown revokes the pool's boosts (rail drains) but leaves the reusable shops/listings in
    place, so the next run reuses them."""
    ids = trending_demo._ensure_pool(db_session)
    trending_demo._refresh_boosts(db_session, ids)
    assert db_session.query(BoostGrant).count() == len(ids)

    revoked = trending_demo._revoke_pool(db_session)
    assert revoked == len(ids)
    assert db_session.query(BoostGrant).count() == 0
    # Pool rows survive for reuse.
    assert db_session.query(Listing).count() == len(ids)
    assert db_session.query(Seller).count() == len(ids)


def test_relocate_moves_rows_to_slot_target_and_is_idempotent(db_session):
    """A pool built at a DIFFERENT centre is relocated in place onto the current slot targets, then
    is a no-op on a second pass. Listing ids are preserved (no delete) so engagement/ledger history
    survives — the whole point of relocate-in-place over delete+reseed."""
    # Build the pool at an old centre, then move the config centre and relocate.
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(trending_demo.settings, "trending_demo_center_lat", -1.2921)  # old CBD
    monkeypatch.setattr(trending_demo.settings, "trending_demo_center_lng", 36.8219)
    ids_before = trending_demo._ensure_pool(db_session)
    monkeypatch.setattr(trending_demo.settings, "trending_demo_center_lat", -1.2900)  # Kilimani
    monkeypatch.setattr(trending_demo.settings, "trending_demo_center_lng", 36.7870)

    moved = trending_demo._relocate_pool(db_session)
    assert moved == len(ids_before)  # every slot was off-target and got moved
    # Listing ids unchanged — rows were MOVED, not recreated (history preserved).
    assert set(trending_demo._ensure_pool(db_session)) == set(ids_before)

    # Every shop+listing now sits on its slot target (Kilimani), lat/lng and geog in sync.
    for i in range(trending_demo.settings.trending_demo_pool_size):
        uuid, _c, _n, lat, lng = trending_demo._slot_spec(i)
        shop, listing = (
            db_session.query(Shop, Listing)
            .join(Seller, Shop.seller_id == Seller.id)
            .join(Listing, Listing.shop_id == Shop.id)
            .filter(Seller.user_uuid == uuid).one()
        )
        assert abs(shop.lat - lat) < 1e-9 and abs(shop.lng - lng) < 1e-9
        assert abs(listing.lat - lat) < 1e-9 and abs(listing.lng - lng) < 1e-9

    # Idempotent: a second relocate (already on target) moves nothing.
    assert trending_demo._relocate_pool(db_session) == 0
    monkeypatch.undo()


def test_relocate_revokes_stale_boost_so_refresh_resnapshots(db_session):
    """Relocating a slot with a live boost drops that grant (its location snapshot is now stale), so
    the following refresh re-grants at the new position — the sponsored lane reads the grant's own
    centre, not the live listing."""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(trending_demo.settings, "trending_demo_center_lat", -1.2921)
    monkeypatch.setattr(trending_demo.settings, "trending_demo_center_lng", 36.8219)
    ids = trending_demo._ensure_pool(db_session)
    trending_demo._refresh_boosts(db_session, ids)
    assert db_session.query(BoostGrant).count() == len(ids)

    monkeypatch.setattr(trending_demo.settings, "trending_demo_center_lat", -1.2900)
    monkeypatch.setattr(trending_demo.settings, "trending_demo_center_lng", 36.7870)
    trending_demo._relocate_pool(db_session)
    # Stale grants dropped by the relocate.
    assert db_session.query(BoostGrant).count() == 0

    # Refresh re-grants at the new centre; the grant's own snapshot now sits in Kilimani.
    reissued = trending_demo._refresh_boosts(db_session, ids)
    assert reissued == len(ids)
    for g in db_session.query(BoostGrant).all():
        assert abs(g.center_lat - (-1.2900)) < trending_demo.settings.trending_demo_jitter_deg + 1e-9
        assert abs(g.center_lng - 36.7870) < trending_demo.settings.trending_demo_jitter_deg + 1e-9
    monkeypatch.undo()


def test_relocate_is_noop_on_fresh_pool_at_current_centre(db_session):
    """A pool freshly built at the CURRENT centre needs no move — relocate is a clean no-op (the
    every-boot-after-first case)."""
    trending_demo._ensure_pool(db_session)
    assert trending_demo._relocate_pool(db_session) == 0


def test_run_forever_refuses_in_production(monkeypatch):
    """The seeder fabricates data — it must hard-refuse under production regardless of the flag."""
    monkeypatch.setattr(trending_demo.settings, "commerce_env", "production")
    with pytest.raises(RuntimeError, match="production"):
        trending_demo.run_forever(threading.Event())


def test_run_forever_noop_when_disabled(monkeypatch):
    """Disabled (the default) → idle no-op that returns once the stop event is set, fabricating
    nothing."""
    monkeypatch.setattr(trending_demo.settings, "commerce_env", "development")
    monkeypatch.setattr(trending_demo.settings, "trending_demo_enabled", False)
    ev = threading.Event()
    ev.set()  # pre-set so the idle wait returns immediately
    trending_demo.run_forever(ev)  # must return without raising
