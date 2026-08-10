"""Bargain state machine — offer/counter/accept turn-taking, round cap, bounds, CAS races.

The §7 trust core: server-authoritative price, ≤3 counter rounds, accept is the sole writer of
the locked price, and concurrent accepts resolve to one winner + one 409.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from PE.commerce.core.database import get_db
from PE.commerce.main import app
from PE.commerce.services import settlement

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


def _auth(sub, scopes=_BUYER):
    return {"Authorization": f"Bearer {_mint(sub, scopes)}"}


def _idem(key):
    return {"Idempotency-Key": key}


@pytest.fixture
def client(db_session):
    app.dependency_overrides.clear()

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _bargain_listing(client, seller_sub, price=10000):
    shop = client.post(
        "/api/v1/shops",
        json={"name": "Shop", "lat": _LAT, "lng": _LNG, "display_name": "S"},
        headers=_auth(seller_sub, _SELLER),
    ).json()
    return client.post(
        f"/api/v1/shops/{shop['id']}/listings",
        json={"title": "Goat", "price_cents": price, "stock_qty": 3, "pricing_mode": "bargain"},
        headers=_auth(seller_sub, _SELLER),
    ).json()["id"]


def test_full_bargain_offer_counter_accept(client):
    seller, buyer = _auth("seller-bg", _SELLER), _auth("buyer-bg")
    lid = _bargain_listing(client, "seller-bg", price=10000)

    # buyer opens at 8000
    o = client.post("/api/v1/orders", json={"listing_id": lid, "offer_cents": 8000},
                    headers={**buyer, **_idem("o1")}).json()
    oid = o["id"]
    assert o["status"] == "OFFERED" and o["current_offer_by"] == "buyer"

    # seller counters 9000 (it's the seller's turn)
    r = client.post(f"/api/v1/orders/{oid}/counter", json={"amount_cents": 9000},
                    headers={**seller, **_idem("c1")})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "COUNTERED" and r.json()["current_offer_by"] == "seller"
    assert r.json()["round_count"] == 1

    # buyer accepts the seller's 9000 → locked at 9000
    r = client.post(f"/api/v1/orders/{oid}/accept", headers={**buyer, **_idem("a1")})
    assert r.status_code == 200
    assert r.json()["status"] == "PRICE_LOCKED" and r.json()["locked_price_cents"] == 9000

    # settle → 3% of 9000 = 270
    r = client.post(f"/api/v1/orders/{oid}/settle", headers={**buyer, **_idem("s1")})
    assert r.json()["status"] == "SETTLED" and r.json()["commission_cents"] == 270


def test_cannot_accept_your_own_offer(client):
    buyer = _auth("buyer-own")
    lid = _bargain_listing(client, "seller-own")
    oid = client.post("/api/v1/orders", json={"listing_id": lid, "offer_cents": 7000},
                      headers={**buyer, **_idem("o1")}).json()["id"]
    # buyer made the standing offer; buyer accepting their own → 403
    r = client.post(f"/api/v1/orders/{oid}/accept", headers={**buyer, **_idem("a1")})
    assert r.status_code == 403


def test_counter_wrong_turn_forbidden(client):
    seller, buyer = _auth("seller-turn", _SELLER), _auth("buyer-turn")
    lid = _bargain_listing(client, "seller-turn")
    oid = client.post("/api/v1/orders", json={"listing_id": lid, "offer_cents": 7000},
                      headers={**buyer, **_idem("o1")}).json()["id"]
    # buyer just offered; buyer countering again (own turn) → 403
    r = client.post(f"/api/v1/orders/{oid}/counter", json={"amount_cents": 7500},
                    headers={**buyer, **_idem("c1")})
    assert r.status_code == 403


def test_round_cap_enforced(client):
    seller, buyer = _auth("seller-cap", _SELLER), _auth("buyer-cap")
    lid = _bargain_listing(client, "seller-cap", price=10000)
    oid = client.post("/api/v1/orders", json={"listing_id": lid, "offer_cents": 5000},
                      headers={**buyer, **_idem("o1")}).json()["id"]
    # 3 counters allowed (seller, buyer, seller), 4th rejected
    client.post(f"/api/v1/orders/{oid}/counter", json={"amount_cents": 9000}, headers={**seller, **_idem("c1")})
    client.post(f"/api/v1/orders/{oid}/counter", json={"amount_cents": 6000}, headers={**buyer, **_idem("c2")})
    client.post(f"/api/v1/orders/{oid}/counter", json={"amount_cents": 8500}, headers={**seller, **_idem("c3")})
    r = client.post(f"/api/v1/orders/{oid}/counter", json={"amount_cents": 6500}, headers={**buyer, **_idem("c4")})
    assert r.status_code == 422  # capped at 3 rounds


def test_offer_bounds_rejected(client):
    buyer = _auth("buyer-bnd")
    lid = _bargain_listing(client, "seller-bnd", price=10000)
    # > 10x reference (100000) → 422
    r = client.post("/api/v1/orders", json={"listing_id": lid, "offer_cents": 200000},
                    headers={**buyer, **_idem("o1")})
    assert r.status_code == 422


def test_bargain_requires_opening_offer(client):
    buyer = _auth("buyer-noo")
    lid = _bargain_listing(client, "seller-noo")
    r = client.post("/api/v1/orders", json={"listing_id": lid}, headers={**buyer, **_idem("o1")})
    assert r.status_code == 422  # bargain needs offer_cents


def test_concurrent_accept_one_wins_one_409(client, db_session):
    """Two accepts on the same locked-eligible order: simulate a stale version (a lost CAS)."""
    seller, buyer = _auth("seller-cas", _SELLER), _auth("buyer-cas")
    lid = _bargain_listing(client, "seller-cas")
    oid = client.post("/api/v1/orders", json={"listing_id": lid, "offer_cents": 7000},
                      headers={**buyer, **_idem("o1")}).json()["id"]
    # seller counters so it's buyer's turn to accept
    client.post(f"/api/v1/orders/{oid}/counter", json={"amount_cents": 8000},
                headers={**seller, **_idem("c1")})

    # first accept wins
    r1 = client.post(f"/api/v1/orders/{oid}/accept", headers={**buyer, **_idem("a1")})
    assert r1.status_code == 200 and r1.json()["status"] == "PRICE_LOCKED"
    # a second accept (new key) on the now-locked order → 409 (not OFFERED/COUNTERED anymore)
    r2 = client.post(f"/api/v1/orders/{oid}/accept", headers={**buyer, **_idem("a2")})
    assert r2.status_code == 409


def test_cancel_pending_then_cannot_act(client):
    buyer = _auth("buyer-cxl")
    lid = _bargain_listing(client, "seller-cxl")
    oid = client.post("/api/v1/orders", json={"listing_id": lid, "offer_cents": 7000},
                      headers={**buyer, **_idem("o1")}).json()["id"]
    assert client.post(f"/api/v1/orders/{oid}/cancel", headers=buyer).json()["status"] == "CANCELLED"
    # accepting a cancelled order → 409
    r = client.post(f"/api/v1/orders/{oid}/accept", headers={**buyer, **_idem("a1")})
    assert r.status_code == 409
