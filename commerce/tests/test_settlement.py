"""Settlement — fixed-price flow, commission ledger, idempotency, authorization, event chain.

Real RS256 tokens. The order endpoints run the real Redis denylist (fail-closed); these tests
keep the actors OFF the denylist (a dedicated test scrubs denylist behaviour). Each test uses
unique subs so leftover state can't bleed across tests.
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


def _seed_listing(client, seller_sub, *, price=10000, mode="fixed", stock=5):
    shop = client.post(
        "/api/v1/shops",
        json={"name": "Shop", "lat": _LAT, "lng": _LNG, "display_name": "S"},
        headers=_auth(seller_sub, _SELLER),
    ).json()
    li = client.post(
        f"/api/v1/shops/{shop['id']}/listings",
        json={"title": "Thing", "price_cents": price, "stock_qty": stock, "pricing_mode": mode},
        headers=_auth(seller_sub, _SELLER),
    ).json()
    return li["id"]


# ----------------------------- commission math (pure) -----------------------------

@pytest.mark.parametrize("price,expected", [(1, 0), (33, 0), (34, 1), (100, 3), (999, 29), (10000, 300)])
def test_commission_is_integer_floor(price, expected):
    assert settlement.commission_cents(price) == expected


# ----------------------------- idempotency scope length (Postgres truncation regression) ---------

def test_idempotency_scope_fits_column_with_uuid_ids():
    """The longest scope a transition builds, "settle:{user_uuid}:{order_id}", must fit the
    IdempotencyKey.scope column. With real UUID-length ids this is ~80 chars; the column was
    String(64) and Postgres truncated → every bargain settle 500'd in production (the live
    Playwright e2e caught it; SQLite ignores VARCHAR length so it can't). This pins the invariant
    so the column can never silently shrink below what the scope builders need."""
    import uuid as _uuid

    from PE.commerce.models.order import IdempotencyKey

    user_id, order_id = str(_uuid.uuid4()), str(_uuid.uuid4())
    longest_scope = f"settle:{user_id}:{order_id}"
    column_len = IdempotencyKey.__table__.c.scope.type.length
    assert len(longest_scope) <= column_len, (
        f"scope of {len(longest_scope)} chars exceeds column length {column_len}"
    )


# ----------------------------- fixed flow -----------------------------

def test_fixed_order_locks_and_settles(client):
    lid = _seed_listing(client, "seller-fx", price=10000, mode="fixed")
    buyer = _auth("buyer-fx")

    r = client.post("/api/v1/orders", json={"listing_id": lid}, headers={**buyer, **_idem("k1")})
    assert r.status_code == 201, r.text
    o = r.json()
    assert o["status"] == "PRICE_LOCKED"
    assert o["locked_price_cents"] == 10000  # fixed locks at list price immediately
    oid = o["id"]

    r = client.post(f"/api/v1/orders/{oid}/settle", headers={**buyer, **_idem("k2")})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["status"] == "SETTLED"
    assert s["commission_cents"] == 300  # 3% of 10000
    assert s["rail_ref"].startswith("stub-")  # stub rail, no real money


def test_order_detail_has_valid_hash_chain(client):
    lid = _seed_listing(client, "seller-hc", price=5000, mode="fixed")
    buyer = _auth("buyer-hc")
    oid = client.post("/api/v1/orders", json={"listing_id": lid},
                      headers={**buyer, **_idem("k1")}).json()["id"]
    client.post(f"/api/v1/orders/{oid}/settle", headers={**buyer, **_idem("k2")})

    detail = client.get(f"/api/v1/orders/{oid}", headers=buyer).json()
    events = detail["events"]
    types = [e["event_type"] for e in events]
    assert types == ["open", "lock", "settle_record", "settle_ok"]
    # chain integrity: each event's prev_hash == the previous event's row_hash; genesis prev=None
    assert events[0]["prev_hash"] is None
    for prev, cur in zip(events, events[1:]):
        assert cur["prev_hash"] == prev["row_hash"]


# ----------------------------- idempotency -----------------------------

def test_open_is_idempotent_on_key(client):
    lid = _seed_listing(client, "seller-id", mode="fixed")
    buyer = _auth("buyer-id")
    r1 = client.post("/api/v1/orders", json={"listing_id": lid}, headers={**buyer, **_idem("same")})
    r2 = client.post("/api/v1/orders", json={"listing_id": lid}, headers={**buyer, **_idem("same")})
    # same key → same order, not a second one (and not a one-open-per-pair 409)
    assert r1.json()["id"] == r2.json()["id"]


def test_state_change_requires_idempotency_key(client):
    lid = _seed_listing(client, "seller-nk", mode="fixed")
    r = client.post("/api/v1/orders", json={"listing_id": lid}, headers=_auth("buyer-nk"))
    assert r.status_code == 422  # missing Idempotency-Key


# ----------------------------- one-open-per-pair -----------------------------

def test_one_open_order_per_buyer_listing(client):
    lid = _seed_listing(client, "seller-1o", mode="bargain", price=10000)
    buyer = _auth("buyer-1o")
    r1 = client.post("/api/v1/orders", json={"listing_id": lid, "offer_cents": 9000},
                     headers={**buyer, **_idem("a")})
    assert r1.status_code == 201
    # different key, same (buyer, listing) while first is open → 409
    r2 = client.post("/api/v1/orders", json={"listing_id": lid, "offer_cents": 8000},
                     headers={**buyer, **_idem("b")})
    assert r2.status_code == 409


# ----------------------------- authorization -----------------------------

def test_non_party_cannot_view_order(client):
    lid = _seed_listing(client, "seller-auth", mode="fixed")
    oid = client.post("/api/v1/orders", json={"listing_id": lid},
                      headers={**_auth("buyer-auth"), **_idem("k1")}).json()["id"]
    # a stranger → 404 (no existence leak)
    assert client.get(f"/api/v1/orders/{oid}", headers=_auth("stranger")).status_code == 404


def test_settle_nonexistent_order_404(client):
    assert client.post(
        "/api/v1/orders/nope/settle", headers={**_auth("b"), **_idem("k")}
    ).status_code == 404


def test_open_on_missing_listing_404(client):
    assert client.post(
        "/api/v1/orders", json={"listing_id": "ghost"}, headers={**_auth("b"), **_idem("k")}
    ).status_code == 404


# ----------------------------- pagination (same-second keyset regression) -----------------------------

def test_my_orders_keyset_paginates_same_second_rows(client):
    """Three orders created within the same wall-clock second must still paginate without
    re-emitting a boundary row. Regression for the SQLite second-precision cursor bug: with a
    server_default-only timestamp, (created_at, id) < (anchor) short-circuits on the equal
    timestamp and the id tiebreak never engages. The Python-side utcnow default fixes it."""
    buyer = _auth("buyer-pg")
    for i in range(3):
        lid = _seed_listing(client, f"seller-pg{i}", mode="fixed")
        r = client.post("/api/v1/orders", json={"listing_id": lid},
                        headers={**buyer, **_idem(f"k{i}")})
        assert r.status_code == 201

    p1 = client.get("/api/v1/me/orders?limit=2", headers=buyer).json()
    assert len(p1["items"]) == 2 and p1["next_cursor"]
    p2 = client.get(f"/api/v1/me/orders?limit=2&cursor={p1['next_cursor']}", headers=buyer).json()
    assert len(p2["items"]) == 1 and p2["next_cursor"] is None
    ids = {o["id"] for o in p1["items"]} | {o["id"] for o in p2["items"]}
    assert len(ids) == 3  # no overlap, no dropped row


# ----------------------------- auth gate -----------------------------

def test_orders_require_token(client):
    assert client.post("/api/v1/orders", json={"listing_id": "x"}, headers=_idem("k")).status_code == 401
    assert client.get("/api/v1/me/orders").status_code == 401
