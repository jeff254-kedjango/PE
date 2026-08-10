"""Denylist gate on money actions — denied → 403, Redis-down → 503 (fail closed), clean → through.

Touches the real Redis (db /3). Uses a unique sub and scrubs it in teardown so no state leaks.
Non-money endpoints (feed) must remain reachable for a denied user — the gate is money-only.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient

from PE.commerce.core.database import get_db
from PE.commerce.main import app
from PE.commerce.services import denylist

_KEYS = Path(__file__).resolve().parents[3] / "PE" / "dev" / "keys"
_PRIVATE = (_KEYS / "insar_jwt_private.pem").read_text()
_SELLER = ("read:feed", "create:trades")
_LAT, _LNG = -1.2920, 36.8219
_DENIED_SUB = "denylist-test-subject"


def _mint(sub, scopes):
    return jwt.encode(
        {"sub": sub, "role": "user", "scope": "commerce_trade", "scopes": list(scopes),
         "exp": datetime.now(timezone.utc) + timedelta(minutes=10)},
        _PRIVATE, algorithm="RS256",
    )


def _auth(sub, scopes=("read:feed",)):
    return {"Authorization": f"Bearer {_mint(sub, scopes)}"}


@pytest.fixture
def client(db_session):
    app.dependency_overrides.clear()

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _scrub_denylist():
    """Guarantee the test subject is off the denylist before and after each test."""
    try:
        denylist.undeny(_DENIED_SUB)
    except Exception:
        pass
    yield
    try:
        denylist.undeny(_DENIED_SUB)
    except Exception:
        pass


def _seed_listing(client):
    shop = client.post(
        "/api/v1/shops",
        json={"name": "Shop", "lat": _LAT, "lng": _LNG, "display_name": "S"},
        headers=_auth("denylist-seller", _SELLER),
    ).json()
    return client.post(
        f"/api/v1/shops/{shop['id']}/listings",
        json={"title": "X", "price_cents": 1000, "stock_qty": 5, "pricing_mode": "fixed"},
        headers=_auth("denylist-seller", _SELLER),
    ).json()["id"]


def test_denied_subject_blocked_from_money_action(client):
    lid = _seed_listing(client)
    denylist.deny(_DENIED_SUB)
    r = client.post(
        "/api/v1/orders", json={"listing_id": lid},
        headers={**_auth(_DENIED_SUB), "Idempotency-Key": "k1"},
    )
    assert r.status_code == 403


def test_denied_subject_can_still_use_feed(client):
    # The denylist gates MONEY actions only — a denied user can still browse the feed.
    denylist.deny(_DENIED_SUB)
    r = client.get(f"/api/v1/feed?lat={_LAT}&lng={_LNG}", headers=_auth(_DENIED_SUB))
    assert r.status_code == 200


def test_clean_subject_passes(client):
    lid = _seed_listing(client)
    r = client.post(
        "/api/v1/orders", json={"listing_id": lid},
        headers={**_auth("clean-subject"), "Idempotency-Key": "k1"},
    )
    assert r.status_code == 201


def test_redis_down_fails_closed_503(client):
    lid = _seed_listing(client)
    # Simulate Redis unreachable: is_denied raises DenylistUnavailable → money path returns 503.
    with patch.object(
        denylist, "is_denied", side_effect=denylist.DenylistUnavailable("redis down")
    ):
        r = client.post(
            "/api/v1/orders", json={"listing_id": lid},
            headers={**_auth("any-subject"), "Idempotency-Key": "k1"},
        )
    assert r.status_code == 503  # fail closed — never admit on an unreachable denylist
