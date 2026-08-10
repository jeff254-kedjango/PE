"""Shop-view endpoint tests (§8, Chunk C). Covers the four endpoints:
  * POST   /shops/{id}/heartbeat        — anon + signed-in
  * GET    /shops/{id}/live-count       — owner-only
  * GET    /shops/{id}/view-history     — owner-only, cursor pagination
  * POST   /shops/{id}/promote-all      — owner-only, boosts every active listing
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from PE.commerce.core.database import get_db
from PE.commerce.main import app
from PE.commerce.models.listing import Listing
from PE.commerce.models.seller import Seller
from PE.commerce.services.proximity import set_location

_KEYS = Path(__file__).resolve().parents[3] / "PE" / "dev" / "keys"
_PRIVATE = (_KEYS / "insar_jwt_private.pem").read_text()
_SELLER = ("read:feed", "create:trades")
_BUYER = ("read:feed",)
_LAT, _LNG = -1.2920, 36.8219


def _mint(sub, scopes):
    return jwt.encode(
        {"sub": sub, "role": "user", "scope": "commerce_trade", "scopes": list(scopes),
         "exp": datetime.now(timezone.utc) + timedelta(minutes=10)},
        _PRIVATE, algorithm="RS256",
    )


def _auth(sub, scopes=_SELLER):
    return {"Authorization": f"Bearer {_mint(sub, scopes)}"}


@pytest.fixture
def client(db_session):
    app.dependency_overrides.clear()

    def _override_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_db
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _open_shop(client, seller_sub, name="Shop"):
    r = client.post(
        "/api/v1/shops",
        json={"name": name, "lat": _LAT, "lng": _LNG, "display_name": seller_sub},
        headers=_auth(seller_sub),
    )
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

class TestHeartbeat:
    def test_anonymous_heartbeat_accepted(self, client):
        shop = _open_shop(client, "seller-a")
        r = client.post(
            f"/api/v1/shops/{shop['id']}/heartbeat",
            json={"session_id": "anon-sess-1"},
            # No Authorization header → anon path.
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["was_new_visit"] is True

    def test_signed_in_heartbeat_captures_sub(self, client, db_session):
        shop = _open_shop(client, "seller-a")
        r = client.post(
            f"/api/v1/shops/{shop['id']}/heartbeat",
            json={"session_id": "signed-sess"},
            headers=_auth("visitor-1"),
        )
        assert r.status_code == 200
        # The row must have viewer_uuid populated on first ping when signed in.
        from PE.commerce.models.shop_view import ShopViewEvent
        row = db_session.query(ShopViewEvent).filter(ShopViewEvent.session_id == "signed-sess").one()
        assert row.viewer_uuid == "visitor-1"

    def test_second_heartbeat_reports_not_new_visit(self, client):
        shop = _open_shop(client, "seller-a")
        client.post(f"/api/v1/shops/{shop['id']}/heartbeat", json={"session_id": "s1"})
        r = client.post(f"/api/v1/shops/{shop['id']}/heartbeat", json={"session_id": "s1"})
        assert r.json()["was_new_visit"] is False

    def test_heartbeat_on_missing_shop_404s(self, client):
        r = client.post(
            "/api/v1/shops/does-not-exist/heartbeat",
            json={"session_id": "s1"},
        )
        assert r.status_code == 404

    def test_empty_session_id_422s(self, client):
        shop = _open_shop(client, "seller-a")
        r = client.post(f"/api/v1/shops/{shop['id']}/heartbeat", json={"session_id": ""})
        assert r.status_code == 422

    def test_bad_token_is_treated_as_anon_not_401(self, client):
        # A malformed/expired token folds into anon rather than blocking a heartbeat — the
        # endpoint's contract is "always accept a valid heartbeat".
        shop = _open_shop(client, "seller-a")
        r = client.post(
            f"/api/v1/shops/{shop['id']}/heartbeat",
            json={"session_id": "s1"},
            headers={"Authorization": "Bearer garbage"},
        )
        assert r.status_code == 200

    def test_heartbeat_accepts_viewing_listing_id(self, client, db_session):
        # §8 Chunk C+: the heartbeat body may carry viewing_listing_id; the server persists it
        # on the row (latest wins). Endpoint-level: same happy path as the base heartbeat, plus
        # the row-level assertion that the id landed.
        shop = _open_shop(client, "seller-a")
        r = client.post(
            f"/api/v1/shops/{shop['id']}/heartbeat",
            json={"session_id": "sess-with-listing", "viewing_listing_id": "some-listing-id"},
        )
        assert r.status_code == 200
        from PE.commerce.models.shop_view import ShopViewEvent
        row = db_session.query(ShopViewEvent).filter(
            ShopViewEvent.session_id == "sess-with-listing",
        ).one()
        assert row.viewing_listing_id == "some-listing-id"

    def test_heartbeat_clears_viewing_listing_id_via_null(self, client, db_session):
        # First ping sets a listing; second omits it → row's viewing_listing_id is null.
        shop = _open_shop(client, "seller-a")
        client.post(
            f"/api/v1/shops/{shop['id']}/heartbeat",
            json={"session_id": "s1", "viewing_listing_id": "listing-a"},
        )
        client.post(
            f"/api/v1/shops/{shop['id']}/heartbeat",
            json={"session_id": "s1"},  # no viewing_listing_id key
        )
        from PE.commerce.models.shop_view import ShopViewEvent
        row = db_session.query(ShopViewEvent).filter(ShopViewEvent.session_id == "s1").one()
        assert row.viewing_listing_id is None


# ---------------------------------------------------------------------------
# Live count
# ---------------------------------------------------------------------------

class TestLiveCount:
    def test_owner_reads_live_count(self, client):
        shop = _open_shop(client, "seller-a")
        client.post(f"/api/v1/shops/{shop['id']}/heartbeat", json={"session_id": "v1"})
        client.post(f"/api/v1/shops/{shop['id']}/heartbeat", json={"session_id": "v2"})
        r = client.get(f"/api/v1/shops/{shop['id']}/live-count", headers=_auth("seller-a"))
        assert r.status_code == 200
        body = r.json()
        assert body["live_count"] == 2
        assert body["window_seconds"] > 0

    def test_non_owner_404s(self, client):
        shop = _open_shop(client, "seller-a")
        # A different signed-in seller should never see this shop's live count.
        _open_shop(client, "seller-b", name="B")
        r = client.get(f"/api/v1/shops/{shop['id']}/live-count", headers=_auth("seller-b"))
        assert r.status_code == 404

    def test_missing_token_401(self, client):
        shop = _open_shop(client, "seller-a")
        r = client.get(f"/api/v1/shops/{shop['id']}/live-count")
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# View history
# ---------------------------------------------------------------------------

class TestViewHistory:
    def test_owner_reads_history(self, client):
        shop = _open_shop(client, "seller-a")
        client.post(f"/api/v1/shops/{shop['id']}/heartbeat", json={"session_id": "v1"})
        client.post(f"/api/v1/shops/{shop['id']}/heartbeat", json={"session_id": "v2"})
        r = client.get(f"/api/v1/shops/{shop['id']}/view-history", headers=_auth("seller-a"))
        assert r.status_code == 200
        body = r.json()
        assert len(body["items"]) == 2
        # Newest first (v2 was inserted second → appears first).
        assert body["items"][0]["session_id"] == "v2"

    def test_cursor_pagination_walks(self, client):
        from urllib.parse import quote
        shop = _open_shop(client, "seller-a")
        for i in range(4):
            client.post(f"/api/v1/shops/{shop['id']}/heartbeat", json={"session_id": f"v{i}"})
        r = client.get(
            f"/api/v1/shops/{shop['id']}/view-history?limit=2",
            headers=_auth("seller-a"),
        )
        page1 = r.json()
        assert len(page1["items"]) == 2
        assert page1["next_cursor"] is not None
        # Cursor contains a '+' from the ISO timestamp — MUST be URL-encoded when appended to
        # the query string (any real client does this). The endpoint decodes it back to '+'.
        r2 = client.get(
            f"/api/v1/shops/{shop['id']}/view-history?limit=2&cursor={quote(page1['next_cursor'], safe='')}",
            headers=_auth("seller-a"),
        )
        page2 = r2.json()
        assert len(page2["items"]) == 2
        assert page2["next_cursor"] is None   # 4 total, 2 per page → this is the last page

    def test_non_owner_404s(self, client):
        shop = _open_shop(client, "seller-a")
        _open_shop(client, "seller-b")
        r = client.get(f"/api/v1/shops/{shop['id']}/view-history", headers=_auth("seller-b"))
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Promote-all
# ---------------------------------------------------------------------------

class TestPromoteAll:
    def _seed_listing(self, db_session, shop_id, seller_uuid, stock_qty=5, is_active=True):
        seller = db_session.query(Seller).filter(Seller.user_uuid == seller_uuid).one()
        li = Listing(
            shop_id=shop_id, seller_id=seller.id, title="Thing",
            price_cents=10000, stock_qty=stock_qty, pricing_mode="fixed", post_kind="listing",
            media_urls="[]", lat=_LAT, lng=_LNG, is_active=is_active,
        )
        set_location(li, _LAT, _LNG)
        db_session.add(li)
        db_session.commit()
        return li

    def test_owner_promotes_all_active_listings(self, client, db_session):
        shop = _open_shop(client, "seller-a")
        li1 = self._seed_listing(db_session, shop["id"], "seller-a")
        li2 = self._seed_listing(db_session, shop["id"], "seller-a")
        r = client.post(
            f"/api/v1/shops/{shop['id']}/promote-all?duration_seconds=3600",
            headers=_auth("seller-a"),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["promoted_count"] == 2
        # Both listings now carry an evergreen promotion.
        db_session.refresh(li1)
        db_session.refresh(li2)
        assert li1.promo_mode == "evergreen"
        assert li2.promo_mode == "evergreen"

    def test_out_of_stock_listings_are_skipped(self, client, db_session):
        shop = _open_shop(client, "seller-a")
        in_stock = self._seed_listing(db_session, shop["id"], "seller-a", stock_qty=3)
        oos = self._seed_listing(db_session, shop["id"], "seller-a", stock_qty=0)
        r = client.post(
            f"/api/v1/shops/{shop['id']}/promote-all?duration_seconds=3600",
            headers=_auth("seller-a"),
        )
        assert r.status_code == 200
        assert r.json()["promoted_count"] == 1
        db_session.refresh(in_stock)
        db_session.refresh(oos)
        assert in_stock.promo_mode == "evergreen"
        assert oos.promo_mode is None   # OOS never gets a boost

    def test_inactive_listings_are_skipped(self, client, db_session):
        shop = _open_shop(client, "seller-a")
        active = self._seed_listing(db_session, shop["id"], "seller-a")
        inactive = self._seed_listing(db_session, shop["id"], "seller-a", is_active=False)
        client.post(
            f"/api/v1/shops/{shop['id']}/promote-all?duration_seconds=3600",
            headers=_auth("seller-a"),
        )
        db_session.refresh(active)
        db_session.refresh(inactive)
        assert active.promo_mode == "evergreen"
        assert inactive.promo_mode is None

    def test_shop_with_no_listings_returns_zero_count(self, client):
        shop = _open_shop(client, "seller-a")
        r = client.post(
            f"/api/v1/shops/{shop['id']}/promote-all?duration_seconds=3600",
            headers=_auth("seller-a"),
        )
        assert r.status_code == 200
        assert r.json()["promoted_count"] == 0

    def test_non_owner_404s(self, client, db_session):
        shop = _open_shop(client, "seller-a")
        self._seed_listing(db_session, shop["id"], "seller-a")
        _open_shop(client, "seller-b")
        r = client.post(
            f"/api/v1/shops/{shop['id']}/promote-all?duration_seconds=3600",
            headers=_auth("seller-b"),
        )
        assert r.status_code == 404

    def test_duration_below_minimum_422s(self, client):
        shop = _open_shop(client, "seller-a")
        r = client.post(
            f"/api/v1/shops/{shop['id']}/promote-all?duration_seconds=10",
            headers=_auth("seller-a"),
        )
        assert r.status_code == 422

    def test_duration_above_max_422s(self, client):
        shop = _open_shop(client, "seller-a")
        # promo_max_duration_seconds is 86_400 (24h). Anything above → 422.
        r = client.post(
            f"/api/v1/shops/{shop['id']}/promote-all?duration_seconds=99999",
            headers=_auth("seller-a"),
        )
        assert r.status_code == 422

    def test_missing_scope_403s(self, client):
        shop = _open_shop(client, "seller-a")
        r = client.post(
            f"/api/v1/shops/{shop['id']}/promote-all?duration_seconds=3600",
            headers=_auth("seller-a", scopes=_BUYER),
        )
        assert r.status_code in (401, 403)
