"""§8 ephemerality — the "selling now" promotion slice, driven end to end.

Covers the two seller-chosen expiry modes and the security/abuse boundaries:
  * EVERGREEN — a live window boosts the listing in the feed (is_promoted=True); when the window
    expires the boost just fades but the listing STAYS visible (it is an ordinary in-stock item).
  * STORY     — a live window shows the post; when it expires the post DISAPPEARS from the feed
    while the listing + stock remain untouched (a later re-promote/clear brings it back).
  * duration bounds are enforced (a 0/blip or an indefinite window is a 422 — anti-abuse);
  * an unknown mode is a 422 at the schema edge;
  * promote/clear are owner-only (a cross-owner target is 404, no existence leak);
  * clear is idempotent and the feed boost decays with NO write (pure function of the window).

The feed/owner views read the SAME promo state (services.ranking.promo_boost), so we assert the
buyer feed and the seller's own storefront agree.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from PE.commerce.core.config import settings
from PE.commerce.core.database import get_db
from PE.commerce.main import app
from PE.commerce.models.listing import Listing

_KEYS = Path(__file__).resolve().parents[3] / "PE" / "dev" / "keys"
_PRIVATE = (_KEYS / "insar_jwt_private.pem").read_text()

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


def _seller_auth(sub="seller-A"):
    return {"Authorization": f"Bearer {_mint(sub, ('read:feed', 'create:trades'))}"}


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


def _feed_items(client):
    r = client.get(f"/api/v1/feed?lat={_LAT}&lng={_LNG}", headers=_buyer_auth())
    assert r.status_code == 200, r.text
    return {item["title"]: item for item in r.json()["items"]}


def _seed_listing(client, *, title="Sukuma 1 bunch", stock=5, sub="seller-A"):
    shop = client.post(
        "/api/v1/shops",
        json={"name": "Corner Shop", "lat": _LAT, "lng": _LNG, "display_name": "A"},
        headers=_seller_auth(sub),
    ).json()
    li = client.post(
        f"/api/v1/shops/{shop['id']}/listings",
        json={"title": title, "price_cents": 2000, "stock_qty": stock},
        headers=_seller_auth(sub),
    ).json()
    return li["id"]


def _backdate_window(db_session, listing_id, *, started_ago_s, expires_in_s):
    """Move a listing's promo window relative to now WITHOUT going through the API — so we can
    test the expired/elapsed states deterministically (the boost is a pure function of the window
    vs now, no sweep). Negative ``expires_in_s`` puts expiry in the past (an expired window)."""
    now = datetime.now(timezone.utc)
    li = db_session.get(Listing, listing_id)
    li.promo_started_at = now - timedelta(seconds=started_ago_s)
    li.promo_expires_at = now + timedelta(seconds=expires_in_s)
    db_session.commit()


# ----------------------------- promote: happy path -----------------------------

def test_promote_marks_listing_promoted_in_feed_and_owner_view(client):
    lid = _seed_listing(client)
    r = client.post(
        f"/api/v1/listings/{lid}/promote",
        json={"mode": "evergreen", "duration_seconds": 3600},
        headers=_seller_auth(),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["promo_mode"] == "evergreen"
    assert body["is_promoted"] is True
    assert body["promo_expires_at"] is not None

    # Buyer feed reflects the live window.
    assert _feed_items(client)["Sukuma 1 bunch"]["is_promoted"] is True

    # Seller's own storefront agrees (same source of truth).
    store = client.get("/api/v1/shops/mine", headers=_seller_auth()).json()
    own = store["shops"][0]["listings"][0]
    assert own["is_promoted"] is True and own["promo_mode"] == "evergreen"


def test_promoted_listing_outranks_identical_unpromoted_neighbour(client):
    # Two listings at the SAME location; the promoted one must sort first (additive boost).
    _seed_listing(client, title="Plain")
    promoted = _seed_listing(client, title="Boosted")
    client.post(
        f"/api/v1/listings/{promoted}/promote",
        json={"mode": "evergreen", "duration_seconds": 3600},
        headers=_seller_auth(),
    )
    r = client.get(f"/api/v1/feed?lat={_LAT}&lng={_LNG}", headers=_buyer_auth())
    titles = [it["title"] for it in r.json()["items"]]
    assert titles.index("Boosted") < titles.index("Plain")


# ----------------------------- expiry semantics -----------------------------

def test_evergreen_stays_visible_after_expiry_but_unboosted(client, db_session):
    lid = _seed_listing(client)
    client.post(
        f"/api/v1/listings/{lid}/promote",
        json={"mode": "evergreen", "duration_seconds": 3600},
        headers=_seller_auth(),
    )
    # Force the window fully into the past.
    _backdate_window(db_session, lid, started_ago_s=7200, expires_in_s=-1)
    item = _feed_items(client)["Sukuma 1 bunch"]
    assert item is not None              # evergreen: still in the feed
    assert item["is_promoted"] is False  # but the boost has faded


def test_story_disappears_from_feed_after_expiry_but_listing_and_stock_survive(client, db_session):
    lid = _seed_listing(client, stock=5)
    client.post(
        f"/api/v1/listings/{lid}/promote",
        json={"mode": "story", "duration_seconds": 3600},
        headers=_seller_auth(),
    )
    assert "Sukuma 1 bunch" in _feed_items(client)  # live story → visible

    _backdate_window(db_session, lid, started_ago_s=7200, expires_in_s=-1)
    assert "Sukuma 1 bunch" not in _feed_items(client)  # expired story → gone from feed

    # Listing + stock untouched: the seller still owns it and can clear/re-promote it.
    li = db_session.get(Listing, lid)
    assert li.is_active is True and li.stock_qty == 5

    # Clearing the (expired) promotion returns it to an ordinary visible listing.
    r = client.delete(f"/api/v1/listings/{lid}/promotion", headers=_seller_auth())
    assert r.status_code == 200 and r.json()["promo_mode"] is None
    assert "Sukuma 1 bunch" in _feed_items(client)


def test_live_story_is_visible(client, db_session):
    lid = _seed_listing(client)
    client.post(
        f"/api/v1/listings/{lid}/promote",
        json={"mode": "story", "duration_seconds": 3600},
        headers=_seller_auth(),
    )
    _backdate_window(db_session, lid, started_ago_s=60, expires_in_s=3600)  # still open
    assert _feed_items(client)["Sukuma 1 bunch"]["is_promoted"] is True


# ----------------------------- clear -----------------------------

def test_clear_promotion_is_idempotent(client):
    lid = _seed_listing(client)
    # Clearing an un-promoted listing is a clean no-op (200, promo_mode None).
    r1 = client.delete(f"/api/v1/listings/{lid}/promotion", headers=_seller_auth())
    assert r1.status_code == 200 and r1.json()["promo_mode"] is None
    # Promote then clear twice — still idempotent.
    client.post(
        f"/api/v1/listings/{lid}/promote",
        json={"mode": "evergreen", "duration_seconds": 3600},
        headers=_seller_auth(),
    )
    assert client.delete(f"/api/v1/listings/{lid}/promotion", headers=_seller_auth()).status_code == 200
    r2 = client.delete(f"/api/v1/listings/{lid}/promotion", headers=_seller_auth())
    assert r2.status_code == 200 and r2.json()["is_promoted"] is False


# ----------------------------- abuse / bounds -----------------------------

def test_duration_below_min_is_rejected(client):
    lid = _seed_listing(client)
    r = client.post(
        f"/api/v1/listings/{lid}/promote",
        json={"mode": "evergreen", "duration_seconds": settings.promo_min_duration_seconds - 1},
        headers=_seller_auth(),
    )
    assert r.status_code == 422


def test_duration_above_max_is_rejected(client):
    lid = _seed_listing(client)
    r = client.post(
        f"/api/v1/listings/{lid}/promote",
        json={"mode": "evergreen", "duration_seconds": settings.promo_max_duration_seconds + 1},
        headers=_seller_auth(),
    )
    assert r.status_code == 422


def test_unknown_mode_is_rejected(client):
    lid = _seed_listing(client)
    r = client.post(
        f"/api/v1/listings/{lid}/promote",
        json={"mode": "spam", "duration_seconds": 3600},
        headers=_seller_auth(),
    )
    assert r.status_code == 422


# ----------------------------- ownership / authz -----------------------------

def test_cannot_promote_another_sellers_listing(client):
    lid = _seed_listing(client, sub="seller-A")
    # seller-B targets seller-A's listing → 404 (no existence leak), and the listing stays clean.
    r = client.post(
        f"/api/v1/listings/{lid}/promote",
        json={"mode": "evergreen", "duration_seconds": 3600},
        headers=_seller_auth("seller-B"),
    )
    assert r.status_code == 404
    assert _feed_items(client)["Sukuma 1 bunch"]["is_promoted"] is False


def test_cannot_clear_another_sellers_promotion(client):
    lid = _seed_listing(client, sub="seller-A")
    client.post(
        f"/api/v1/listings/{lid}/promote",
        json={"mode": "evergreen", "duration_seconds": 3600},
        headers=_seller_auth("seller-A"),
    )
    r = client.delete(f"/api/v1/listings/{lid}/promotion", headers=_seller_auth("seller-B"))
    assert r.status_code == 404
    # seller-A's promotion survived the cross-owner clear attempt.
    assert _feed_items(client)["Sukuma 1 bunch"]["is_promoted"] is True


def test_promote_requires_write_scope(client):
    lid = _seed_listing(client)
    # A read-only buyer token cannot promote.
    r = client.post(
        f"/api/v1/listings/{lid}/promote",
        json={"mode": "evergreen", "duration_seconds": 3600},
        headers=_buyer_auth(),
    )
    assert r.status_code == 403
