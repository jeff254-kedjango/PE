"""Local reviews / ratings (§8) — proof-of-purchase gate, aggregate, authorization, reads.

Real RS256 tokens. A review is writable ONLY by the buyer of a SETTLED order, once per order.
Mirrors test_receipts.py's settle helper. Unique subs per test so state can't bleed.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from PE.commerce.core.database import get_db
from PE.commerce.main import app

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
    return li["id"], shop["seller_id"]


def _settled_order(client, seller_sub, buyer_sub, *, price=10000, tag="x"):
    """Open + settle a fixed-price order; return (order_id, seller_id, buyer_headers)."""
    lid, seller_id = _seed_listing(client, seller_sub, price=price, mode="fixed")
    buyer = _auth(buyer_sub)
    oid = client.post("/api/v1/orders", json={"listing_id": lid},
                      headers={**buyer, **_idem(f"open-{tag}")}).json()["id"]
    r = client.post(f"/api/v1/orders/{oid}/settle", headers={**buyer, **_idem(f"settle-{tag}")})
    assert r.status_code == 200 and r.json()["status"] == "SETTLED", r.text
    return oid, seller_id, buyer


# ----------------------------- the proof-of-purchase gate -----------------------------

def test_buyer_can_review_settled_order(client):
    oid, seller_id, buyer = _settled_order(client, "seller-rv", "buyer-rv", tag="rv")
    r = client.post(f"/api/v1/orders/{oid}/review",
                    json={"rating": 5, "body": "Great neighbour seller"}, headers=buyer)
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["rating"] == 5 and rec["seller_id"] == seller_id and rec["order_id"] == oid


def test_cannot_review_before_settle(client):
    # A locked-but-unsettled order is not reviewable → 409.
    lid, _ = _seed_listing(client, "seller-nb", mode="fixed")
    buyer = _auth("buyer-nb")
    oid = client.post("/api/v1/orders", json={"listing_id": lid},
                      headers={**buyer, **_idem("open")}).json()["id"]
    r = client.post(f"/api/v1/orders/{oid}/review", json={"rating": 4}, headers=buyer)
    assert r.status_code == 409


def test_seller_cannot_review_own_sale(client):
    oid, _, _ = _settled_order(client, "seller-own", "buyer-own", tag="own")
    # The seller of the order tries to review it → 403 (their identity is on the order).
    r = client.post(f"/api/v1/orders/{oid}/review", json={"rating": 5},
                    headers=_auth("seller-own", _SELLER))
    assert r.status_code == 403


def test_non_party_cannot_review_and_no_leak(client):
    oid, _, _ = _settled_order(client, "seller-np", "buyer-np", tag="np")
    # A stranger (neither buyer nor seller) → 404, no existence leak.
    r = client.post(f"/api/v1/orders/{oid}/review", json={"rating": 1},
                    headers=_auth("stranger-np"))
    assert r.status_code == 404


def test_one_review_per_order(client):
    oid, _, buyer = _settled_order(client, "seller-1r", "buyer-1r", tag="1r")
    assert client.post(f"/api/v1/orders/{oid}/review", json={"rating": 5},
                       headers=buyer).status_code == 201
    # second review of the same order → 409
    assert client.post(f"/api/v1/orders/{oid}/review", json={"rating": 1},
                       headers=buyer).status_code == 409


def test_review_unknown_order_404(client):
    assert client.post("/api/v1/orders/ghost/review", json={"rating": 5},
                       headers=_auth("b-uk")).status_code == 404


@pytest.mark.parametrize("rating", [0, 6, -1, 99])
def test_rating_out_of_range_rejected(client, rating):
    oid, _, buyer = _settled_order(client, f"seller-rr{rating}", f"buyer-rr{rating}", tag=f"rr{rating}")
    # Out-of-range rating is a 422 at the schema boundary (defended again by the service + CHECK).
    assert client.post(f"/api/v1/orders/{oid}/review", json={"rating": rating},
                       headers=buyer).status_code == 422


def test_reviews_require_token(client):
    assert client.post("/api/v1/orders/x/review", json={"rating": 5}).status_code == 401
    assert client.get("/api/v1/sellers/s/reviews").status_code == 401


# ----------------------------- aggregate + reads -----------------------------

def test_seller_rating_aggregate_and_list(client):
    # One seller, two buyers, two settled orders → two reviews (5 and 3) → avg 4.0, count 2.
    seller = "seller-agg"
    oid1, seller_id, b1 = _settled_order(client, seller, "buyer-a1", tag="a1")
    # second buyer on the SAME seller: seed another listing under that seller, settle, review.
    lid2, _ = _seed_listing(client, seller, mode="fixed")
    b2 = _auth("buyer-a2")
    oid2 = client.post("/api/v1/orders", json={"listing_id": lid2},
                       headers={**b2, **_idem("open-a2")}).json()["id"]
    client.post(f"/api/v1/orders/{oid2}/settle", headers={**b2, **_idem("settle-a2")})

    client.post(f"/api/v1/orders/{oid1}/review", json={"rating": 5}, headers=b1)
    client.post(f"/api/v1/orders/{oid2}/review", json={"rating": 3}, headers=b2)

    page = client.get(f"/api/v1/sellers/{seller_id}/reviews", headers=b1).json()
    assert page["summary"]["count"] == 2
    assert page["summary"]["average"] == 4.0
    assert len(page["items"]) == 2


def test_unrated_seller_is_empty_summary(client):
    # A seller that exists but has no reviews → count 0, average None, empty list (not 404).
    _, seller_id, buyer = _settled_order(client, "seller-ur", "buyer-ur", tag="ur")
    page = client.get(f"/api/v1/sellers/{seller_id}/reviews", headers=buyer).json()
    assert page["summary"] == {"average": None, "count": 0}
    assert page["items"] == []


def test_feed_surfaces_seller_rating(client):
    # After a buyer reviews a settled order, the seller's OTHER in-stock listing shows the
    # seller_rating on the proximity feed (display-only, batch aggregate). An unrated seller's
    # listing shows null. Both sellers' shops sit at the feed query point.
    seller = "seller-feed"
    oid, _, b = _settled_order(client, seller, "buyer-feed", price=10000, tag="feed")
    client.post(f"/api/v1/orders/{oid}/review", json={"rating": 5}, headers=b)
    # A second, in-stock listing for the SAME seller so it appears on the feed (the reviewed
    # order's listing still has stock too, but assert via the seller, not a specific listing).
    _seed_listing(client, seller, mode="fixed")
    # An unrated different seller, same location.
    _seed_listing(client, "seller-feed-unr", mode="fixed")

    resp = client.get(f"/api/v1/feed?lat={_LAT}&lng={_LNG}&radius_m=2000", headers=b)
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    rated = [i for i in items if i["seller_id"] != "" and i["seller_rating"] is not None]
    assert rated, "expected at least one item carrying the seller rating"
    # Every item for the reviewed seller reports avg 5.0 / count 1; the unrated seller reports null.
    by_seller = {}
    for i in items:
        by_seller.setdefault(i["seller_id"], (i["seller_rating"], i["seller_review_count"]))
    assert (5.0, 1) in by_seller.values()
    assert (None, 0) in by_seller.values()  # the unrated seller


def test_seller_reviews_pagination_keyset(client):
    seller = "seller-pg"
    oids, buyers = [], []
    for i in range(3):
        oid, seller_id, b = _settled_order(client, seller, f"buyer-pg{i}", tag=f"pg{i}")
        client.post(f"/api/v1/orders/{oid}/review", json={"rating": 4}, headers=b)
        oids.append(oid)
        buyers.append(b)

    viewer = buyers[0]
    p1 = client.get(f"/api/v1/sellers/{seller_id}/reviews?limit=2", headers=viewer).json()
    assert len(p1["items"]) == 2 and p1["next_cursor"]
    p2 = client.get(f"/api/v1/sellers/{seller_id}/reviews?limit=2&cursor={p1['next_cursor']}",
                    headers=viewer).json()
    assert len(p2["items"]) == 1 and p2["next_cursor"] is None
    ids = {x["id"] for x in p1["items"]} | {x["id"] for x in p2["items"]}
    assert len(ids) == 3  # no overlap across pages
