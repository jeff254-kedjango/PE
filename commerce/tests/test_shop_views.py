"""Shop-view service tests (§8, Chunk C). Exercises the heartbeat upsert semantics, the live-
count freshness window, and the keyset-paginated history query."""
from datetime import datetime, timedelta, timezone

import pytest

from PE.commerce.models.seller import Seller, Shop
from PE.commerce.models.shop_view import ShopViewEvent
from PE.commerce.services import shop_views


_NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def shop(db_session):
    """A seller + shop the test can attach view events to."""
    s = Seller(user_uuid="seller-a", display_name="A")
    db_session.add(s)
    db_session.flush()
    sh = Shop(seller_id=s.id, name="Shop A", lat=-1.29, lng=36.82)
    db_session.add(sh)
    db_session.commit()
    return sh


class TestRecordHeartbeat:
    def test_first_ping_inserts_a_row(self, db_session, shop):
        r = shop_views.record_heartbeat(
            db_session, shop_id=shop.id, session_id="sess1", viewer_uuid=None, now=_NOW,
        )
        assert r.was_new_visit is True
        assert db_session.query(ShopViewEvent).count() == 1

    def test_second_ping_same_session_updates_heartbeat(self, db_session, shop):
        # First ping.
        first = shop_views.record_heartbeat(
            db_session, shop_id=shop.id, session_id="sess1", viewer_uuid=None, now=_NOW,
        )
        first_id = first.event.id
        # Second ping 30 seconds later.
        second = shop_views.record_heartbeat(
            db_session, shop_id=shop.id, session_id="sess1", viewer_uuid=None,
            now=_NOW + timedelta(seconds=30),
        )
        assert second.was_new_visit is False
        # Same row, updated heartbeat.
        assert second.event.id == first_id
        assert db_session.query(ShopViewEvent).count() == 1
        assert (second.event.last_heartbeat_at.replace(tzinfo=timezone.utc) if second.event.last_heartbeat_at.tzinfo is None else second.event.last_heartbeat_at) == _NOW + timedelta(seconds=30)

    def test_different_session_inserts_a_second_row(self, db_session, shop):
        shop_views.record_heartbeat(db_session, shop_id=shop.id, session_id="sess1", viewer_uuid=None, now=_NOW)
        shop_views.record_heartbeat(db_session, shop_id=shop.id, session_id="sess2", viewer_uuid=None, now=_NOW)
        assert db_session.query(ShopViewEvent).count() == 2

    def test_signed_in_viewer_captured_on_first_ping_only(self, db_session, shop):
        # First ping: anonymous.
        r1 = shop_views.record_heartbeat(
            db_session, shop_id=shop.id, session_id="sess1", viewer_uuid=None, now=_NOW,
        )
        assert r1.event.viewer_uuid is None
        # Second ping: same session, now signed-in. The row stays anonymous by design.
        r2 = shop_views.record_heartbeat(
            db_session, shop_id=shop.id, session_id="sess1", viewer_uuid="user-42",
            now=_NOW + timedelta(seconds=30),
        )
        assert r2.event.viewer_uuid is None   # anonymity is sticky within a visit

    def test_empty_shop_id_raises(self, db_session):
        with pytest.raises(shop_views.HeartbeatError):
            shop_views.record_heartbeat(
                db_session, shop_id="", session_id="sess1", viewer_uuid=None, now=_NOW,
            )

    def test_empty_session_id_raises(self, db_session, shop):
        with pytest.raises(shop_views.HeartbeatError):
            shop_views.record_heartbeat(
                db_session, shop_id=shop.id, session_id="", viewer_uuid=None, now=_NOW,
            )

    def test_oversize_session_id_raises(self, db_session, shop):
        with pytest.raises(shop_views.HeartbeatError):
            shop_views.record_heartbeat(
                db_session, shop_id=shop.id, session_id="x" * 65, viewer_uuid=None, now=_NOW,
            )

    def test_naive_now_tolerated(self, db_session, shop):
        # SQLite path can hand back a naive datetime; the service must not error out.
        naive = _NOW.replace(tzinfo=None)
        r = shop_views.record_heartbeat(
            db_session, shop_id=shop.id, session_id="sess1", viewer_uuid=None, now=naive,
        )
        assert r.was_new_visit is True

    # ---------- §8 Chunk C+ viewing_listing_id (latest wins) ----------

    def test_first_heartbeat_captures_viewing_listing_id(self, db_session, shop):
        r = shop_views.record_heartbeat(
            db_session, shop_id=shop.id, session_id="sess1", viewer_uuid=None, now=_NOW,
            viewing_listing_id="listing-42",
        )
        assert r.event.viewing_listing_id == "listing-42"

    def test_later_heartbeat_overwrites_viewing_listing_id(self, db_session, shop):
        # First ping: viewer on listing-42. Second ping: viewer on listing-77. Latest wins.
        shop_views.record_heartbeat(
            db_session, shop_id=shop.id, session_id="sess1", viewer_uuid=None, now=_NOW,
            viewing_listing_id="listing-42",
        )
        r2 = shop_views.record_heartbeat(
            db_session, shop_id=shop.id, session_id="sess1", viewer_uuid=None,
            now=_NOW + timedelta(seconds=30),
            viewing_listing_id="listing-77",
        )
        assert r2.event.viewing_listing_id == "listing-77"

    def test_heartbeat_clears_viewing_listing_id_when_null(self, db_session, shop):
        # Viewer opens a PDP, then leaves it to browse the storefront index (client omits or
        # explicitly nulls the field). The row's viewing_listing_id MUST clear — otherwise the
        # seller sees a stale "viewing X" long after the viewer moved on.
        shop_views.record_heartbeat(
            db_session, shop_id=shop.id, session_id="sess1", viewer_uuid=None, now=_NOW,
            viewing_listing_id="listing-42",
        )
        r2 = shop_views.record_heartbeat(
            db_session, shop_id=shop.id, session_id="sess1", viewer_uuid=None,
            now=_NOW + timedelta(seconds=30),
            viewing_listing_id=None,
        )
        assert r2.event.viewing_listing_id is None

    def test_empty_string_treated_as_null(self, db_session, shop):
        # A client that sends viewing_listing_id="" (a common serializer quirk) should not
        # produce a row that carries the empty string — it would break equality checks against
        # real ids downstream.
        r = shop_views.record_heartbeat(
            db_session, shop_id=shop.id, session_id="sess1", viewer_uuid=None, now=_NOW,
            viewing_listing_id="",
        )
        assert r.event.viewing_listing_id is None


