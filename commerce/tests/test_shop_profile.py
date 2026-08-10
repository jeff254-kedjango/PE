"""Shop profile hovercard (§8) + follow ("Notify") — published business card, follower count,
per-viewer follow state, and the idempotent follow toggle.

Real RS256 tokens so the audience-scope gate + per-user identity (token sub) run end to end.
Viewing a profile / following is a BUYER action (read:feed audience, NOT create:trades); a seller
token is used only to seed the shop with its business card.
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


def _auth(sub, scopes=_BUYER_SCOPES, name=None):
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
        "description": "Fresh produce daily.", "contact": "0712 000 000",
    }
    body.update(over)
    return client.post("/api/v1/shops", json=body, headers=_auth(sub, _SELLER_SCOPES))


# --------------------------- create stores the business card ---------------------------

def test_create_shop_stores_description_and_contact(client):
    shop = _make_shop(client).json()
    assert shop["description"] == "Fresh produce daily."
    assert shop["contact"] == "0712 000 000"


def test_blank_business_card_fields_store_as_null(client):
    shop = _make_shop(client, description="   ", contact="").json()
    assert shop["description"] is None
    assert shop["contact"] is None


def test_description_over_200_words_rejected(client):
    long_blurb = " ".join(["word"] * 201)
    r = _make_shop(client, description=long_blurb)
    assert r.status_code == 422, r.text


def test_description_exactly_200_words_accepted(client):
    blurb = " ".join(["word"] * 200)
    r = _make_shop(client, description=blurb)
    assert r.status_code == 201, r.text


# --------------------------- profile card ---------------------------

def test_profile_returns_published_card_and_zero_state(client):
    shop = _make_shop(client).json()
    prof = client.get(f"/api/v1/shops/{shop['id']}/profile", headers=_auth("buyer-1")).json()
    assert prof["shop_id"] == shop["id"]
    assert prof["seller_id"] == shop["seller_id"]
    assert prof["name"] == "Mama Njeri Groceries"
    assert prof["description"] == "Fresh produce daily."
    assert prof["contact"] == "0712 000 000"
    # No followers, this viewer doesn't follow, unrated.
    assert prof["follower_count"] == 0
    assert prof["following"] is False
    assert prof["rating"] is None
    assert prof["review_count"] == 0


def test_profile_404_for_unknown_shop(client):
    r = client.get("/api/v1/shops/does-not-exist/profile", headers=_auth("buyer-1"))
    assert r.status_code == 404


def test_profile_requires_auth(client):
    shop = _make_shop(client).json()
    assert client.get(f"/api/v1/shops/{shop['id']}/profile").status_code in (401, 403)


# --------------------------- follow ("Notify") toggle ---------------------------

def test_follow_toggle_and_idempotency(client):
    shop = _make_shop(client).json()
    sid = shop["id"]
    buyer = _auth("buyer-1")

    # Follow on.
    r = client.post(f"/api/v1/shops/{sid}/follow", headers=buyer).json()
    assert r["following"] is True and r["follower_count"] == 1

    # The profile now reflects it for this viewer.
    prof = client.get(f"/api/v1/shops/{sid}/profile", headers=buyer).json()
    assert prof["following"] is True and prof["follower_count"] == 1

    # Toggle off.
    r = client.post(f"/api/v1/shops/{sid}/follow", headers=buyer).json()
    assert r["following"] is False and r["follower_count"] == 0


def test_follower_count_distinct_users(client):
    shop = _make_shop(client).json()
    sid = shop["id"]
    client.post(f"/api/v1/shops/{sid}/follow", headers=_auth("buyer-1"))
    r = client.post(f"/api/v1/shops/{sid}/follow", headers=_auth("buyer-2")).json()
    assert r["follower_count"] == 2
    # Each viewer sees only their OWN follow state.
    p1 = client.get(f"/api/v1/shops/{sid}/profile", headers=_auth("buyer-1")).json()
    p3 = client.get(f"/api/v1/shops/{sid}/profile", headers=_auth("buyer-3")).json()
    assert p1["following"] is True and p1["follower_count"] == 2
    assert p3["following"] is False and p3["follower_count"] == 2


def test_follow_404_for_unknown_shop(client):
    r = client.post("/api/v1/shops/does-not-exist/follow", headers=_auth("buyer-1"))
    assert r.status_code == 404


def test_follow_does_not_need_seller_scope(client):
    # A pure buyer token (no create:trades) can follow — it's an audience action.
    shop = _make_shop(client).json()
    r = client.post(f"/api/v1/shops/{shop['id']}/follow", headers=_auth("buyer-1", _BUYER_SCOPES))
    assert r.status_code == 200, r.text


# --------------------------- personal (auto-provisioned) shop ---------------------------

def test_personal_timeline_shop_has_null_card(client):
    # A plain post auto-provisions a personal shop with no published business card.
    poster = _auth("poster-1", _SELLER_SCOPES)
    post = client.post(
        "/api/v1/posts",
        json={"body": "Hello street!", "lat": _LAT, "lng": _LNG},
        headers=poster,
    ).json()
    prof = client.get(f"/api/v1/shops/{post['shop_id']}/profile", headers=_auth("buyer-1")).json()
    assert prof["description"] is None
    assert prof["contact"] is None
    assert prof["name"]  # the personal shop still has a name
