"""Handle claim + resolve endpoints (§8 storefront: /shop/<handle>).

Covers the three new routes in routers/sellers.py end-to-end (real RS256 tokens, real DB,
real auth gate — mirrors test_shop_profile.py's style):

  * PATCH /shops/{shop_id}/handle       — one-shot claim, idempotent same-value, 409 on
                                           locked/taken, 422 on invalid, 404 on cross-owner.
  * GET   /shops/handle-available       — live probe: available OR {available:false, reason}.
  * GET   /shops/@{handle}/storefront   — resolver + public storefront in one round-trip.

Every failure mode named in the router docstring gets a specific test so a future edit that
breaks a mode fails a named test (not a fuzzy "something changed")."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from PE.commerce.core.database import get_db
from PE.commerce.main import app

_KEYS = Path(__file__).resolve().parents[3] / "PE" / "dev" / "keys"
_PRIVATE = (_KEYS / "insar_jwt_private.pem").read_text()

_SELLER_SCOPES = ("read:feed", "create:trades")
_BUYER_SCOPES = ("read:feed",)
_LAT, _LNG = -1.2920, 36.8219


def _mint(sub, scopes, name=None):
    payload = {
        "sub": sub,
        "role": "user",
        "scope": "commerce_trade",
        "scopes": list(scopes),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=10),
    }
    if name is not None:
        payload["name"] = name
    return jwt.encode(payload, _PRIVATE, algorithm="RS256")


def _auth(sub, scopes=_SELLER_SCOPES, name=None):
    return {"Authorization": f"Bearer {_mint(sub, scopes, name)}"}


@pytest.fixture
def client(db_session):
    app.dependency_overrides.clear()

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _make_shop(client, sub="seller-A", **over):
    body = {
        "name": "Mama Njeri Groceries", "lat": _LAT, "lng": _LNG, "display_name": "Mama Njeri",
    }
    body.update(over)
    r = client.post("/api/v1/shops", headers=_auth(sub), json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ================================ PATCH /shops/{shop_id}/handle ================================

class TestClaimHandle:
    def test_claim_new_handle_lowercases_and_persists(self, client):
        shop = _make_shop(client)
        r = client.patch(
            f"/api/v1/shops/{shop['id']}/handle",
            headers=_auth("seller-A"),
            json={"handle": "MamaMboga"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["handle"] == "mamamboga"

    def test_same_value_reclaim_is_idempotent(self, client):
        shop = _make_shop(client)
        client.patch(
            f"/api/v1/shops/{shop['id']}/handle",
            headers=_auth("seller-A"), json={"handle": "nyama-choma"},
        )
        # A second PATCH with the same value — even in mixed case — is a no-op 200.
        r = client.patch(
            f"/api/v1/shops/{shop['id']}/handle",
            headers=_auth("seller-A"), json={"handle": "Nyama-Choma"},
        )
        assert r.status_code == 200
        assert r.json()["handle"] == "nyama-choma"

    def test_different_value_after_claim_is_locked_409(self, client):
        shop = _make_shop(client)
        client.patch(
            f"/api/v1/shops/{shop['id']}/handle",
            headers=_auth("seller-A"), json={"handle": "first-name"},
        )
        # One-shot policy: a rename would break every previously-shared link — refuse.
        r = client.patch(
            f"/api/v1/shops/{shop['id']}/handle",
            headers=_auth("seller-A"), json={"handle": "second-name"},
        )
        assert r.status_code == 409
        assert r.json()["detail"] == "handle-locked"

    def test_case_insensitive_collision_across_sellers_is_409(self, client):
        shop_a = _make_shop(client, sub="seller-A")
        shop_b = _make_shop(client, sub="seller-B", name="Other shop")
        # A claims 'kitengela-butcher'; B tries the SAME name in different case → 409 taken.
        client.patch(
            f"/api/v1/shops/{shop_a['id']}/handle",
            headers=_auth("seller-A"), json={"handle": "kitengela-butcher"},
        )
        r = client.patch(
            f"/api/v1/shops/{shop_b['id']}/handle",
            headers=_auth("seller-B"), json={"handle": "Kitengela-Butcher"},
        )
        assert r.status_code == 409
        assert r.json()["detail"] == "handle-taken"

    def test_invalid_syntax_is_422(self, client):
        shop = _make_shop(client)
        r = client.patch(
            f"/api/v1/shops/{shop['id']}/handle",
            headers=_auth("seller-A"), json={"handle": "mama--mboga"},  # double hyphen
        )
        assert r.status_code == 422
        assert r.json()["detail"] == "handle-syntax"

    def test_reserved_word_is_422(self, client):
        shop = _make_shop(client)
        r = client.patch(
            f"/api/v1/shops/{shop['id']}/handle",
            headers=_auth("seller-A"), json={"handle": "admin"},
        )
        assert r.status_code == 422
        assert r.json()["detail"] == "handle-reserved"

    def test_cross_owner_is_uniform_404_not_403(self, client):
        # Owner-A creates a shop; owner-B tries to PATCH its handle. Must be 404, NOT 403 — a
        # 403 would confirm the shop exists to a cross-owner probe (S6 existence-leak).
        shop = _make_shop(client, sub="seller-A")
        r = client.patch(
            f"/api/v1/shops/{shop['id']}/handle",
            headers=_auth("seller-B"), json={"handle": "sneaky"},
        )
        assert r.status_code == 404

    def test_unknown_shop_is_404(self, client):
        r = client.patch(
            "/api/v1/shops/does-not-exist/handle",
            headers=_auth("seller-A"), json={"handle": "any-name"},
        )
        assert r.status_code == 404


# ============================ GET /shops/handle-available ==============================

class TestHandleAvailable:
    def test_unclaimed_syntactically_valid_handle_is_available(self, client):
        r = client.get(
            "/api/v1/shops/handle-available?handle=fresh-name",
            headers=_auth("buyer-1", scopes=_BUYER_SCOPES),
        )
        assert r.status_code == 200
        body = r.json()
        assert body == {"handle": "fresh-name", "available": True, "reason": None}

    def test_claimed_handle_is_unavailable_with_reason_taken(self, client):
        shop = _make_shop(client)
        client.patch(
            f"/api/v1/shops/{shop['id']}/handle",
            headers=_auth("seller-A"), json={"handle": "already-mine"},
        )
        r = client.get(
            "/api/v1/shops/handle-available?handle=Already-Mine",  # mixed case, same handle
            headers=_auth("buyer-1", scopes=_BUYER_SCOPES),
        )
        body = r.json()
        assert body["available"] is False
        assert body["reason"] == "handle-taken"
        assert body["handle"] == "already-mine"

    def test_syntactically_invalid_handle_returns_reason_not_422(self, client):
        # The whole point of the probe: the frontend calls this on every keystroke, so a syntax
        # error is a normal answer (available:false + reason), never an HTTP 422. This lets the
        # frontend show ONE consistent inline error map regardless of failure type.
        r = client.get(
            "/api/v1/shops/handle-available?handle=Bad--Name",
            headers=_auth("buyer-1", scopes=_BUYER_SCOPES),
        )
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is False
        assert body["reason"] == "handle-syntax"

    def test_reserved_word_reports_reserved(self, client):
        r = client.get(
            "/api/v1/shops/handle-available?handle=admin",
            headers=_auth("buyer-1", scopes=_BUYER_SCOPES),
        )
        body = r.json()
        assert body["available"] is False
        assert body["reason"] == "handle-reserved"

    def test_too_short_reports_length(self, client):
        r = client.get(
            "/api/v1/shops/handle-available?handle=ab",
            headers=_auth("buyer-1", scopes=_BUYER_SCOPES),
        )
        body = r.json()
        assert body["available"] is False
        assert body["reason"] == "handle-length"

    def test_probe_requires_auth(self, client):
        # Commerce fails closed: no public endpoints, not even the probe.
        r = client.get("/api/v1/shops/handle-available?handle=anything")
        assert r.status_code in (401, 403)


# =========================== GET /shops/@{handle}/storefront ============================

class TestStorefrontByHandle:
    def test_by_handle_returns_public_storefront(self, client):
        shop = _make_shop(client)
        client.patch(
            f"/api/v1/shops/{shop['id']}/handle",
            headers=_auth("seller-A"), json={"handle": "mama-mboga"},
        )
        # Any authenticated buyer may view any storefront by handle.
        r = client.get(
            "/api/v1/shops/@mama-mboga/storefront",
            headers=_auth("buyer-1", scopes=_BUYER_SCOPES),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["seller_id"] == shop["seller_id"]
        # Public storefront shape: shops list present (even if empty for a shop with no listings).
        assert "shops" in body
        assert body["rating"] is None  # unrated seller
        assert body["review_count"] == 0

    def test_by_handle_is_case_insensitive(self, client):
        shop = _make_shop(client)
        client.patch(
            f"/api/v1/shops/{shop['id']}/handle",
            headers=_auth("seller-A"), json={"handle": "case-test"},
        )
        # A shared URL that carries mixed case still resolves to the same storefront.
        r = client.get(
            "/api/v1/shops/@Case-TEST/storefront",
            headers=_auth("buyer-1", scopes=_BUYER_SCOPES),
        )
        assert r.status_code == 200
        assert r.json()["seller_id"] == shop["seller_id"]

    def test_unknown_handle_is_404_not_422(self, client):
        # A URL is a URL: an unresolvable handle is 404, not 422 (probing for validity via 422
        # would leak the reserved-word deny-list to any caller with a URL bar).
        r = client.get(
            "/api/v1/shops/@nobody-claimed-this/storefront",
            headers=_auth("buyer-1", scopes=_BUYER_SCOPES),
        )
        assert r.status_code == 404

    def test_grammar_invalid_handle_is_also_404(self, client):
        # e.g. leading hyphen — same 404, no 422 (uniform error, no existence leak).
        r = client.get(
            "/api/v1/shops/@-bad/storefront",
            headers=_auth("buyer-1", scopes=_BUYER_SCOPES),
        )
        assert r.status_code == 404
