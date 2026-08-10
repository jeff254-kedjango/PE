"""Digital receipts (§8) — issuance on settle, money split, hash binding, authorization, reads.

Real RS256 tokens (the receipt endpoints run the real Redis denylist, fail-closed); actors are
kept OFF the denylist. Unique subs per test so state can't bleed. Mirrors test_settlement.py.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from PE.commerce.core.database import get_db
from PE.commerce.main import app
from PE.commerce.services import receipts, settlement

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


def _settle_fixed(client, seller_sub, buyer_sub, *, price=10000):
    """Open a fixed-price order and settle it; return (order_id, buyer_headers)."""
    lid = _seed_listing(client, seller_sub, price=price, mode="fixed")
    buyer = _auth(buyer_sub)
    oid = client.post("/api/v1/orders", json={"listing_id": lid},
                      headers={**buyer, **_idem(f"open-{buyer_sub}")}).json()["id"]
    r = client.post(f"/api/v1/orders/{oid}/settle",
                    headers={**buyer, **_idem(f"settle-{buyer_sub}")})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "SETTLED"
    return oid, buyer


# ----------------------------- issuance -----------------------------

def test_receipt_issued_on_settle(client):
    oid, buyer = _settle_fixed(client, "seller-ri", "buyer-ri", price=10000)
    r = client.get(f"/api/v1/orders/{oid}/receipt", headers=buyer)
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["order_id"] == oid
    assert rec["listing_title"] == "Thing"   # frozen snapshot
    assert rec["currency"] == "KES"


def test_receipt_money_split(client):
    # gross = locked price; commission = 3%; net = gross - commission.
    oid, buyer = _settle_fixed(client, "seller-rm", "buyer-rm", price=10000)
    rec = client.get(f"/api/v1/orders/{oid}/receipt", headers=buyer).json()
    assert rec["gross_cents"] == 10000
    assert rec["commission_cents"] == 300
    assert rec["net_to_seller_cents"] == 9700
    assert rec["gross_cents"] == rec["commission_cents"] + rec["net_to_seller_cents"]


def test_receipt_hash_binds_to_settle_ok_chain_tip(client):
    oid, buyer = _settle_fixed(client, "seller-rh", "buyer-rh", price=5000)
    detail = client.get(f"/api/v1/orders/{oid}", headers=buyer).json()
    settle_ok = next(e for e in detail["events"] if e["event_type"] == "settle_ok")
    rec = client.get(f"/api/v1/orders/{oid}/receipt", headers=buyer).json()
    # The receipt's chain tip IS the settle_ok event's row_hash — binds it to the §7 chain.
    assert rec["chain_tip_hash"] == settle_ok["row_hash"]
    # And the receipt_hash recomputes from the snapshot + that tip (tamper-evident).
    expected = receipts._receipt_hash(
        order_id=rec["order_id"], buyer_uuid=rec["buyer_uuid"], seller_id=rec["seller_id"],
        listing_id=rec["listing_id"], gross_cents=rec["gross_cents"],
        commission_cents=rec["commission_cents"], net_to_seller_cents=rec["net_to_seller_cents"],
        rail_ref=rec["rail_ref"], chain_tip_hash=rec["chain_tip_hash"],
    )
    assert rec["receipt_hash"] == expected


def test_no_receipt_before_settle(client):
    # A locked-but-unsettled order has no receipt → 404 (uniform, no "not yet" leak).
    lid = _seed_listing(client, "seller-nb", price=10000, mode="fixed")
    buyer = _auth("buyer-nb")
    oid = client.post("/api/v1/orders", json={"listing_id": lid},
                      headers={**buyer, **_idem("open")}).json()["id"]
    assert client.get(f"/api/v1/orders/{oid}/receipt", headers=buyer).status_code == 404


def test_resettle_does_not_double_issue(client):
    # A replayed settle (same idem key) must not mint a second receipt.
    oid, buyer = _settle_fixed(client, "seller-rd", "buyer-rd", price=10000)
    r2 = client.post(f"/api/v1/orders/{oid}/settle",
                     headers={**buyer, **_idem("settle-buyer-rd")})
    assert r2.status_code == 200  # idempotent replay
    page = client.get("/api/v1/me/receipts", headers=buyer).json()
    assert len([x for x in page["items"] if x["order_id"] == oid]) == 1


# ----------------------------- authorization -----------------------------

def test_seller_can_view_receipt(client):
    oid, _ = _settle_fixed(client, "seller-sv", "buyer-sv", price=10000)
    r = client.get(f"/api/v1/orders/{oid}/receipt", headers=_auth("seller-sv", _SELLER))
    assert r.status_code == 200
    assert r.json()["order_id"] == oid


def test_non_party_cannot_view_receipt(client):
    oid, _ = _settle_fixed(client, "seller-np", "buyer-np", price=10000)
    # A stranger → 404 (no existence leak), same as the order endpoint.
    assert client.get(f"/api/v1/orders/{oid}/receipt",
                      headers=_auth("stranger-np")).status_code == 404


def test_receipt_unknown_order_404(client):
    assert client.get("/api/v1/orders/ghost/receipt",
                      headers=_auth("b-uk")).status_code == 404


def test_receipts_require_token(client):
    assert client.get("/api/v1/orders/x/receipt").status_code == 401
    assert client.get("/api/v1/me/receipts").status_code == 401


# ----------------------------- reads / pagination -----------------------------

def test_my_receipts_buyer_and_seller_views(client):
    # One seller, one buyer, two settled fixed orders on two listings.
    seller, buyer_sub = "seller-mr", "buyer-mr"
    buyer = _auth(buyer_sub)
    for i in range(2):
        lid = _seed_listing(client, seller, price=10000, mode="fixed")
        oid = client.post("/api/v1/orders", json={"listing_id": lid},
                          headers={**buyer, **_idem(f"open-{i}")}).json()["id"]
        client.post(f"/api/v1/orders/{oid}/settle",
                    headers={**buyer, **_idem(f"settle-{i}")})

    buyer_page = client.get("/api/v1/me/receipts?role=buyer", headers=buyer).json()
    assert len(buyer_page["items"]) == 2
    seller_page = client.get("/api/v1/me/receipts?role=seller",
                             headers=_auth(seller, _SELLER)).json()
    assert len(seller_page["items"]) == 2


def test_my_receipts_pagination_keyset(client):
    seller, buyer_sub = "seller-pg", "buyer-pg"
    buyer = _auth(buyer_sub)
    for i in range(3):
        lid = _seed_listing(client, seller, price=10000, mode="fixed")
        oid = client.post("/api/v1/orders", json={"listing_id": lid},
                          headers={**buyer, **_idem(f"open-{i}")}).json()["id"]
        client.post(f"/api/v1/orders/{oid}/settle",
                    headers={**buyer, **_idem(f"settle-{i}")})

    p1 = client.get("/api/v1/me/receipts?limit=2", headers=buyer).json()
    assert len(p1["items"]) == 2 and p1["next_cursor"]
    p2 = client.get(f"/api/v1/me/receipts?limit=2&cursor={p1['next_cursor']}",
                    headers=buyer).json()
    assert len(p2["items"]) == 1 and p2["next_cursor"] is None
    # no overlap across pages
    ids = {x["id"] for x in p1["items"]} | {x["id"] for x in p2["items"]}
    assert len(ids) == 3


def test_seller_view_empty_for_buyer_only_actor(client):
    # An actor who only ever bought has no Seller row → seller view is an empty page.
    _settle_fixed(client, "seller-eo", "buyer-eo", price=10000)
    page = client.get("/api/v1/me/receipts?role=seller", headers=_auth("buyer-eo")).json()
    assert page["items"] == []
