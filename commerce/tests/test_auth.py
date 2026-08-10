"""Auth verification: real RS256 tokens signed with the dev private key.

Proves the three load-bearing properties:
  - missing token → 401
  - correctly-signed token of the WRONG scope (insar_telemetry) → 401 (scope confusion, S2)
  - valid commerce_trade token → 200
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from PE.commerce.core.auth import get_current_principal
from PE.commerce.core.config import settings
from PE.commerce.main import app

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


@pytest.fixture
def raw_client():
    """A client WITHOUT the principal override, so real token verification runs.
    Auth needs a valid /feed call, which needs a DB — but auth fails before the DB is
    touched, so an unconfigured get_db is fine for the 401 cases; the 200 case stubs it."""
    app.dependency_overrides.clear()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def test_missing_token_401(raw_client):
    resp = raw_client.get("/api/v1/feed?lat=-1.29&lng=36.82")
    assert resp.status_code == 401


def test_wrong_scope_rejected(raw_client):
    # A correctly-signed telemetry token must not authenticate commerce (scope confusion).
    token = _mint(scope="insar_telemetry", scopes=())
    resp = raw_client.get(
        "/api/v1/feed?lat=-1.29&lng=36.82", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 401


def test_expired_token_rejected(raw_client):
    token = _mint(exp_min=-5)
    resp = raw_client.get(
        "/api/v1/feed?lat=-1.29&lng=36.82", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 401


def test_valid_commerce_token_authenticates(db_session, raw_client):
    # Stub the DB so the request reaches the handler; a valid token should yield 200.
    from PE.commerce.core.database import get_db

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    token = _mint()
    resp = raw_client.get(
        "/api/v1/feed?lat=-1.29&lng=36.82", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_auth_enabled_in_tests():
    # The dev public key is wired, so the verifier is live (not inert).
    assert settings.auth_enabled is True
