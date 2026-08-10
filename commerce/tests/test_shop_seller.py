"""§8.1b — GET /shops/{shop_id}/seller: the shop → owning-seller channel-key read.

The weespas contact uplink calls this to learn which seller's per-user SSE channel to publish
the anonymized pair-radiate pulse to. Asserts the contract the uplink depends on:
  * a known shop resolves to its owning seller's weespas user_uuid;
  * an unknown shop_id is a 200 with seller_uuid=null (NOT 404) — the uplink degrades to
    buyer-local glow rather than treating a stale pin as an error;
  * ONLY the seller_uuid crosses (S6 — no shop meta, no coordinates, no buyer data);
  * auth fails closed — missing token 401, a wrong-audience (telemetry) token 401.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from PE.commerce.main import app
from PE.commerce.models.seller import Seller, Shop
from PE.commerce.services import proximity


def _url(shop_id: str) -> str:
    return f"/api/v1/shops/{shop_id}/seller"


_KEYS = Path(__file__).resolve().parents[3] / "PE" / "dev" / "keys"
_PRIVATE = (_KEYS / "insar_jwt_private.pem").read_text()


def _mint(scope="commerce_trade", scopes=("read:feed",), sub="u1", exp_min=10):
    payload = {
        "sub": sub,
        "role": "user",
        "scope": scope,
        "scopes": list(scopes),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=exp_min),
    }
    return jwt.encode(payload, _PRIVATE, algorithm="RS256")


def _shop(db, *, name, seller_user_uuid, lat=-1.29, lng=36.82) -> Shop:
    seller = Seller(user_uuid=seller_user_uuid, display_name=name)
    db.add(seller)
    db.flush()
    shop = Shop(seller_id=seller.id, name=name)
    proximity.set_location(shop, lat, lng)
    db.add(shop)
    db.flush()
    return shop


def test_known_shop_resolves_to_seller_uuid(client, db_session):
    shop = _shop(db_session, name="Corner Shop", seller_user_uuid="weespas-user-42")
    db_session.commit()

    resp = client.get(_url(shop.id))
    assert resp.status_code == 200
    body = resp.json()
    assert body["shop_id"] == shop.id
    assert body["seller_uuid"] == "weespas-user-42"


def test_unknown_shop_is_200_null(client, db_session):
    """A stale/unknown shop_id degrades to seller_uuid=null (200), never a 404 — the uplink then
    skips the publish and the buyer still glows locally."""
    resp = client.get(_url("does-not-exist"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["shop_id"] == "does-not-exist"
    assert body["seller_uuid"] is None


def test_only_seller_uuid_crosses(client, db_session):
    """S6: no shop meta / coordinates / buyer data may ride along — exactly two keys, one of them
    the seller's already-synchronized weespas identity."""
    shop = _shop(db_session, name="Corner Shop", seller_user_uuid="u-9", lat=-1.30, lng=36.83)
    db_session.commit()

    body = client.get(_url(shop.id)).json()
    assert set(body.keys()) == {"shop_id", "seller_uuid"}
    assert "lat" not in body and "lng" not in body and "name" not in body


# --- auth (real token verification, no principal override) ------------------------------

@pytest.fixture
def raw_client():
    app.dependency_overrides.clear()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def test_missing_token_401(raw_client):
    resp = raw_client.get(_url("any"))
    assert resp.status_code == 401


def test_wrong_audience_token_rejected(raw_client):
    # A correctly-signed telemetry token must not authenticate commerce (scope confusion, S2).
    token = _mint(scope="insar_telemetry", scopes=())
    resp = raw_client.get(_url("any"), headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
