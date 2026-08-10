"""Commerce session-token bridge: the cross-service security boundary.

The commerce token authenticates the SEPARATE commerce service (:8003). These tests pin the
two load-bearing properties of the weespas side:
  1. A commerce-scoped token must NEVER authenticate a weespas endpoint (scope confusion) —
     get_current_user and get_current_user_optional both reject it.
  2. /commerce/session-token mints a token only for a signed-in user.
"""
from datetime import datetime, timedelta, timezone

from jose import jwt
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

from PE.weespas.core.config import settings
from PE.weespas.main import app
from PE.weespas.services import auth_service


def _commerce_token(user_id="user-1", role="user"):
    return auth_service.create_commerce_token(user_id, role, ["read:feed"])


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


# 1. Security boundary — a commerce token is inert against weespas
def test_commerce_token_rejected_by_get_current_user():
    """A commerce token must NOT authenticate /policy/me (PII) — 401."""
    with TestClient(app) as c:
        r = c.get("/api/v1/policy/me", headers=_bearer(_commerce_token()))
    assert r.status_code == 401


def test_commerce_token_cannot_spend_money():
    """A commerce token must NOT reach /reveal (spends M-Pesa) — 401."""
    with TestClient(app) as c:
        r = c.post("/api/v1/reveal/some-listing", headers=_bearer(_commerce_token()))
    assert r.status_code == 401


def test_commerce_token_rejected_by_optional_auth_returns_none():
    """get_current_user_optional treats a commerce token as anonymous (None)."""
    from PE.weespas.services.auth_service import get_current_user_optional

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=_commerce_token())
    assert get_current_user_optional(credentials=creds, db=None) is None


# 2. Token shape — claims carry the audience scope + granular permissions
def test_commerce_token_claims():
    token = _commerce_token("u-42", "agent")
    # Decode without verification just to assert claim shape (signing is exercised elsewhere).
    payload = jwt.get_unverified_claims(token)
    assert payload["sub"] == "u-42"
    assert payload["role"] == "agent"
    assert payload["scope"] == auth_service.COMMERCE_TRADE_SCOPE
    assert "read:feed" in payload["scopes"]
    assert "exp" in payload


def test_commerce_token_carries_name_claim_when_provided():
    """The caller's display name rides as a `name` claim so commerce can snapshot it onto
    comments/inquiries (it owns no identity). Absent when no name is passed — never fabricated."""
    with_name = auth_service.create_commerce_token("u-7", "user", ["read:feed"], name="Asha Kimani")
    assert jwt.get_unverified_claims(with_name)["name"] == "Asha Kimani"
    without = auth_service.create_commerce_token("u-7", "user", ["read:feed"])
    assert "name" not in jwt.get_unverified_claims(without)


def test_session_token_requires_login():
    """/commerce/session-token is gated by get_current_user — anonymous → 401/403."""
    with TestClient(app) as c:
        r = c.get("/api/v1/commerce/session-token")
    assert r.status_code in (401, 403)
