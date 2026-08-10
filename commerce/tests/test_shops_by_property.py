"""§8.1a — POST /shops/by-property: the weespas-aggregated "which footprints are shops?" read.

Asserts the contract the map aggregator depends on:
  * present uuids resolve to their shop meta; unknown uuids are simply absent (no fabricated rows);
  * a footprint shared by two shops yields BOTH entries (property_uuid is not unique);
  * the response carries NO lat/lng (S6 — coordinates never leave commerce);
  * the batch is bounded (over-cap input is a 422 at the schema edge, anti-O(n) S8);
  * empty input is a valid empty response (no query);
  * auth fails closed — missing token 401, a wrong-audience (telemetry) token 401.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from PE.commerce.main import app
from PE.commerce.models.seller import Seller, Shop
from PE.commerce.schemas.catalog import SHOPS_BY_PROPERTY_BATCH_MAX
from PE.commerce.services import proximity

_URL = "/api/v1/shops/by-property"

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


def _shop(db, *, name, property_uuid, category=None, lat=-1.29, lng=36.82):
    seller = Seller(user_uuid="seller-" + name, display_name=name)
    db.add(seller)
    db.flush()
    shop = Shop(
        seller_id=seller.id, name=name, property_uuid=property_uuid,
        category=category,
    )
    proximity.set_location(shop, lat, lng)
    db.add(shop)
    db.flush()
    return shop


def test_present_uuids_resolve_to_meta(client, db_session):
    _shop(db_session, name="Corner Shop", property_uuid="bld-1", category="grocery")
    db_session.commit()

    resp = client.post(_URL, json={"property_uuids": ["bld-1"]})
    assert resp.status_code == 200
    shops = resp.json()["shops"]
    assert len(shops) == 1
    s = shops[0]
    assert s["property_uuid"] == "bld-1"
    assert s["name"] == "Corner Shop"
    assert s["category"] == "grocery"
    # avatar_url is NOT part of the map contract — a shop logo is never rendered on a glyph pin
    # or the text-only tooltip, so it must not ride along (no-dead-code / minimal S6 surface).
    assert "avatar_url" not in s


def test_no_latlng_in_response(client, db_session):
    """S6: a shop's raw coordinates must never leave commerce — the footprint is already public."""
    _shop(db_session, name="Corner Shop", property_uuid="bld-1", lat=-1.30, lng=36.83)
    db_session.commit()

    resp = client.post(_URL, json={"property_uuids": ["bld-1"]})
    assert resp.status_code == 200
    s = resp.json()["shops"][0]
    assert "lat" not in s and "lng" not in s


def test_unknown_uuid_absent(client, db_session):
    _shop(db_session, name="Corner Shop", property_uuid="bld-1")
    db_session.commit()

    resp = client.post(_URL, json={"property_uuids": ["bld-1", "bld-does-not-exist"]})
    assert resp.status_code == 200
    returned = {s["property_uuid"] for s in resp.json()["shops"]}
    assert returned == {"bld-1"}  # the unknown uuid is silently omitted, not fabricated


def test_shared_footprint_yields_both_shops(client, db_session):
    """property_uuid is NOT unique — two shops on one building must both come back (no collapse)."""
    _shop(db_session, name="Shop A", property_uuid="bld-shared")
    _shop(db_session, name="Shop B", property_uuid="bld-shared")
    db_session.commit()

    resp = client.post(_URL, json={"property_uuids": ["bld-shared"]})
    assert resp.status_code == 200
    names = sorted(s["name"] for s in resp.json()["shops"])
    assert names == ["Shop A", "Shop B"]


def test_duplicate_input_uuid_not_inflated(client, db_session):
    """A caller repeating a uuid must not inflate the result — one shop, one entry."""
    _shop(db_session, name="Corner Shop", property_uuid="bld-1")
    db_session.commit()

    resp = client.post(_URL, json={"property_uuids": ["bld-1", "bld-1", "bld-1"]})
    assert resp.status_code == 200
    assert len(resp.json()["shops"]) == 1


def test_empty_input_is_empty_response(client, db_session):
    resp = client.post(_URL, json={"property_uuids": []})
    assert resp.status_code == 200
    assert resp.json()["shops"] == []


def test_over_cap_batch_rejected(client):
    """Anti-O(n) (S8): a batch beyond the cap is a 422 at the schema edge, before any query."""
    too_many = [f"bld-{i}" for i in range(SHOPS_BY_PROPERTY_BATCH_MAX + 1)]
    resp = client.post(_URL, json={"property_uuids": too_many})
    assert resp.status_code == 422


# --- auth (real token verification, no principal override) ------------------------------

@pytest.fixture
def raw_client():
    app.dependency_overrides.clear()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def test_missing_token_401(raw_client):
    resp = raw_client.post(_URL, json={"property_uuids": ["bld-1"]})
    assert resp.status_code == 401


def test_wrong_audience_token_rejected(raw_client):
    # A correctly-signed telemetry token must not authenticate commerce (scope confusion, S2).
    token = _mint(scope="insar_telemetry", scopes=())
    resp = raw_client.post(
        _URL, json={"property_uuids": ["bld-1"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401
