"""Live-viewer demo seeder — services/viewer_demo.py.

A dev-only seeder, but it WRITES to a real table through the real service function, so the
properties that keep it from becoming a liability are worth pinning:

  1. **Double prod-gate.** Refuses to start under production; idles as a no-op when the flag is
     off. This is the whole reason a fabricating process is safe to keep in the tree.
  2. **Rotation actually rotates.** ``record_heartbeat`` captures ``viewer_uuid`` on INSERT and
     never overwrites it (verified against the service). So the identity index has to be part of
     the session id — otherwise a slot keeps its first face forever and ``churn_per_tick`` is a
     silent no-op. That regression is invisible by inspection, hence tested directly.
  3. **Bounded footprint, steady live count.** Session ids are a pure function of
     (shop, slot, identity), so ticks UPSERT once rotation wraps. And each tick backdates the
     occupants it rotated away — without that the LIVE count climbs every tick even though the
     row count is bounded.
  4. **Real identities only.** Viewer uuids must be resolvable weespas ids, never the trending
     seeder's fake ``demo-trending-pool-N`` ones, or every card row renders as 'Guest'.
  5. **Only real shops.** The 50 synthetic trending shops must not get viewers.
  6. **Teardown drains the card**, and never touches a real browser's row.
"""
import threading
from datetime import datetime, timedelta, timezone

import pytest

from PE.commerce.models.listing import Listing
from PE.commerce.models.seller import Seller, Shop
from PE.commerce.models.shop_view import ShopViewEvent
from PE.commerce.services import shop_views, viewer_demo

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)

# Real-looking weespas uuids (the seeder's identity pool comes from sellers.user_uuid).
_UUID_A = "af38c880-5c1a-439a-a97a-bc07931079a8"
_UUID_B = "34a479ce-f397-4f15-bc7f-32b400c4863f"
_UUID_C = "7b48f1f9-25b3-4cd3-a4c8-59a5c515e67f"


def _seller(db, user_uuid, name="S"):
    s = Seller(user_uuid=user_uuid, display_name=name)
    db.add(s)
    db.flush()
    return s


def _shop(db, seller, name="Shop", lat=-1.29, lng=36.78):
    sh = Shop(seller_id=seller.id, name=name, category="general", lat=lat, lng=lng)
    db.add(sh)
    db.flush()
    return sh


def _listing(db, shop, seller, title="Thing"):
    li = Listing(
        shop_id=shop.id, seller_id=seller.id, title=title, price_cents=10_000,
        currency="KES", pricing_mode="fixed", stock_qty=5, is_active=True,
        lat=shop.lat, lng=shop.lng,
    )
    db.add(li)
    db.flush()
    return li


@pytest.fixture
def seeded(db_session):
    """Two real shops (one seller each) + the trending seeder's synthetic shop, which must be
    ignored by everything below."""
    a = _seller(db_session, _UUID_A, "Alpha")
    b = _seller(db_session, _UUID_B, "Beta")
    _seller(db_session, _UUID_C, "Gamma")                # identity pool only, owns no shop
    fake = _seller(db_session, "demo-trending-pool-7", "Demo Seller 7")

    shop_a = _shop(db_session, a, "Alpha Shop")
    shop_b = _shop(db_session, b, "Beta Shop")
    fake_shop = _shop(db_session, fake, "Demo Shop")
    _listing(db_session, shop_a, a)
    db_session.commit()
    return db_session, shop_a, shop_b, fake_shop


@pytest.fixture
def cfg(monkeypatch):
    """Set the seeder's knobs without mutating the process-wide settings singleton past the test."""
    def _set(*, per_shop=None, churn=None, env=None, enabled=None):
        s = viewer_demo.settings
        if per_shop is not None:
            monkeypatch.setattr(s, "viewer_demo_viewers_per_shop", per_shop)
        if churn is not None:
            monkeypatch.setattr(s, "viewer_demo_churn_per_tick", churn)
        if env is not None:
            monkeypatch.setattr(s, "commerce_env", env)
        if enabled is not None:
            monkeypatch.setattr(s, "viewer_demo_enabled", enabled)
    return _set


class TestSelection:
    def test_excludes_trending_seeder_shops(self, seeded):
        """The 50 synthetic trending shops exist only to populate the rail — nobody logs in as
        them, and seeding viewers there would bury the real shops in the History tab."""
        db, shop_a, shop_b, fake_shop = seeded
        ids = {s.id for s in viewer_demo._real_shops(db)}
        assert ids == {shop_a.id, shop_b.id}
        assert fake_shop.id not in ids

    def test_identity_pool_excludes_fabricated_uuids(self, seeded):
        """`demo-trending-pool-7` is not a weespas user, so the S2S bridge cannot resolve it and
        the card would show 'Guest'. Seeding faces is the entire point, so these must be filtered."""
        db, *_ = seeded
        pool = viewer_demo._identity_pool(db)
        assert set(pool) == {_UUID_A, _UUID_B, _UUID_C}
        assert not any(u.startswith("demo-trending-") for u in pool)

    def test_listing_lookup_is_one_query_for_all_shops(self, seeded):
        """Guards the N+1: the per-shop lookup this replaced cost one round trip per shop, every
        tick, forever. Also pins the shape — shops with no active listing are simply absent."""
        db, shop_a, shop_b, _ = seeded
        got = viewer_demo._listings_by_shop(db, [shop_a.id, shop_b.id])
        assert set(got) == {shop_a.id}, "Beta Shop has no listing, so it must not appear"
        assert viewer_demo._listings_by_shop(db, []) == {}

    def test_inactive_listings_are_not_offered_as_viewed_products(self, db_session):
        """A deactivated listing must never show as 'viewing X' — the seller deliberately took it
        down, so surfacing it would misrepresent the storefront."""
        s = _seller(db_session, _UUID_A)
        sh = _shop(db_session, s)
        li = _listing(db_session, sh, s)
        li.is_active = False
        db_session.commit()
        assert viewer_demo._listings_by_shop(db_session, [sh.id]) == {}


