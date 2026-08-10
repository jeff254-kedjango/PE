"""Seller write path — ownership, scope-gating, stock-adjust validation, storefront view.

Uses REAL RS256 tokens (not the principal-bypass fixture) so the scope gate and the
per-seller identity (token sub) are exercised end to end. The DB is the in-memory SQLite
fixture from conftest.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from PE.commerce.core.database import get_db
from PE.commerce.main import app
from PE.commerce.schemas.catalog import DESCRIPTION_MAX_LEN

_KEYS = Path(__file__).resolve().parents[3] / "PE" / "dev" / "keys"
_PRIVATE = (_KEYS / "insar_jwt_private.pem").read_text()


def _mint(sub="seller-A", scopes=("read:feed", "create:trades"), exp_min=10):
    payload = {
        "sub": sub,
        "role": "user",
        "scope": "commerce_trade",
        "scopes": list(scopes),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=exp_min),
    }
    return jwt.encode(payload, _PRIVATE, algorithm="RS256")


@pytest.fixture
def client(db_session):
    """Real-auth client (no principal override) bound to the SQLite test session."""
    app.dependency_overrides.clear()

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _auth(sub="seller-A", scopes=("read:feed", "create:trades")):
    return {"Authorization": f"Bearer {_mint(sub=sub, scopes=scopes)}"}


def _make_shop(client, sub="seller-A", **over):
    body = {"name": "Mama Mboga", "lat": -1.29, "lng": 36.82, "display_name": "Mama A"}
    body.update(over)
    return client.post("/api/v1/shops", json=body, headers=_auth(sub=sub))


def _make_listing(client, shop_id, sub="seller-A", **over):
    body = {"title": "Maize flour 2kg", "price_cents": 12000, "stock_qty": 3}
    body.update(over)
    return client.post(
        f"/api/v1/shops/{shop_id}/listings", json=body, headers=_auth(sub=sub)
    )


# --------------------------- happy path ---------------------------

def test_create_shop_then_listing(client):
    r = _make_shop(client)
    assert r.status_code == 201, r.text
    shop = r.json()
    assert shop["lat"] == -1.29 and shop["seller_id"]

    r2 = _make_listing(client, shop["id"])
    assert r2.status_code == 201, r2.text
    li = r2.json()
    assert li["stock_qty"] == 3 and li["price_cents"] == 12000
    assert li["is_out_of_stock"] is False
    # listing inherits the shop's location (denormalized for the feed)
    assert li["seller_id"] == shop["seller_id"]


def test_listing_inherits_shop_property_uuid(client):
    r = _make_shop(client, property_uuid="bldg-123")
    shop = r.json()
    li = _make_listing(client, shop["id"]).json()
    assert li["property_uuid"] == "bldg-123"


# --------------------------- shop category (§8 trending color) ---------------------------

def test_shop_category_round_trips_through_create_and_feed(client):
    """A valid category persists on the shop and surfaces on the feed item (display-only)."""
    shop = _make_shop(client, category="bakery").json()
    assert shop["category"] == "bakery"
    li = _make_listing(client, shop["id"]).json()
    resp = client.get("/api/v1/feed?lat=-1.29&lng=36.82&radius_m=2000", headers=_auth())
    item = next(i for i in resp.json()["items"] if i["id"] == li["id"])
    assert item["shop_category"] == "bakery"


def test_shop_category_is_optional(client):
    shop = _make_shop(client).json()
    assert shop["category"] is None


def test_shop_category_rejects_unknown_value(client):
    # An unknown category is a 422 at the API edge (no free-text into the rail).
    r = _make_shop(client, category="not-a-real-category")
    assert r.status_code == 422, r.text


def test_shop_category_surfaces_on_profile(client):
    shop = _make_shop(client, category="electronics").json()
    prof = client.get(f"/api/v1/shops/{shop['id']}/profile", headers=_auth()).json()
    assert prof["category"] == "electronics"


# --------------------------- shop logo (avatar) + banner (§8 setup) ---------------------------

def test_shop_avatar_and_banner_round_trip_through_create_and_profile(client):
    shop = _make_shop(
        client,
        avatar_url="/uploads/trade/images/logo.png",
        banner_url="/uploads/trade/images/cover.jpg",
    ).json()
    # Owner view carries both.
    assert shop["avatar_url"] == "/uploads/trade/images/logo.png"
    assert shop["banner_url"] == "/uploads/trade/images/cover.jpg"
    # Public hovercard carries both too (seller-published, not PII).
    prof = client.get(f"/api/v1/shops/{shop['id']}/profile", headers=_auth()).json()
    assert prof["avatar_url"] == "/uploads/trade/images/logo.png"
    assert prof["banner_url"] == "/uploads/trade/images/cover.jpg"


def test_shop_avatar_and_banner_default_null(client):
    shop = _make_shop(client).json()
    assert shop["avatar_url"] is None and shop["banner_url"] is None


def test_shop_blank_avatar_normalised_to_null(client):
    shop = _make_shop(client, avatar_url="   ", banner_url="").json()
    assert shop["avatar_url"] is None and shop["banner_url"] is None


# --------------------------- product description ---------------------------

def test_description_round_trips_through_create_and_feed(client):
    """A multi-paragraph description survives create (preserving newlines) and surfaces in the feed."""
    desc = "Crisp greens.\n\nPicked this morning."
    shop = _make_shop(client).json()
    li = _make_listing(client, shop["id"], description=desc).json()
    assert li["description"] == desc  # newlines preserved verbatim — client renders paragraphs

    resp = client.get(f"/api/v1/feed?lat=-1.29&lng=36.82&radius_m=2000", headers=_auth())
    assert resp.status_code == 200, resp.text
    item = next(i for i in resp.json()["items"] if i["id"] == li["id"])
    assert item["description"] == desc


def test_description_is_optional_and_defaults_null(client):
    shop = _make_shop(client).json()
    li = _make_listing(client, shop["id"]).json()
    assert li["description"] is None


def test_blank_description_is_normalised_to_null(client):
    shop = _make_shop(client).json()
    li = _make_listing(client, shop["id"], description="   \n  ").json()
    assert li["description"] is None


def test_description_over_max_length_is_rejected(client):
    shop = _make_shop(client).json()
    r = _make_listing(client, shop["id"], description="x" * (DESCRIPTION_MAX_LEN + 1))
    assert r.status_code == 422, r.text


# --------------------------- scope gating ---------------------------

def test_write_requires_create_trades_scope(client):
    # A read-only token (feed scope only) is forbidden on every write endpoint.
    ro = _auth(scopes=("read:feed",))
    assert client.post(
        "/api/v1/shops",
        json={"name": "X", "lat": 0, "lng": 0, "display_name": "n"},
        headers=ro,
    ).status_code == 403
    assert client.post(
        "/api/v1/shops/whatever/listings",
        json={"title": "t", "price_cents": 1},
        headers=ro,
    ).status_code == 403
    assert client.patch(
        "/api/v1/listings/whatever/stock", json={"delta": -1}, headers=ro
    ).status_code == 403
    assert client.get("/api/v1/shops/mine", headers=ro).status_code == 403


def test_write_requires_token(client):
    assert client.post(
        "/api/v1/shops",
        json={"name": "X", "lat": 0, "lng": 0, "display_name": "n"},
    ).status_code == 401


# --------------------------- ownership (no cross-seller writes / leaks) ---------------------------

def test_cannot_create_listing_under_another_sellers_shop(client):
    shop = _make_shop(client, sub="seller-A").json()
    # seller-B tries to add a listing to seller-A's shop → 404 (existence not confirmed)
    r = _make_listing(client, shop["id"], sub="seller-B")
    assert r.status_code == 404


def test_cannot_adjust_another_sellers_stock(client):
    shop = _make_shop(client, sub="seller-A").json()
    li = _make_listing(client, shop["id"], sub="seller-A").json()
    r = client.patch(
        f"/api/v1/listings/{li['id']}/stock",
        json={"delta": -1},
        headers=_auth(sub="seller-B"),
    )
    assert r.status_code == 404


# --------------------------- stock adjust validation ---------------------------

def test_stock_absolute_and_delta(client):
    shop = _make_shop(client).json()
    li = _make_listing(client, shop["id"], stock_qty=5).json()
    lid = li["id"]

    # absolute set
    r = client.patch(f"/api/v1/listings/{lid}/stock", json={"stock_qty": 2}, headers=_auth())
    assert r.status_code == 200 and r.json()["stock_qty"] == 2

    # relative delta
    r = client.patch(f"/api/v1/listings/{lid}/stock", json={"delta": 3}, headers=_auth())
    assert r.json()["stock_qty"] == 5

    # delta clamps at zero — never negative
    r = client.patch(f"/api/v1/listings/{lid}/stock", json={"delta": -99}, headers=_auth())
    assert r.json()["stock_qty"] == 0
    assert r.json()["is_out_of_stock"] is True


def test_stock_adjust_rejects_both_or_neither(client):
    shop = _make_shop(client).json()
    li = _make_listing(client, shop["id"]).json()
    lid = li["id"]
    # neither
    assert client.patch(f"/api/v1/listings/{lid}/stock", json={}, headers=_auth()).status_code == 422
    # both
    assert client.patch(
        f"/api/v1/listings/{lid}/stock", json={"stock_qty": 1, "delta": 1}, headers=_auth()
    ).status_code == 422
    # negative absolute
    assert client.patch(
        f"/api/v1/listings/{lid}/stock", json={"stock_qty": -1}, headers=_auth()
    ).status_code == 422


def test_create_rejects_negative_money_and_stock(client):
    shop = _make_shop(client).json()
    assert _make_listing(client, shop["id"], price_cents=-1).status_code == 422
    assert _make_listing(client, shop["id"], stock_qty=-1).status_code == 422


def test_create_listing_defaults_stock_to_one_when_omitted(client):
    # An OMITTED stock must publish a VISIBLE product (stock 1), not an invisible one (stock 0):
    # the buyer feed hides stock_qty<=0, so defaulting to 0 was a "published but nobody sees it"
    # footgun on the direct API. A 0-stock draft now requires an explicit opt-in.
    shop = _make_shop(client).json()
    body = {"title": "No-stock-field flour", "price_cents": 9000}  # stock_qty deliberately omitted
    r = client.post(f"/api/v1/shops/{shop['id']}/listings", json=body, headers=_auth())
    assert r.status_code == 201, r.text
    li = r.json()
    assert li["stock_qty"] == 1 and li["is_out_of_stock"] is False
    # Explicit 0 is still honoured — the seller opted into a hidden draft.
    r0 = _make_listing(client, shop["id"], stock_qty=0)
    assert r0.status_code == 201 and r0.json()["stock_qty"] == 0 and r0.json()["is_out_of_stock"] is True


# --------------------------- storefront (seller sees everything) ---------------------------

def test_storefront_shows_out_of_stock_and_low_flag(client):
    shop = _make_shop(client).json()
    li = _make_listing(client, shop["id"], stock_qty=2, low_stock_threshold=2).json()
    # sell one unit → 1 left, at/under threshold 2 → low (but not out)
    client.patch(f"/api/v1/listings/{li['id']}/stock", json={"delta": -1}, headers=_auth())

    sf = client.get("/api/v1/shops/mine", headers=_auth()).json()
    assert sf["display_name"] == "Mama A"
    listings = sf["shops"][0]["listings"]
    assert len(listings) == 1
    assert listings[0]["is_low_stock"] is True
    assert listings[0]["is_out_of_stock"] is False

    # sell the last → out of stock, no longer "low"; storefront STILL shows it
    client.patch(f"/api/v1/listings/{li['id']}/stock", json={"delta": -1}, headers=_auth())
    sf = client.get("/api/v1/shops/mine", headers=_auth()).json()
    out = sf["shops"][0]["listings"][0]
    assert out["is_out_of_stock"] is True and out["is_low_stock"] is False


def test_storefront_empty_for_new_seller(client):
    sf = client.get("/api/v1/shops/mine", headers=_auth(sub="never-sold")).json()
    assert sf["shops"] == []


# --------------------------- storefront rating badge (increment 6 surfacing) ---------------------------

def _idem(key):
    return {"Idempotency-Key": key}


def test_storefront_unrated_seller_shows_null_rating(client):
    # A seller with listings but no reviews yet → rating None, count 0 (unrated, not zero-star).
    shop = _make_shop(client, sub="seller-unr").json()
    _make_listing(client, shop["id"], sub="seller-unr")
    sf = client.get("/api/v1/shops/mine", headers=_auth(sub="seller-unr")).json()
    assert sf["rating"] is None and sf["review_count"] == 0


def test_storefront_shows_seller_rating_after_review(client):
    # A buyer settles + reviews a fixed-price listing → the seller's storefront shows the rating.
    shop = _make_shop(client, sub="seller-rt").json()
    lid = _make_listing(client, shop["id"], sub="seller-rt", price_cents=10000,
                        pricing_mode="fixed", stock_qty=5).json()["id"]
    buyer = _auth(sub="buyer-rt", scopes=("read:feed",))
    oid = client.post("/api/v1/orders", json={"listing_id": lid},
                      headers={**buyer, **_idem("open")}).json()["id"]
    client.post(f"/api/v1/orders/{oid}/settle", headers={**buyer, **_idem("settle")})
    r = client.post(f"/api/v1/orders/{oid}/review", json={"rating": 4}, headers=buyer)
    assert r.status_code == 201, r.text

    sf = client.get("/api/v1/shops/mine", headers=_auth(sub="seller-rt")).json()
    assert sf["rating"] == 4.0 and sf["review_count"] == 1


# --------------------------- PUBLIC storefront read ---------------------------

def test_public_storefront_shows_in_stock_only_and_no_pos_leak(client):
    shop = _make_shop(client, sub="seller-pub").json()
    seller_id = shop["seller_id"]
    in_stock = _make_listing(client, shop["id"], sub="seller-pub",
                             title="Avail", stock_qty=4).json()
    out_stock = _make_listing(client, shop["id"], sub="seller-pub",
                              title="Sold out", stock_qty=0).json()

    # Any authenticated buyer (no create:trades) may view the public storefront.
    buyer = _auth(sub="buyer-pub", scopes=("read:feed",))
    resp = client.get(f"/api/v1/sellers/{seller_id}/storefront", headers=buyer)
    assert resp.status_code == 200, resp.text
    sf = resp.json()
    listings = sf["shops"][0]["listings"]
    titles = {li["title"] for li in listings}
    assert titles == {"Avail"}  # out-of-stock hidden (buyer visibility)
    # No POS-internal fields leak to a buyer (S6).
    li = listings[0]
    for leaked in ("stock_qty", "low_stock_threshold", "is_low_stock",
                   "is_out_of_stock", "intent_weight", "is_active"):
        assert leaked not in li, f"public listing leaked {leaked}"
    # property_uuid IS exposed (for the InSAR Confirmed-badge client stitch).
    assert "property_uuid" in li


def test_public_storefront_embeds_rating(client):
    shop = _make_shop(client, sub="seller-pr").json()
    seller_id = shop["seller_id"]
    lid = _make_listing(client, shop["id"], sub="seller-pr", price_cents=10000,
                        pricing_mode="fixed", stock_qty=5).json()["id"]
    buyer = _auth(sub="buyer-pr", scopes=("read:feed",))
    oid = client.post("/api/v1/orders", json={"listing_id": lid},
                      headers={**buyer, **_idem("open")}).json()["id"]
    client.post(f"/api/v1/orders/{oid}/settle", headers={**buyer, **_idem("settle")})
    client.post(f"/api/v1/orders/{oid}/review", json={"rating": 5}, headers=buyer)

    sf = client.get(f"/api/v1/sellers/{seller_id}/storefront", headers=buyer).json()
    assert sf["rating"] == 5.0 and sf["review_count"] == 1


def test_public_storefront_unknown_seller_404(client):
    assert client.get("/api/v1/sellers/ghost/storefront",
                      headers=_auth(sub="b", scopes=("read:feed",))).status_code == 404


def test_public_storefront_requires_token(client):
    assert client.get("/api/v1/sellers/x/storefront").status_code == 401


# --------------------------- §8 plain posts (price-less listings) ---------------------------

_LAT, _LNG = -1.29, 36.82


def _make_post(client, sub="poster-A", body="Hello Huruma 👋", **over):
    payload = {"body": body, "lat": _LAT, "lng": _LNG}
    payload.update(over)
    return client.post("/api/v1/posts", json=payload, headers=_auth(sub=sub))


def test_create_post_auto_provisions_shop_and_is_price_less(client):
    r = _make_post(client, sub="poster-1", body="First post on my street.\n\nGood morning!")
    assert r.status_code == 201, r.text
    post = r.json()
    assert post["post_kind"] == "post"
    assert post["price_cents"] == 0 and post["stock_qty"] == 0
    # The post's text lives in description (paragraphs preserved); title is a derived snippet.
    assert post["description"] == "First post on my street.\n\nGood morning!"
    assert post["title"]  # non-empty derived title
    assert post["shop_id"] and post["seller_id"]  # a personal shop was auto-created


def test_post_appears_in_feed_despite_zero_stock(client):
    """The critical regression: the buyer feed hides out-of-stock PRODUCTS, but a post (stock 0,
    no inventory) must still surface."""
    post = _make_post(client, sub="poster-2", body="Anyone selling sukuma near Mathare?").json()
    feed = client.get(f"/api/v1/feed?lat={_LAT}&lng={_LNG}&radius_m=2000", headers=_auth()).json()
    ids = {i["id"] for i in feed["items"]}
    assert post["id"] in ids
    item = next(i for i in feed["items"] if i["id"] == post["id"])
    assert item["post_kind"] == "post" and item["price_cents"] == 0


def test_out_of_stock_product_still_hidden_from_feed(client):
    """The other direction: post_kind must NOT make sold-out products visible."""
    shop = _make_shop(client, sub="seller-oos").json()
    li = _make_listing(client, shop["id"], sub="seller-oos", stock_qty=0).json()
    feed = client.get(f"/api/v1/feed?lat=-1.29&lng=36.82&radius_m=2000", headers=_auth()).json()
    assert li["id"] not in {i["id"] for i in feed["items"]}


def test_post_surfaces_in_public_storefront(client):
    post = _make_post(client, sub="poster-3", body="Open for business!").json()
    sf = client.get(
        f"/api/v1/sellers/{post['seller_id']}/storefront",
        headers=_auth(sub="viewer", scopes=("read:feed",)),
    ).json()
    all_ids = {li["id"] for shop in sf["shops"] for li in shop["listings"]}
    assert post["id"] in all_ids


def test_second_post_reuses_the_same_personal_shop(client):
    p1 = _make_post(client, sub="poster-4", body="one").json()
    p2 = _make_post(client, sub="poster-4", body="two").json()
    assert p1["shop_id"] == p2["shop_id"]  # no shop spam — one personal shop per poster


def test_post_requires_create_trades_scope(client):
    ro = _auth(scopes=("read:feed",))
    assert client.post(
        "/api/v1/posts", json={"body": "hi", "lat": _LAT, "lng": _LNG}, headers=ro
    ).status_code == 403


def test_post_requires_body(client):
    assert _make_post(client, body="").status_code == 422
    assert _make_post(client, body="   ").status_code == 422


def test_public_storefront_hides_shop_with_no_visible_listings(client):
    # A shop whose only listing is out of stock is omitted entirely (nothing to sell).
    shop = _make_shop(client, sub="seller-empty").json()
    seller_id = shop["seller_id"]
    _make_listing(client, shop["id"], sub="seller-empty", title="Gone", stock_qty=0)
    sf = client.get(f"/api/v1/sellers/{seller_id}/storefront",
                    headers=_auth(sub="b2", scopes=("read:feed",))).json()
    assert sf["shops"] == []


# --------------------------- listing edit (PATCH /listings/{id}) ---------------------------

def test_update_listing_partial_fields_only(client):
    shop = _make_shop(client).json()
    li = _make_listing(client, shop["id"], title="Old", price_cents=12000,
                       description="original").json()
    lid = li["id"]
    # Patch only the title + price; description must be left untouched (not nulled).
    r = client.patch(f"/api/v1/listings/{lid}",
                     json={"title": "New Title", "price_cents": 9900}, headers=_auth())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "New Title"
    assert body["price_cents"] == 9900
    assert body["description"] == "original"  # omitted field untouched


def test_update_listing_explicit_null_clears_description(client):
    shop = _make_shop(client).json()
    li = _make_listing(client, shop["id"], description="to be cleared").json()
    r = client.patch(f"/api/v1/listings/{li['id']}",
                     json={"description": None}, headers=_auth())
    assert r.status_code == 200 and r.json()["description"] is None


def test_update_listing_media_and_video_flag(client):
    shop = _make_shop(client).json()
    li = _make_listing(client, shop["id"]).json()
    r = client.patch(f"/api/v1/listings/{li['id']}",
                     json={"media_urls": ["/uploads/trade/videos/x.mp4"], "is_short_video": True},
                     headers=_auth())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["media_urls"] == ["/uploads/trade/videos/x.mp4"]
    assert body["is_short_video"] is True


def test_update_empty_patch_is_422(client):
    shop = _make_shop(client).json()
    li = _make_listing(client, shop["id"]).json()
    assert client.patch(f"/api/v1/listings/{li['id']}", json={}, headers=_auth()).status_code == 422


def test_update_listing_requires_write_scope(client):
    shop = _make_shop(client).json()
    li = _make_listing(client, shop["id"]).json()
    ro = _auth(scopes=("read:feed",))
    assert client.patch(f"/api/v1/listings/{li['id']}",
                        json={"title": "x"}, headers=ro).status_code == 403


def test_cannot_update_another_sellers_listing(client):
    shop = _make_shop(client, sub="seller-A").json()
    li = _make_listing(client, shop["id"], sub="seller-A").json()
    r = client.patch(f"/api/v1/listings/{li['id']}",
                     json={"title": "hijack"}, headers=_auth(sub="seller-B"))
    assert r.status_code == 404  # cross-owner: not-found, never confirms existence


def test_update_edit_surfaces_on_feed(client):
    shop = _make_shop(client).json()
    li = _make_listing(client, shop["id"], title="Before").json()
    client.patch(f"/api/v1/listings/{li['id']}", json={"title": "After"}, headers=_auth())
    feed = client.get("/api/v1/feed?lat=-1.29&lng=36.82&radius_m=2000", headers=_auth()).json()
    item = next(i for i in feed["items"] if i["id"] == li["id"])
    assert item["title"] == "After"


def test_update_price_on_post_is_ignored(client):
    # A commerce-only edit (price) on a plain POST must be silently ignored — a post stays price-less
    # (never becomes orderable through an edit).
    post = _make_post(client, sub="poster-edit", body="hi").json()
    r = client.patch(f"/api/v1/listings/{post['id']}",
                     json={"price_cents": 5000, "description": "edited body"},
                     headers=_auth(sub="poster-edit"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["price_cents"] == 0                 # commerce field ignored on a post
    assert body["description"] == "edited body"     # non-commerce edit still applies


# --------------------------- listing soft-delete (DELETE /listings/{id}) ---------------------------

def test_delete_listing_removes_from_feed_and_storefront(client):
    shop = _make_shop(client).json()
    li = _make_listing(client, shop["id"], stock_qty=5).json()
    lid = li["id"]
    # Present before delete.
    feed = client.get("/api/v1/feed?lat=-1.29&lng=36.82&radius_m=2000", headers=_auth()).json()
    assert lid in {i["id"] for i in feed["items"]}

    r = client.delete(f"/api/v1/listings/{lid}", headers=_auth())
    assert r.status_code == 204, r.text

    # Gone from the buyer feed AND the seller's own storefront (soft-deleted → is_active False).
    feed = client.get("/api/v1/feed?lat=-1.29&lng=36.82&radius_m=2000", headers=_auth()).json()
    assert lid not in {i["id"] for i in feed["items"]}
    sf = client.get("/api/v1/shops/mine", headers=_auth()).json()
    live_ids = {li_["id"] for s in sf["shops"] for li_ in s["listings"] if li_["is_active"]}
    assert lid not in live_ids


def test_delete_listing_is_idempotent(client):
    shop = _make_shop(client).json()
    li = _make_listing(client, shop["id"]).json()
    assert client.delete(f"/api/v1/listings/{li['id']}", headers=_auth()).status_code == 204
    # A second delete is a clean no-op — still 204 (the asked-for end state holds).
    assert client.delete(f"/api/v1/listings/{li['id']}", headers=_auth()).status_code == 204


def test_cannot_delete_another_sellers_listing(client):
    shop = _make_shop(client, sub="seller-A").json()
    li = _make_listing(client, shop["id"], sub="seller-A").json()
    r = client.delete(f"/api/v1/listings/{li['id']}", headers=_auth(sub="seller-B"))
    assert r.status_code == 404
    # The victim's listing is untouched — still live in their storefront.
    sf = client.get("/api/v1/shops/mine", headers=_auth(sub="seller-A")).json()
    assert li["id"] in {li_["id"] for s in sf["shops"] for li_ in s["listings"]}


def test_delete_requires_write_scope(client):
    shop = _make_shop(client).json()
    li = _make_listing(client, shop["id"]).json()
    ro = _auth(scopes=("read:feed",))
    assert client.delete(f"/api/v1/listings/{li['id']}", headers=ro).status_code == 403


# --------------------------- §8 Chunk E2 — low-stock listing ---------------------------

class TestLowStockEndpoint:
    """GET /sellers/me/low-stock — the LEFT-column card's data source."""

    def test_empty_when_seller_has_no_shop(self, client):
        r = client.get("/api/v1/sellers/me/low-stock", headers=_auth(sub="ghost-seller"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body == {"floor": 5, "items": []}

    def test_shop_wide_floor_triggers_on_untouched_listings(self, client):
        shop = _make_shop(client).json()
        # stock_qty=3 with default low_stock_threshold=0 → floor 5 catches it
        _make_listing(client, shop["id"], title="A", stock_qty=3).json()
        r = client.get("/api/v1/sellers/me/low-stock", headers=_auth())
        assert r.status_code == 200
        body = r.json()
        assert body["floor"] == 5
        assert len(body["items"]) == 1
        assert body["items"][0]["title"] == "A" and body["items"][0]["stock_qty"] == 3

    def test_per_listing_threshold_overrides_shop_floor(self, client):
        shop = _make_shop(client).json()
        # A listing with threshold=10 and stock=8 IS low (8 <= 10) even though 8 > shop floor 5.
        li = _make_listing(client, shop["id"], title="Bigcase", stock_qty=8,
                           low_stock_threshold=10).json()
        # And a listing with threshold=1 and stock=3 is NOT low (3 > 1) — its own threshold
        # beats the shop-wide floor of 5.
        _make_listing(client, shop["id"], title="Bulk", stock_qty=3,
                      low_stock_threshold=1).json()
        r = client.get("/api/v1/sellers/me/low-stock", headers=_auth())
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 1 and items[0]["id"] == li["id"]

    def test_ordered_ascending_by_stock(self, client):
        shop = _make_shop(client).json()
        _make_listing(client, shop["id"], title="Two", stock_qty=2)
        _make_listing(client, shop["id"], title="Five", stock_qty=5)
        _make_listing(client, shop["id"], title="One", stock_qty=1)
        r = client.get("/api/v1/sellers/me/low-stock", headers=_auth())
        items = r.json()["items"]
        assert [i["stock_qty"] for i in items] == [1, 2, 5]

    def test_excludes_inactive_listings(self, client):
        shop = _make_shop(client).json()
        li = _make_listing(client, shop["id"], stock_qty=1).json()
        # DELETE soft-deactivates the listing (flips is_active=false).
        client.delete(f"/api/v1/listings/{li['id']}", headers=_auth())
        r = client.get("/api/v1/sellers/me/low-stock", headers=_auth())
        assert r.json()["items"] == []

    def test_scope_gating(self, client):
        # No token → 401 (or 403 depending on the shared auth policy — mirrors adjust_stock).
        r = client.get("/api/v1/sellers/me/low-stock")
        assert r.status_code in (401, 403)
        # read:feed-only token → 403 (create:trades required).
        r = client.get("/api/v1/sellers/me/low-stock",
                       headers=_auth(scopes=("read:feed",)))
        assert r.status_code == 403

    def test_custom_floor_query_param(self, client):
        shop = _make_shop(client).json()
        _make_listing(client, shop["id"], title="A", stock_qty=8)
        _make_listing(client, shop["id"], title="B", stock_qty=12)
        r = client.get("/api/v1/sellers/me/low-stock?floor=10", headers=_auth())
        body = r.json()
        assert body["floor"] == 10
        titles = sorted(i["title"] for i in body["items"])
        assert titles == ["A"]

    def test_negative_floor_clamped_to_zero(self, client):
        shop = _make_shop(client).json()
        _make_listing(client, shop["id"], title="Zero", stock_qty=0)
        _make_listing(client, shop["id"], title="One", stock_qty=1)
        r = client.get("/api/v1/sellers/me/low-stock?floor=-4", headers=_auth())
        body = r.json()
        assert body["floor"] == 0
        # Only stock_qty=0 satisfies "<= 0"; a listing at 1 is above the clamped floor.
        titles = [i["title"] for i in body["items"]]
        assert titles == ["Zero"]

    def test_excludes_other_sellers_listings(self, client):
        shop_a = _make_shop(client, sub="seller-A").json()
        shop_b = _make_shop(client, sub="seller-B").json()
        _make_listing(client, shop_a["id"], sub="seller-A", title="Mine", stock_qty=1)
        _make_listing(client, shop_b["id"], sub="seller-B", title="Theirs", stock_qty=1)
        r = client.get("/api/v1/sellers/me/low-stock", headers=_auth(sub="seller-A"))
        items = r.json()["items"]
        assert [i["title"] for i in items] == ["Mine"]


# --------------------------- §8 Chunk E3 — bulk stock CSV ---------------------------

class TestBulkStockCsv:
    """POST /sellers/me/stock/bulk-csv — one-shot restock across many listings."""

    def _seed_three(self, client):
        shop = _make_shop(client).json()
        a = _make_listing(client, shop["id"], title="A", stock_qty=1).json()
        b = _make_listing(client, shop["id"], title="B", stock_qty=1).json()
        c = _make_listing(client, shop["id"], title="C", stock_qty=1).json()
        return shop, (a, b, c)

    def test_updates_all_owned_listings(self, client):
        _, (a, b, c) = self._seed_three(client)
        csv = f"{a['id']},10\n{b['id']},20\n{c['id']},30\n"
        r = client.post("/api/v1/sellers/me/stock/bulk-csv",
                        json={"csv": csv}, headers=_auth())
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["updated_count"] == 3
        assert body["skipped_count"] == 0
        assert set(body["updated_ids"]) == {a["id"], b["id"], c["id"]}

    def test_accepts_header_row(self, client):
        _, (a, _b, _c) = self._seed_three(client)
        csv = f"listing_id,stock_qty\n{a['id']},42\n"
        r = client.post("/api/v1/sellers/me/stock/bulk-csv",
                        json={"csv": csv}, headers=_auth())
        assert r.status_code == 200
        assert r.json()["updated_count"] == 1

    def test_stock_actually_changes(self, client):
        _, (a, _b, _c) = self._seed_three(client)
        csv = f"{a['id']},77\n"
        client.post("/api/v1/sellers/me/stock/bulk-csv",
                    json={"csv": csv}, headers=_auth())
        # Read back via the storefront — the seller's own view shows every listing.
        r = client.get("/api/v1/shops/mine", headers=_auth())
        # Find our updated listing in the storefront tree.
        all_listings = [li for s in r.json()["shops"] for li in s["listings"]]
        li = next(li for li in all_listings if li["id"] == a["id"])
        assert li["stock_qty"] == 77

    def test_unowned_ids_skipped_not_raised(self, client):
        _, (a, _b, _c) = self._seed_three(client)
        csv = f"{a['id']},5\nsome-other-seller-lst,999\n"
        r = client.post("/api/v1/sellers/me/stock/bulk-csv",
                        json={"csv": csv}, headers=_auth())
        assert r.status_code == 200
        body = r.json()
        assert body["updated_count"] == 1
        assert body["skipped_count"] == 1
        # The unowned id must NEVER appear in the response — that would leak existence.
        assert "some-other-seller-lst" not in body["updated_ids"]

    def test_duplicate_id_422s(self, client):
        _, (a, _b, _c) = self._seed_three(client)
        csv = f"{a['id']},10\n{a['id']},20\n"
        r = client.post("/api/v1/sellers/me/stock/bulk-csv",
                        json={"csv": csv}, headers=_auth())
        assert r.status_code == 422
        assert "duplicate" in r.json()["detail"].lower()

    def test_bad_qty_422s(self, client):
        _, (a, _b, _c) = self._seed_three(client)
        csv = f"{a['id']},notanumber\n"
        r = client.post("/api/v1/sellers/me/stock/bulk-csv",
                        json={"csv": csv}, headers=_auth())
        assert r.status_code == 422

    def test_negative_qty_422s(self, client):
        _, (a, _b, _c) = self._seed_three(client)
        csv = f"{a['id']},-3\n"
        r = client.post("/api/v1/sellers/me/stock/bulk-csv",
                        json={"csv": csv}, headers=_auth())
        assert r.status_code == 422

    def test_wrong_column_count_422s(self, client):
        _, (a, _b, _c) = self._seed_three(client)
        csv = f"{a['id']},5,extra\n"
        r = client.post("/api/v1/sellers/me/stock/bulk-csv",
                        json={"csv": csv}, headers=_auth())
        assert r.status_code == 422
        assert "column" in r.json()["detail"].lower()

    def test_empty_body_422s(self, client):
        # empty string trips the min_length=1 constraint at the schema layer.
        r = client.post("/api/v1/sellers/me/stock/bulk-csv",
                        json={"csv": ""}, headers=_auth())
        assert r.status_code == 422

    def test_no_data_rows_422s(self, client):
        # Header-only CSV — no rows to apply.
        r = client.post("/api/v1/sellers/me/stock/bulk-csv",
                        json={"csv": "listing_id,stock_qty\n"}, headers=_auth())
        assert r.status_code == 422

    def test_all_or_nothing_on_parse_error(self, client):
        _, (a, b, _c) = self._seed_three(client)
        # A valid row followed by an invalid row → the whole call must roll back / never
        # apply the valid row (all-or-nothing).
        csv = f"{a['id']},50\n{b['id']},bad\n"
        r = client.post("/api/v1/sellers/me/stock/bulk-csv",
                        json={"csv": csv}, headers=_auth())
        assert r.status_code == 422
        # Read back — a's stock should still be its original 1, not 50.
        rf = client.get("/api/v1/shops/mine", headers=_auth())
        all_listings = [li for s in rf.json()["shops"] for li in s["listings"]]
        la = next(li for li in all_listings if li["id"] == a["id"])
        assert la["stock_qty"] == 1

    def test_scope_gating(self, client):
        r = client.post("/api/v1/sellers/me/stock/bulk-csv",
                        json={"csv": "x,1\n"})
        assert r.status_code in (401, 403)
        r = client.post("/api/v1/sellers/me/stock/bulk-csv",
                        json={"csv": "x,1\n"}, headers=_auth(scopes=("read:feed",)))
        assert r.status_code == 403