class TestCountLiveViewers:
    def test_no_rows_returns_zero(self, db_session, shop):
        assert shop_views.count_live_viewers(db_session, shop_id=shop.id, now=_NOW) == 0

    def test_fresh_heartbeat_within_window_counts(self, db_session, shop):
        shop_views.record_heartbeat(db_session, shop_id=shop.id, session_id="sess1", viewer_uuid=None, now=_NOW)
        # 30s later — inside the 60s live window.
        assert shop_views.count_live_viewers(
            db_session, shop_id=shop.id, now=_NOW + timedelta(seconds=30),
        ) == 1

    def test_stale_heartbeat_outside_window_excluded(self, db_session, shop):
        shop_views.record_heartbeat(db_session, shop_id=shop.id, session_id="sess1", viewer_uuid=None, now=_NOW)
        # 90s later — outside the 60s window.
        assert shop_views.count_live_viewers(
            db_session, shop_id=shop.id, now=_NOW + timedelta(seconds=90),
        ) == 0

    def test_multiple_sessions_each_count_once(self, db_session, shop):
        shop_views.record_heartbeat(db_session, shop_id=shop.id, session_id="a", viewer_uuid=None, now=_NOW)
        shop_views.record_heartbeat(db_session, shop_id=shop.id, session_id="b", viewer_uuid="u2", now=_NOW)
        shop_views.record_heartbeat(db_session, shop_id=shop.id, session_id="c", viewer_uuid="u3", now=_NOW)
        assert shop_views.count_live_viewers(
            db_session, shop_id=shop.id, now=_NOW + timedelta(seconds=1),
        ) == 3

    def test_other_shop_isolated(self, db_session, shop):
        # A different shop's viewer must not leak into this shop's count.
        other = Shop(seller_id=shop.seller_id, name="B", lat=-1.29, lng=36.82)
        db_session.add(other)
        db_session.commit()
        shop_views.record_heartbeat(db_session, shop_id=other.id, session_id="sess1", viewer_uuid=None, now=_NOW)
        assert shop_views.count_live_viewers(db_session, shop_id=shop.id, now=_NOW) == 0

    def test_empty_shop_id_returns_zero(self, db_session):
        assert shop_views.count_live_viewers(db_session, shop_id="", now=_NOW) == 0