class TestSeedTick:
    def test_seeds_viewers_that_count_as_live(self, seeded, cfg):
        db, shop_a, shop_b, _ = seeded
        cfg(per_shop=3, churn=1)

        written = viewer_demo.seed_tick(db, tick=0, now=NOW)
        assert written == 6                                     # 2 shops x 3 slots
        # The service's own liveness predicate is the one that matters — not a row count.
        assert shop_views.count_live_viewers(db, shop_id=shop_a.id, now=NOW) == 3
        assert shop_views.count_live_viewers(db, shop_id=shop_b.id, now=NOW) == 3

    def test_live_count_stays_flat_across_many_ticks(self, seeded, cfg):
        """The leak guard that matters to the UI. Rotation creates a new session per occupant, so
        without per-tick retirement the card's count would climb every tick while the row count
        still looked bounded. Asserts the LIVE count, at the same instant, tick after tick."""
        db, shop_a, *_ = seeded
        cfg(per_shop=2, churn=1)

        for tick in range(8):
            now = NOW + timedelta(seconds=20 * tick)
            viewer_demo.seed_tick(db, tick=tick, now=now)
            assert shop_views.count_live_viewers(db, shop_id=shop_a.id, now=now) == 2, (
                f"live count drifted at tick {tick}"
            )

    def test_row_count_is_bounded_by_the_identity_pool(self, seeded, cfg):
        """Rows are a pure function of (shop, slot, identity), so once rotation has cycled the pool
        every later tick reuses rows. Bound: shops x slots x pool."""
        db, *_ = seeded
        cfg(per_shop=2, churn=1)

        for tick in range(30):
            viewer_demo.seed_tick(db, tick=tick, now=NOW + timedelta(seconds=20 * tick))

        # 2 real shops x 2 slots x 3 identities
        assert db.query(ShopViewEvent).count() <= 2 * 2 * 3

    def test_churn_rotates_occupants_between_ticks(self, seeded, cfg):
        """The regression that motivated encoding identity in the session id: with a stable
        per-slot session id, viewer_uuid's insert-only capture froze each slot's face and this
        assertion failed while everything else still passed."""
        db, shop_a, *_ = seeded
        cfg(per_shop=2, churn=1)

        def live_faces(now):
            cutoff = now - timedelta(seconds=shop_views.LIVE_WINDOW_SECONDS)
            return {
                r.viewer_uuid
                for r in db.query(ShopViewEvent).filter(
                    ShopViewEvent.shop_id == shop_a.id,
                    ShopViewEvent.last_heartbeat_at > cutoff,
                )
            }

        viewer_demo.seed_tick(db, tick=0, now=NOW)
        first = live_faces(NOW)
        later = NOW + timedelta(seconds=20)
        viewer_demo.seed_tick(db, tick=1, now=later)
        second = live_faces(later)

        assert first and second
        assert first != second, "at least one visible face must change hands between ticks"

    def test_zero_churn_keeps_a_static_population(self, seeded, cfg):
        """churn=0 is documented as 'static population' — assert it actually is, so the config
        knob doesn't silently do nothing."""
        db, shop_a, *_ = seeded
        cfg(per_shop=2, churn=0)

        viewer_demo.seed_tick(db, tick=0, now=NOW)
        before = {
            r.session_id: r.viewer_uuid for r in db.query(ShopViewEvent).all()
        }
        viewer_demo.seed_tick(db, tick=2, now=NOW + timedelta(seconds=20))
        after = {r.session_id: r.viewer_uuid for r in db.query(ShopViewEvent).all()}
        assert before.keys() == after.keys()
        assert before == after

    def test_some_viewers_are_on_a_product_and_some_are_not(self, seeded, cfg):
        """Both row shapes ('viewing <product>' vs plain browse) should appear, so the card's two
        rendering paths are both exercised by the demo data."""
        db, shop_a, *_ = seeded
        cfg(per_shop=4, churn=1)
        viewer_demo.seed_tick(db, tick=0, now=NOW)

        rows = db.query(ShopViewEvent).filter(ShopViewEvent.shop_id == shop_a.id).all()
        assert [r for r in rows if r.viewing_listing_id], "expected some product-viewers"
        assert [r for r in rows if not r.viewing_listing_id], "expected some plain browsers"

    def test_shop_without_listings_still_gets_viewers(self, seeded, cfg):
        """Beta Shop has no listing. Viewers must still appear (as plain browsers) — a shop with
        an empty catalog is exactly when a seller wants to know someone is looking."""
        db, _, shop_b, _ = seeded
        cfg(per_shop=2, churn=1)
        viewer_demo.seed_tick(db, tick=0, now=NOW)

        rows = db.query(ShopViewEvent).filter(ShopViewEvent.shop_id == shop_b.id).all()
        assert len(rows) == 2
        assert all(r.viewing_listing_id is None for r in rows)

    def test_viewer_uuids_are_real_weespas_ids(self, seeded, cfg):
        """Every seeded row must carry an identity the bridge can resolve — otherwise the Viewing
        Card renders a list of 'Guest' and the seeding achieved nothing."""
        db, *_ = seeded
        cfg(per_shop=2, churn=1)
        viewer_demo.seed_tick(db, tick=0, now=NOW)

        uuids = {r.viewer_uuid for r in db.query(ShopViewEvent).all()}
        assert uuids <= {_UUID_A, _UUID_B, _UUID_C}
        assert None not in uuids

    def test_session_ids_fit_the_column_and_are_prefixed(self, seeded, cfg):
        """The column is String(64) and record_heartbeat rejects longer ids. The prefix is what
        makes the seeder's rows identifiable for teardown."""
        db, *_ = seeded
        cfg(per_shop=2, churn=1)
        viewer_demo.seed_tick(db, tick=0, now=NOW)

        for r in db.query(ShopViewEvent).all():
            assert r.session_id.startswith(viewer_demo.DEMO_SESSION_PREFIX)
            assert len(r.session_id) <= 64

    def test_long_shop_id_still_yields_unique_bounded_session_ids(self):
        """Truncation must cut the SHOP id, never the slot/identity suffix — two slots that
        collapsed to the same session id would silently halve the population via upsert."""
        long_id = "s" * 120
        ids = {
            viewer_demo._session_id(long_id, slot, ident)
            for slot in range(5) for ident in range(4)
        }
        assert len(ids) == 20
        assert all(len(i) <= 64 for i in ids)

    def test_no_shops_is_a_safe_noop(self, db_session):
        """An empty dev DB must not raise — the launcher may start before any shop is seeded."""
        assert viewer_demo.seed_tick(db_session, tick=0, now=NOW) == 0

    def test_no_identities_is_a_safe_noop(self, db_session):
        """A DB with only trending-pool sellers has no resolvable identity: seed nothing rather
        than write 'Guest' rows."""
        fake = _seller(db_session, "demo-trending-pool-1", "Demo")
        _shop(db_session, fake)
        db_session.commit()
        assert viewer_demo.seed_tick(db_session, tick=0, now=NOW) == 0


