"""Stock-aware buyer feed: out-of-stock listings are hidden, restock makes them reappear.

Drives the full write path (create shop → create listing → POS stock adjust) with a real
``create:trades`` token, then reads the buyer feed with a real ``read:feed`` token, asserting
the stock gate end to end.
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

# Buyer and shop share a location so the listing is well within the default radius.
_LAT, _LNG = -1.2920, 36.8219


def _mint(sub, scopes):
    payload = {
        "sub": sub,
        "role": "user",
        "scope": "commerce_trade",
        "scopes": list(scopes),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=10),
    }
    return jwt.encode(payload, _PRIVATE, algorithm="RS256")


def _seller_auth():
    return {"Authorization": f"Bearer {_mint('seller-A', ('read:feed', 'create:trades'))}"}


def _buyer_auth():
    return {"Authorization": f"Bearer {_mint('buyer-Z', ('read:feed',))}"}


@pytest.fixture
def client(db_session):
    app.dependency_overrides.clear()

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _feed_titles(client):
    r = client.get(f"/api/v1/feed?lat={_LAT}&lng={_LNG}", headers=_buyer_auth())
    assert r.status_code == 200, r.text
    return [item["title"] for item in r.json()["items"]]


def _seed_listing(client, *, stock):
    shop = client.post(
        "/api/v1/shops",
        json={"name": "Corner Shop", "lat": _LAT, "lng": _LNG, "display_name": "A"},
        headers=_seller_auth(),
    ).json()
    li = client.post(
        f"/api/v1/shops/{shop['id']}/listings",
        json={"title": "Sukuma 1 bunch", "price_cents": 2000, "stock_qty": stock},
        headers=_seller_auth(),
    ).json()
    return li["id"]


def test_in_stock_listing_appears_in_feed(client):
    _seed_listing(client, stock=4)
    assert "Sukuma 1 bunch" in _feed_titles(client)


def test_out_of_stock_listing_hidden_from_feed(client):
    _seed_listing(client, stock=0)
    assert "Sukuma 1 bunch" not in _feed_titles(client)


def test_selling_last_unit_removes_from_feed_then_restock_returns(client):
    lid = _seed_listing(client, stock=1)
    assert "Sukuma 1 bunch" in _feed_titles(client)  # in stock → visible

    # sell the last unit → 0 → hidden
    client.patch(
        f"/api/v1/listings/{lid}/stock", json={"delta": -1}, headers=_seller_auth()
    )
    assert "Sukuma 1 bunch" not in _feed_titles(client)

    # restock → visible again
    client.patch(
        f"/api/v1/listings/{lid}/stock", json={"stock_qty": 7}, headers=_seller_auth()
    )
    assert "Sukuma 1 bunch" in _feed_titles(client)
