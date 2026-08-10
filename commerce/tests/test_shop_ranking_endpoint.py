"""GET /api/v1/sellers/me/ranking (§8, Chunk B) — endpoint tests.

Covers:
  * default happy path (radius = 10 km) → RankingOut.
  * a caller with no shop → RankingUnavailableOut (`kind: no_shop`), 200 not 404.
  * radius > 200 km without entitlement → RankingPaywallOut (200 with `kind: paywall_required`).
  * radius > 200 km WITH entitlement → RankingOut (paywall bypassed).
  * cache: two calls in the same 5-min window hit the SAME payload (no recompute).
  * cache: paywall check runs BEFORE the cache lookup (no leak).
  * scope gate: a token without create:trades is rejected.

Uses real RS256 tokens (matches test_reviews.py's pattern)."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from PE.commerce.core.database import get_db
from PE.commerce.main import app
from PE.commerce.models.listing import Listing
from PE.commerce.models.order import Order, STATUS_SETTLED
from PE.commerce.models.seller import Seller, Shop
from PE.commerce.routers.shop_ranking import _clear_cache_for_tests
from PE.commerce.services import ranking_entitlement
from PE.commerce.models.ranking import ENTITLEMENT_KIND_ONE_TIME_2H
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
    _clear_cache_for_tests()
    app.dependency_overrides.clear()

    def _override_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_db
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()
    _clear_cache_for_tests()


def _open_shop(client, seller_sub, name="Shop"):
    """POST /shops via the real endpoint — creates both the Seller (from the token) and the Shop."""
    r = client.post(
        "/api/v1/shops",
        json={"name": name, "lat": _LAT, "lng": _LNG, "display_name": seller_sub},
        headers=_auth(seller_sub),
    )
    assert r.status_code == 201, r.text
    return r.json()


class TestHappyPath:
    def test_default_radius_returns_ranking(self, client):
        _open_shop(client, "seller-a")
        r = client.get("/api/v1/sellers/me/ranking", headers=_auth("seller-a"))
        assert r.status_code == 200
        body = r.json()
        assert body["kind"] == "ranking"
        assert body["rank"] == 1        # solo shop
        assert body["peer_count"] == 1
        assert body["radius_km"] == 10.0
        # weight_breakdown + signals shape is present, defaults are sensible.
        assert body["weight_breakdown"]["sales_score"] == 0.0    # no orders yet
        assert body["signals"]["revenue_cents"] == 0
        assert body["signals"]["rating_count"] == 0
        # refreshed_at + next_refresh_at parse as ISO datetimes and differ by ~5 min.
        r1 = datetime.fromisoformat(body["refreshed_at"].replace("Z", "+00:00"))
        r2 = datetime.fromisoformat(body["next_refresh_at"].replace("Z", "+00:00"))
        assert 240 <= (r2 - r1).total_seconds() <= 360   # 5min ±60s of slack

    def test_custom_radius_within_free_cap(self, client):
        _open_shop(client, "seller-a")
        r = client.get("/api/v1/sellers/me/ranking?radius_km=50", headers=_auth("seller-a"))
        assert r.status_code == 200
        assert r.json()["kind"] == "ranking"
        assert r.json()["radius_km"] == 50.0


class TestNoShop:
    def test_seller_without_shop_returns_no_shop_kind(self, client):
        # Mint a token but never open a shop.
        r = client.get("/api/v1/sellers/me/ranking", headers=_auth("ghost"))
        assert r.status_code == 200
        assert r.json() == {"kind": "no_shop"}


class TestPaywall:
    def test_radius_over_free_cap_no_entitlement_gates(self, client):
        _open_shop(client, "seller-a")
        r = client.get("/api/v1/sellers/me/ranking?radius_km=300", headers=_auth("seller-a"))
        assert r.status_code == 200   # a paywall is a NORMAL answer, not an HTTP error
        body = r.json()
        assert body["kind"] == "paywall_required"
        assert body["free_max_radius_km"] == 200.0
        assert body["requested_radius_km"] == 300.0
        assert set(body["cta_kinds"]) == {"one_time_2h", "annual"}

    def test_at_exactly_free_cap_allowed_without_entitlement(self, client):
        _open_shop(client, "seller-a")
        r = client.get("/api/v1/sellers/me/ranking?radius_km=200", headers=_auth("seller-a"))
        assert r.status_code == 200
        assert r.json()["kind"] == "ranking"

    def test_with_active_entitlement_paywall_bypassed(self, client, db_session):
        _open_shop(client, "seller-a")
        ranking_entitlement.grant_entitlement(
            db_session, user_uuid="seller-a", kind=ENTITLEMENT_KIND_ONE_TIME_2H,
            now=datetime.now(timezone.utc),
        )
        db_session.commit()   # visible to the endpoint's session
        r = client.get("/api/v1/sellers/me/ranking?radius_km=500", headers=_auth("seller-a"))
        assert r.status_code == 200
        body = r.json()
        assert body["kind"] == "ranking"
        assert body["radius_km"] == 500.0


class TestCache:
    def test_second_call_within_ttl_returns_same_payload(self, client):
        _open_shop(client, "seller-a")
        first = client.get("/api/v1/sellers/me/ranking?radius_km=10", headers=_auth("seller-a")).json()
        second = client.get("/api/v1/sellers/me/ranking?radius_km=10", headers=_auth("seller-a")).json()
        # `refreshed_at` is captured once at compute time and cached, so a same-window call
        # returns EXACTLY the first timestamp (byte-identical payload).
        assert first == second

    def test_paywall_runs_before_cache_lookup(self, client, db_session):
        # Prime a cached happy-path payload at radius=10 for seller-a.
        _open_shop(client, "seller-a")
        primed = client.get("/api/v1/sellers/me/ranking?radius_km=10", headers=_auth("seller-a")).json()
        assert primed["kind"] == "ranking"
        # A subsequent call at radius=300 must hit the paywall, NOT the cached ranking (even
        # though a rounded-cache-key collision would have to be constructed on purpose, this
        # test guards the invariant: the gate is UPSTREAM of the cache lookup).
        r = client.get("/api/v1/sellers/me/ranking?radius_km=300", headers=_auth("seller-a"))
        assert r.status_code == 200
        assert r.json()["kind"] == "paywall_required"


class TestAuth:
    def test_missing_scope_rejected(self, client):
        # A buyer token (read:feed only) can't reach a seller-owner surface.
        _open_shop(client, "seller-a")   # need a seller row so the endpoint has a target
        r = client.get("/api/v1/sellers/me/ranking", headers=_auth("seller-a", scopes=_BUYER))
        assert r.status_code in (401, 403)   # the auth layer decides which

    def test_missing_token_rejected(self, client):
        _open_shop(client, "seller-a")
        r = client.get("/api/v1/sellers/me/ranking")
        assert r.status_code == 401


class TestPeerRankReflectsSales:
    def test_two_shops_ranked_by_revenue(self, client, db_session):
        # Seller A: one big settled order. Seller B: none. A ranks #1.
        shop_a = _open_shop(client, "seller-a", name="A")
        shop_b = _open_shop(client, "seller-b", name="B")
        # Directly seed a settled order (skipping the full negotiation flow which is exercised
        # in test_settlement.py). Both shops are at the same coords → both in each other's radius.
        seller_a = db_session.query(Seller).filter(Seller.user_uuid == "seller-a").one()
        li = Listing(
            shop_id=shop_a["id"], seller_id=seller_a.id, title="Thing",
            price_cents=10000, stock_qty=5, pricing_mode="fixed", post_kind="listing",
            media_urls="[]", lat=_LAT, lng=_LNG,
        )
        set_location(li, _LAT, _LNG)
        db_session.add(li)
        db_session.flush()
        order = Order(
            listing_id=li.id, seller_id=seller_a.id, buyer_uuid="buyer1",
            pricing_mode="fixed", status=STATUS_SETTLED,
            reference_price_cents=10000, locked_price_cents=100_000, commission_cents=3000,
            version=1,
        )
        db_session.add(order)
        db_session.commit()

        a = client.get("/api/v1/sellers/me/ranking", headers=_auth("seller-a")).json()
        b = client.get("/api/v1/sellers/me/ranking", headers=_auth("seller-b")).json()
        assert a["kind"] == "ranking" and b["kind"] == "ranking"
        assert a["rank"] == 1
        assert b["rank"] == 2
        assert a["peer_count"] == 2
        assert a["signals"]["revenue_cents"] == 100_000
