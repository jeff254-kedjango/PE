"""Global trade search (navbar unified search — trade half).

Two layers, mirroring test_trending:
  * direct service-layer unit tests of ``search_trade`` (multi-field match, nearest-first ranking,
    LIKE-metacharacter escaping, visibility rules, min-length backstop, case-insensitivity);
  * HTTP integration through real RS256 tokens (auth gate, lat/lng + query bounds, no-PII shape).

Contract these tests pin:
  * a keyword matches the listing TITLE, listing DESCRIPTION, or owning SHOP NAME (case-insensitive);
  * results are ranked NEAREST-FIRST, nationwide (no radius gate) — a far match still appears, below
    a near one;
  * a sold-out product / inactive listing / expired-story post is NOT a searchable hit (reuses the
    feed visibility rules);
  * user wildcards ``%`` / ``_`` are matched LITERALLY (escaped) — no injection into the LIKE pattern;
  * a query shorter than the min length returns nothing (the server-side backstop);
  * the wire shape carries no PII (only opaque ids + seller-published fields).
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from PE.commerce.core.config import settings
from PE.commerce.core.database import get_db
from PE.commerce.main import app
from PE.commerce.models.listing import PROMO_STORY, Listing
from PE.commerce.models.seller import Seller, Shop
from PE.commerce.services import proximity, search

_KEYS = Path(__file__).resolve().parents[3] / "PE" / "dev" / "keys"
_PRIVATE = (_KEYS / "insar_jwt_private.pem").read_text()

_LAT, _LNG = -1.2920, 36.8219          # Nairobi CBD-ish
_FAR_LAT, _FAR_LNG = -4.0435, 39.6682  # Mombasa — ~440 km away
_NOW = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)


def _mint(sub, scopes):
    return jwt.encode(
        {"sub": sub, "role": "user", "scope": "commerce_trade", "scopes": list(scopes),
         "exp": datetime.now(timezone.utc) + timedelta(minutes=10)},
        _PRIVATE, algorithm="RS256",
    )


def _buyer_auth(sub="buyer-Z"):
    return {"Authorization": f"Bearer {_mint(sub, ('read:feed',))}"}


@pytest.fixture
def client(db_session):
    app.dependency_overrides.clear()

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


# ----------------------------- seeding -----------------------------

def _seed(db, *, user_uuid, title, lat=_LAT, lng=_LNG, shop_name="Shop", category=None,
          description=None, stock=5, is_active=True, media_urls=None, promo_mode=None,
          promo_started_at=None, promo_expires_at=None):
    """One seller→shop→listing. Returns the listing (with its shop/seller attached)."""
    seller = Seller(user_uuid=user_uuid, display_name="S")
    db.add(seller)
    db.flush()
    shop = Shop(seller_id=seller.id, name=shop_name, category=category, lat=lat, lng=lng)
    proximity.set_location(shop, lat, lng)
    db.add(shop)
    db.flush()
    listing = Listing(
        shop_id=shop.id, seller_id=seller.id, title=title, description=description,
        price_cents=1000, currency="KES", stock_qty=stock, is_active=is_active,
        lat=lat, lng=lng, media_urls=media_urls, promo_mode=promo_mode,
        promo_started_at=promo_started_at, promo_expires_at=promo_expires_at,
    )
    proximity.set_location(listing, lat, lng)
    db.add(listing)
    db.commit()
    return listing


# ----------------------------- service: matching -----------------------------

def test_matches_title(db_session):
    li = _seed(db_session, user_uuid="s1", title="Cordless Drill")
    hits = search.search_trade(db_session, "drill", _LAT, _LNG, limit=10, now=_NOW)
    assert [h.listing.id for h in hits] == [li.id]


def test_matches_description(db_session):
    li = _seed(db_session, user_uuid="s1", title="Power tool",
               description="A heavy-duty cordless DRILL for concrete")
    hits = search.search_trade(db_session, "drill", _LAT, _LNG, limit=10, now=_NOW)
    assert [h.listing.id for h in hits] == [li.id]


def test_matches_shop_name(db_session):
    li = _seed(db_session, user_uuid="s1", title="Generic item", shop_name="Mama Njeri Hardware")
    hits = search.search_trade(db_session, "njeri", _LAT, _LNG, limit=10, now=_NOW)
    assert [h.listing.id for h in hits] == [li.id]
    assert hits[0].shop_name == "Mama Njeri Hardware"


def test_match_is_case_insensitive(db_session):
    li = _seed(db_session, user_uuid="s1", title="MAIZE flour")
    assert [h.listing.id for h in search.search_trade(db_session, "maize", _LAT, _LNG, limit=10, now=_NOW)] == [li.id]
    assert [h.listing.id for h in search.search_trade(db_session, "MAIZE", _LAT, _LNG, limit=10, now=_NOW)] == [li.id]


def test_no_match_returns_empty(db_session):
    _seed(db_session, user_uuid="s1", title="Cordless Drill")
    assert search.search_trade(db_session, "xylophone", _LAT, _LNG, limit=10, now=_NOW) == []


# ----------------------------- service: nearest-first, nationwide -----------------------------

def test_ranked_nearest_first_nationwide(db_session):
    # Two matches for "drill": one near, one ~440 km away. BOTH returned (nationwide), near one first.
    near = _seed(db_session, user_uuid="s-near", title="Drill near", lat=_LAT, lng=_LNG)
    far = _seed(db_session, user_uuid="s-far", title="Drill far", lat=_FAR_LAT, lng=_FAR_LNG)
    hits = search.search_trade(db_session, "drill", _LAT, _LNG, limit=10, now=_NOW)
    assert [h.listing.id for h in hits] == [near.id, far.id]
    assert hits[0].distance_m < hits[1].distance_m


# ----------------------------- service: LIKE-injection escaping (SECURITY) -----------------------------

def test_wildcard_percent_is_literal(db_session):
    # "50%" must match the literal listing, NOT act as a LIKE wildcard matching everything.
    hit_row = _seed(db_session, user_uuid="s1", title="50% off soap")
    _seed(db_session, user_uuid="s2", title="Unrelated widget")
    hits = search.search_trade(db_session, "50%", _LAT, _LNG, limit=10, now=_NOW)
    assert [h.listing.id for h in hits] == [hit_row.id]  # only the literal "50%" row, not both


def test_wildcard_underscore_is_literal(db_session):
    # "a_b" must match "a_b" literally, not "axb" (underscore = single-char wildcard if unescaped).
    literal = _seed(db_session, user_uuid="s1", title="model a_b spec")
    _seed(db_session, user_uuid="s2", title="model axb spec")
    hits = search.search_trade(db_session, "a_b", _LAT, _LNG, limit=10, now=_NOW)
    assert [h.listing.id for h in hits] == [literal.id]


def test_backslash_is_literal(db_session):
    # A raw backslash in the query must not corrupt the ESCAPE handling.
    li = _seed(db_session, user_uuid="s1", title="path c\\d drive")
    hits = search.search_trade(db_session, "c\\d", _LAT, _LNG, limit=10, now=_NOW)
    assert [h.listing.id for h in hits] == [li.id]


# ----------------------------- service: visibility rules -----------------------------

def test_out_of_stock_product_hidden(db_session):
    _seed(db_session, user_uuid="s1", title="Sold-out drill", stock=0)
    assert search.search_trade(db_session, "drill", _LAT, _LNG, limit=10, now=_NOW) == []


def test_inactive_listing_hidden(db_session):
    _seed(db_session, user_uuid="s1", title="Deactivated drill", is_active=False)
    assert search.search_trade(db_session, "drill", _LAT, _LNG, limit=10, now=_NOW) == []


def test_expired_story_hidden(db_session):
    # A STORY-mode promotion whose window has closed disappears from discovery (feed rule reused).
    _seed(db_session, user_uuid="s1", title="Expired story drill", promo_mode=PROMO_STORY,
          promo_started_at=_NOW - timedelta(hours=2), promo_expires_at=_NOW - timedelta(hours=1))
    assert search.search_trade(db_session, "drill", _LAT, _LNG, limit=10, now=_NOW) == []


# ----------------------------- service: min-length backstop -----------------------------

def test_too_short_query_returns_empty(db_session):
    _seed(db_session, user_uuid="s1", title="a")
    assert search.search_trade(db_session, "a", _LAT, _LNG, limit=10, now=_NOW) == []
    assert search.search_trade(db_session, "  ", _LAT, _LNG, limit=10, now=_NOW) == []


def test_limit_caps_results(db_session):
    for i in range(5):
        _seed(db_session, user_uuid=f"s{i}", title=f"Drill {i}")
    hits = search.search_trade(db_session, "drill", _LAT, _LNG, limit=2, now=_NOW)
    assert len(hits) == 2


# ----------------------------- HTTP: auth + bounds + shape -----------------------------

def test_http_requires_auth(client):
    r = client.get("/api/v1/search", params={"q": "drill", "lat": _LAT, "lng": _LNG})
    assert r.status_code in (401, 403)


def test_http_rejects_out_of_range_lat(client):
    r = client.get("/api/v1/search",
                   params={"q": "drill", "lat": 999.0, "lng": _LNG}, headers=_buyer_auth())
    assert r.status_code == 422


def test_http_returns_results_no_pii(client, db_session):
    li = _seed(db_session, user_uuid="seller-http", title="Cordless Drill", shop_name="Tool Hub",
               category="hardware", description="great drill",
               media_urls=json.dumps(["/uploads/trade/images/drill.webp"]))
    r = client.get("/api/v1/search",
                   params={"q": "drill", "lat": _LAT, "lng": _LNG}, headers=_buyer_auth())
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "drill"
    assert len(body["results"]) == 1
    res = body["results"][0]
    assert res["listing_id"] == li.id
    assert res["title"] == "Cordless Drill"
    assert res["shop_name"] == "Tool Hub"
    assert res["shop_category"] == "hardware"
    assert res["image_url"] == "/uploads/trade/images/drill.webp"
    assert res["price_cents"] == 1000
    # No PII: the only identity field is the opaque seller id; no user_uuid / email / display_name.
    assert "user_uuid" not in res
    assert "email" not in res
    assert set(res).issuperset({"listing_id", "seller_id", "shop_id", "distance_m"})


def test_http_too_short_query_empty(client, db_session):
    _seed(db_session, user_uuid="seller-http", title="Drill")
    r = client.get("/api/v1/search",
                   params={"q": "d", "lat": _LAT, "lng": _LNG}, headers=_buyer_auth())
    assert r.status_code == 200
    assert r.json()["results"] == []