class TestTeardown:
    def test_expire_drains_the_live_viewer_list(self, seeded, cfg):
        db, shop_a, *_ = seeded
        cfg(per_shop=3, churn=1)
        viewer_demo.seed_tick(db, tick=0, now=NOW)
        assert shop_views.count_live_viewers(db, shop_id=shop_a.id, now=NOW) == 3

        n = viewer_demo.expire_demo_viewers(db, now=NOW)
        db.commit()

        assert n == 6
        # Card drains to empty...
        assert shop_views.count_live_viewers(db, shop_id=shop_a.id, now=NOW) == 0
        # ...but the rows survive, so the History tab still has data and the next run reuses them.
        assert db.query(ShopViewEvent).count() == 6

    def test_expire_ignores_rows_it_did_not_create(self, seeded, cfg):
        """A real browser's row must never be backdated by the seeder — not on teardown, and not
        by the per-tick retirement either."""
        db, shop_a, *_ = seeded
        cfg(per_shop=2, churn=1)
        shop_views.record_heartbeat(
            db, shop_id=shop_a.id, session_id="real-browser-session",
            viewer_uuid=_UUID_A, now=NOW,
        )
        viewer_demo.seed_tick(db, tick=0, now=NOW)          # exercises the per-tick path too
        viewer_demo.expire_demo_viewers(db, now=NOW)
        db.commit()

        assert shop_views.count_live_viewers(db, shop_id=shop_a.id, now=NOW) == 1


class TestProductionGate:
    def test_refuses_to_run_in_production(self, cfg):
        """The load-bearing safety property: this process fabricates traffic, so it must be
        impossible to start it against production even with the flag switched on."""
        cfg(env="production", enabled=True)
        with pytest.raises(RuntimeError, match="production"):
            viewer_demo.run_forever(threading.Event())

    def test_flag_off_idles_without_writing(self, seeded, cfg):
        """Second gate: started but disabled must fabricate NOTHING, then exit on the stop event."""
        db, *_ = seeded
        cfg(env="development", enabled=False)

        stop = threading.Event()
        stop.set()                       # pre-set: run_forever's stop.wait() returns immediately
        viewer_demo.run_forever(stop)

        assert db.query(ShopViewEvent).count() == 0