class TestListViewHistory:
    def test_empty_shop_returns_empty_page(self, db_session, shop):
        page = shop_views.list_view_history(db_session, shop_id=shop.id)
        assert page.rows == []
        assert page.next_cursor is None

    def test_newest_first_order(self, db_session, shop):
        shop_views.record_heartbeat(db_session, shop_id=shop.id, session_id="a", viewer_uuid=None, now=_NOW)
        shop_views.record_heartbeat(db_session, shop_id=shop.id, session_id="b", viewer_uuid=None, now=_NOW + timedelta(minutes=1))
        shop_views.record_heartbeat(db_session, shop_id=shop.id, session_id="c", viewer_uuid=None, now=_NOW + timedelta(minutes=2))
        page = shop_views.list_view_history(db_session, shop_id=shop.id)
        assert [r.session_id for r in page.rows] == ["c", "b", "a"]

    def test_since_filter(self, db_session, shop):
        shop_views.record_heartbeat(db_session, shop_id=shop.id, session_id="old", viewer_uuid=None, now=_NOW - timedelta(days=2))
        shop_views.record_heartbeat(db_session, shop_id=shop.id, session_id="new", viewer_uuid=None, now=_NOW)
        page = shop_views.list_view_history(db_session, shop_id=shop.id, since=_NOW - timedelta(hours=1))
        assert [r.session_id for r in page.rows] == ["new"]

    def test_until_filter(self, db_session, shop):
        shop_views.record_heartbeat(db_session, shop_id=shop.id, session_id="early", viewer_uuid=None, now=_NOW)
        shop_views.record_heartbeat(db_session, shop_id=shop.id, session_id="late", viewer_uuid=None, now=_NOW + timedelta(hours=2))
        page = shop_views.list_view_history(db_session, shop_id=shop.id, until=_NOW + timedelta(hours=1))
        assert [r.session_id for r in page.rows] == ["early"]

    def test_cursor_pagination(self, db_session, shop):
        # 5 visits, page-size 2 → we should walk c-d, then b-a-plus-cursor-empty over three pages.
        for i, ts in enumerate([_NOW, _NOW + timedelta(minutes=1), _NOW + timedelta(minutes=2),
                                _NOW + timedelta(minutes=3), _NOW + timedelta(minutes=4)]):
            shop_views.record_heartbeat(db_session, shop_id=shop.id, session_id=f"s{i}", viewer_uuid=None, now=ts)
        page1 = shop_views.list_view_history(db_session, shop_id=shop.id, limit=2)
        assert len(page1.rows) == 2
        assert page1.next_cursor is not None
        page2 = shop_views.list_view_history(db_session, shop_id=shop.id, limit=2, cursor=page1.next_cursor)
        assert len(page2.rows) == 2
        assert page2.next_cursor is not None
        page3 = shop_views.list_view_history(db_session, shop_id=shop.id, limit=2, cursor=page2.next_cursor)
        assert len(page3.rows) == 1
        assert page3.next_cursor is None
        # No row repeated across pages.
        seen = [r.session_id for r in page1.rows + page2.rows + page3.rows]
        assert len(seen) == len(set(seen))

    def test_malformed_cursor_treated_as_start(self, db_session, shop):
        # A stale/garbled cursor from a client should not error — just start from the top.
        shop_views.record_heartbeat(db_session, shop_id=shop.id, session_id="a", viewer_uuid=None, now=_NOW)
        page = shop_views.list_view_history(db_session, shop_id=shop.id, cursor="not-a-valid-cursor")
        assert len(page.rows) == 1

    def test_limit_is_capped(self, db_session, shop):
        # limit=500 should get clamped to 200. Insert enough rows to prove it.
        for i in range(5):
            shop_views.record_heartbeat(db_session, shop_id=shop.id, session_id=f"s{i}", viewer_uuid=None, now=_NOW + timedelta(seconds=i))
        page = shop_views.list_view_history(db_session, shop_id=shop.id, limit=500)
        assert len(page.rows) == 5   # not the cap; we only have 5 rows. The clamp is silent.

    def test_empty_shop_id_returns_empty(self, db_session):
        page = shop_views.list_view_history(db_session, shop_id="")
        assert page.rows == []
