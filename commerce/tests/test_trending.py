"""§8 trending rail — the deterministic queue of boosted PRODUCTS near the buyer.

Two layers, mirroring test_boost:
  * direct service-layer unit tests of build_slate (listing-only eligibility, widest-tier roll-up,
    contention slot lifetime, bucket sharing, live/in-stock filtering, no-PII shape);
  * HTTP integration through real RS256 tokens (auth gate, lat/lng bounds, the fail-open cache).

Contract (changed from the prior SHOP rail — these tests pin the new behaviour):
  * trending shows boosted LISTINGS as product cards; SHOP-level boosts are EXCLUDED (they appear
    only in the in-feed sponsored lane). A listing with two grants collapses to ONE card at its
    WIDEST tier. Scope (mtaa near/far, sovereign nationwide) reuses the Boost machinery.
  * the slate carries a full bounded QUEUE plus `visible_slots` + `slot_seconds` (per-card lifetime,
    always > 5 s, shrinking under contention) — the CLIENT owns the per-slot decay; there is NO
    server-side rotation window.
  * a sold-out / inactive listing never surfaces (don't advertise a dead, buyable-looking card).
  * the cache fails OPEN — a Redis blip recomputes from the DB rather than erroring.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient

from PE.commerce.core.config import settings
from PE.commerce.core.database import get_db
from PE.commerce.main import app
from PE.commerce.models.boost import (
    BOOST_HUSTLE, BOOST_MTAA, BOOST_SOVEREIGN, TARGET_LISTING, TARGET_SHOP,
)
from PE.commerce.models.listing import Listing
from PE.commerce.models.seller import Seller, Shop
from PE.commerce.services import boost, proximity, trending

_KEYS = Path(__file__).resolve().parents[3] / "PE" / "dev" / "keys"
_PRIVATE = (_KEYS / "insar_jwt_private.pem").read_text()

_LAT, _LNG = -1.2920, 36.8219          # Nairobi CBD-ish
_FAR_LAT, _FAR_LNG = -4.0435, 39.6682  # Mombasa — ~440 km away
_NOW = datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc)


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
    # Default to a cold cache (fail-open None on get, no-op set) so the HTTP tests exercise the real
    # recompute path deterministically, regardless of whether a real Redis is running.
    with patch("PE.commerce.services.trending_cache.get", return_value=None), \
         patch("PE.commerce.services.trending_cache.set"):
        yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


# ----------------------------- seeding -----------------------------

def _seed_shop(db, *, user_uuid, lat=_LAT, lng=_LNG, name="Shop", category=None,
               stock=5, is_active=True, with_listing=True, avatar_url=None, media_urls=None):
    seller = Seller(user_uuid=user_uuid, display_name="S")
    db.add(seller)
    db.flush()
    shop = Shop(seller_id=seller.id, name=name, category=category, lat=lat, lng=lng,
                avatar_url=avatar_url)
    proximity.set_location(shop, lat, lng)
    db.add(shop)
    db.flush()
    listing = None
    if with_listing:
        listing = Listing(shop_id=shop.id, seller_id=seller.id, title=f"{name} item",
                          price_cents=1000, currency="KES", stock_qty=stock, is_active=is_active,
                          lat=lat, lng=lng, media_urls=media_urls)
        proximity.set_location(listing, lat, lng)
        db.add(listing)
    db.commit()
    return seller, shop, listing


# ----------------------------- eligibility + roll-up -----------------------------

def test_listing_boost_appears_as_product_card(db_session):
    _, shop, listing = _seed_shop(db_session, user_uuid="seller-A", category="restaurant")
    boost.grant_boost(db_session, "seller-A", target_type=TARGET_LISTING, target_id=listing.id,
                      tier=BOOST_MTAA, now=_NOW)
    slate = trending.build_slate(db_session, _LAT + 0.005, _LNG, now=_NOW)
    assert [c.listing_id for c in slate.cards] == [listing.id]
    card = slate.cards[0]
    assert card.title == listing.title
    assert card.price_cents == 1000
    assert card.currency == "KES"
    assert card.category == "restaurant"          # category comes off the owning Shop
    assert card.seller_id == shop.seller_id
    assert card.boost_tier == BOOST_MTAA


def test_promoted_card_carries_product_image(db_session):
    # A boosted product surfaces its OWN lead image (first non-video media URL) so the promotion
    # shows the item for sale, not a shop logo. Derived from Listing.media_urls in the same batch as
    # category (no N+1), via the shared quick_buys.first_image_url (video-skip rule in one place).
    _, _, listing = _seed_shop(
        db_session, user_uuid="seller-img", category="bakery",
        media_urls=json.dumps(["/uploads/trade/videos/clip.mp4", "/uploads/trade/images/loaf.webp"]),
    )
    boost.grant_boost(db_session, "seller-img", target_type=TARGET_LISTING, target_id=listing.id,
                      tier=BOOST_MTAA, now=_NOW)
    slate = trending.build_slate(db_session, _LAT + 0.005, _LNG, now=_NOW)
    # The video is skipped; the first IMAGE leads the card.
    assert slate.cards[0].image_url == "/uploads/trade/images/loaf.webp"


def test_promoted_card_without_image_falls_back_to_none(db_session):
    # No media (or video-only) ⇒ image_url is None so the client shows the category glyph, never a
    # broken image or a video URL as a still.
    _, _, listing = _seed_shop(db_session, user_uuid="seller-noimg", category="bakery",
                               media_urls=None)
    boost.grant_boost(db_session, "seller-noimg", target_type=TARGET_LISTING, target_id=listing.id,
                      tier=BOOST_MTAA, now=_NOW)
    slate = trending.build_slate(db_session, _LAT + 0.005, _LNG, now=_NOW)
    assert slate.cards[0].image_url is None


def test_shop_boost_is_excluded_from_trending(db_session):
    # The contract INVERTED: a shop-level boost no longer surfaces in trending (it lives only in the
    # in-feed sponsored lane). Trending is listing-target only.
    _, shop, _ = _seed_shop(db_session, user_uuid="seller-A", category="bakery")
    boost.grant_boost(db_session, "seller-A", target_type=TARGET_SHOP, target_id=shop.id,
                      tier=BOOST_SOVEREIGN, now=_NOW)
    slate = trending.build_slate(db_session, _LAT, _LNG, now=_NOW)
    assert slate.cards == []
    assert slate.active_count == 0


def test_shop_boost_does_not_steal_the_candidate_cap_from_listings(db_session):
    # A pile of shop boosts must not consume the eligibility cap and starve the listing queue: the
    # listing-only filter is pushed into SQL, so the one boosted LISTING still surfaces.
    for i in range(settings.trending_visible_cap * 2):
        s, shop, _ = _seed_shop(db_session, user_uuid=f"shoponly-{i}", lat=_LAT, lng=_LNG,
                                name=f"ShopOnly{i}")
        boost.grant_boost(db_session, f"shoponly-{i}", target_type=TARGET_SHOP, target_id=shop.id,
                          tier=BOOST_SOVEREIGN, now=_NOW)
    _, _, listing = _seed_shop(db_session, user_uuid="seller-L", lat=_LAT, lng=_LNG, name="RealProduct")
    boost.grant_boost(db_session, "seller-L", target_type=TARGET_LISTING, target_id=listing.id,
                      tier=BOOST_MTAA, now=_NOW)
    slate = trending.build_slate(db_session, _LAT, _LNG, now=_NOW)
    assert [c.listing_id for c in slate.cards] == [listing.id]


def test_mtaa_does_not_reach_far_buyer(db_session):
    _, _, listing = _seed_shop(db_session, user_uuid="seller-A")
    boost.grant_boost(db_session, "seller-A", target_type=TARGET_LISTING, target_id=listing.id,
                      tier=BOOST_MTAA, now=_NOW)
    assert trending.build_slate(db_session, _FAR_LAT, _FAR_LNG, now=_NOW).cards == []


def test_sovereign_reaches_far_buyer(db_session):
    _, _, listing = _seed_shop(db_session, user_uuid="seller-A")
    boost.grant_boost(db_session, "seller-A", target_type=TARGET_LISTING, target_id=listing.id,
                      tier=BOOST_SOVEREIGN, now=_NOW)
    slate = trending.build_slate(db_session, _FAR_LAT, _FAR_LNG, now=_NOW)
    assert [c.listing_id for c in slate.cards] == [listing.id]


def test_empty_slate_when_no_boosts(db_session):
    _seed_shop(db_session, user_uuid="seller-A")  # a shop+listing, but no boost
    slate = trending.build_slate(db_session, _LAT, _LNG, now=_NOW)
    assert slate.cards == []
    assert slate.active_count == 0
    assert slate.slot_seconds == settings.trending_base_slot_s
    assert slate.visible_slots == settings.trending_visible_cap
    assert slate.poll_seconds == settings.trending_poll_seconds


def test_widest_tier_wins_when_a_listing_has_two_grants(db_session):
    _, _, listing = _seed_shop(db_session, user_uuid="seller-A")
    # The SAME listing boosted at mtaa AND sovereign — it collapses to one card at the widest tier.
    boost.grant_boost(db_session, "seller-A", target_type=TARGET_LISTING, target_id=listing.id,
                      tier=BOOST_MTAA, now=_NOW)
    boost.grant_boost(db_session, "seller-A", target_type=TARGET_LISTING, target_id=listing.id,
                      tier=BOOST_SOVEREIGN, now=_NOW)
    slate = trending.build_slate(db_session, _LAT, _LNG, now=_NOW)
    assert len(slate.cards) == 1
    assert slate.cards[0].boost_tier == BOOST_SOVEREIGN


def test_out_of_stock_listing_is_not_surfaced(db_session):
    # A boost can outlive the product's availability; a sold-out listing must NOT advertise a price.
    _, _, listing = _seed_shop(db_session, user_uuid="seller-A", stock=0)
    boost.grant_boost(db_session, "seller-A", target_type=TARGET_LISTING, target_id=listing.id,
                      tier=BOOST_SOVEREIGN, now=_NOW)
    slate = trending.build_slate(db_session, _LAT, _LNG, now=_NOW)
    assert slate.cards == []
    assert slate.active_count == 0


def test_inactive_listing_is_not_surfaced(db_session):
    _, _, listing = _seed_shop(db_session, user_uuid="seller-A", is_active=False)
    boost.grant_boost(db_session, "seller-A", target_type=TARGET_LISTING, target_id=listing.id,
                      tier=BOOST_SOVEREIGN, now=_NOW)
    slate = trending.build_slate(db_session, _LAT, _LNG, now=_NOW)
    assert slate.cards == []
    assert slate.active_count == 0


# ----------------------------- eligible_grants target_type unit -----------------------------

def test_eligible_grants_target_type_counts_only_that_kind(db_session):
    # Direct unit on the boost layer: with target_type='listing' the cap is spent on LISTING grants
    # only, so shop grants in the same locality don't appear and can't crowd them out.
    _, shop, listing = _seed_shop(db_session, user_uuid="seller-A")
    boost.grant_boost(db_session, "seller-A", target_type=TARGET_SHOP, target_id=shop.id,
                      tier=BOOST_SOVEREIGN, now=_NOW)
    boost.grant_boost(db_session, "seller-A", target_type=TARGET_LISTING, target_id=listing.id,
                      tier=BOOST_SOVEREIGN, now=_NOW)
    listing_only = boost.eligible_grants(db_session, _LAT, _LNG, now=_NOW, target_type=TARGET_LISTING)
    assert {g.target_type for g in listing_only} == {TARGET_LISTING}
    assert [g.target_id for g in listing_only] == [listing.id]
    # Default (no target_type) still pulls both kinds (the feed lane is unchanged).
    both = boost.eligible_grants(db_session, _LAT, _LNG, now=_NOW)
    assert {g.target_type for g in both} == {TARGET_SHOP, TARGET_LISTING}


# ----------------------------- contention slot lifetime -----------------------------

def _seed_boosted_listing(db, i, *, lat=_LAT, lng=_LNG, tier=BOOST_MTAA):
    _, _, listing = _seed_shop(db, user_uuid=f"seller-{i}", lat=lat, lng=lng, name=f"Shop{i}")
    boost.grant_boost(db, f"seller-{i}", target_type=TARGET_LISTING, target_id=listing.id,
                      tier=tier, now=_NOW)
    return listing


def test_quiet_locality_keeps_full_base_slot_and_shows_all(db_session):
    # A handful of boosted products, all under the visible cap → the whole queue, base slot lifetime.
    for i in range(3):
        _seed_boosted_listing(db_session, i, lat=_LAT + i * 0.001)
    slate = trending.build_slate(db_session, _LAT, _LNG, now=_NOW)
    assert slate.active_count == 3
    assert len(slate.cards) == 3
    assert slate.slot_seconds == settings.trending_base_slot_s


def test_contention_shrinks_slot_seconds_but_keeps_full_queue(db_session):
    # More boosted products than the visible cap → slot lifetime shrinks below base (toward the
    # floor), but the FULL queue is returned (the client cycles it through the freed slots).
    cap = settings.trending_visible_cap
    for i in range(cap * 2):
        _seed_boosted_listing(db_session, i)
    slate = trending.build_slate(db_session, _LAT, _LNG, now=_NOW)
    assert slate.active_count == cap * 2
    assert len(slate.cards) == cap * 2          # full queue, NOT pre-sliced server-side
    assert slate.visible_slots == cap
    assert slate.slot_seconds < settings.trending_base_slot_s
    assert slate.slot_seconds >= settings.trending_min_slot_s
    assert slate.slot_seconds > 5               # the owner's hard requirement


def test_slot_seconds_never_drops_to_five_or_below(db_session):
    # Even at extreme contention the per-card lifetime stays readable (> 5 s).
    for i in range(settings.trending_visible_cap * 8):
        _seed_boosted_listing(db_session, i)
    slate = trending.build_slate(db_session, _LAT, _LNG, now=_NOW)
    assert slate.slot_seconds > 5
    assert slate.slot_seconds == settings.trending_min_slot_s


def test_queue_is_deterministically_ordered_widest_tier_first(db_session):
    # Widest reach leads the queue (so the client's first slots show the strongest boosts).
    mtaa = _seed_boosted_listing(db_session, 0, tier=BOOST_MTAA)
    sovereign = _seed_boosted_listing(db_session, 1, tier=BOOST_SOVEREIGN)
    hustle = _seed_boosted_listing(db_session, 2, tier=BOOST_HUSTLE)
    slate = trending.build_slate(db_session, _LAT, _LNG, now=_NOW)
    order = [c.listing_id for c in slate.cards]
    assert order.index(sovereign.id) < order.index(hustle.id) < order.index(mtaa.id)


# ----------------------------- bucket sharing + determinism -----------------------------

def test_nearby_buyers_share_one_bucket(db_session):
    # Two buyers a few metres apart snap to the same bucket key → identical queue (cache-shareable).
    _, _, listing = _seed_shop(db_session, user_uuid="seller-A")
    boost.grant_boost(db_session, "seller-A", target_type=TARGET_LISTING, target_id=listing.id,
                      tier=BOOST_MTAA, now=_NOW)
    _, _, k1 = trending.bucket_for(_LAT, _LNG)
    _, _, k2 = trending.bucket_for(_LAT + 0.0001, _LNG + 0.0001)  # ~15 m away
    assert k1 == k2
    s1 = trending.build_slate(db_session, _LAT, _LNG, now=_NOW)
    s2 = trending.build_slate(db_session, _LAT + 0.0001, _LNG + 0.0001, now=_NOW)
    assert [c.listing_id for c in s1.cards] == [c.listing_id for c in s2.cards]
    assert s1.cards[0].distance_m == s2.cards[0].distance_m  # bucket-centre distance, identical


# ----------------------------- HTTP -----------------------------

def test_trending_endpoint_returns_boosted_product(client, db_session):
    _, _, listing = _seed_shop(db_session, user_uuid="seller-A", category="electronics")
    boost.grant_boost(db_session, "seller-A", target_type=TARGET_LISTING, target_id=listing.id,
                      tier=BOOST_MTAA)
    r = client.get(f"/api/v1/trending?lat={_LAT}&lng={_LNG}", headers=_buyer_auth())
    assert r.status_code == 200, r.text
    body = r.json()
    assert any(c["listing_id"] == listing.id and c["category"] == "electronics"
               for c in body["cards"])
    assert body["slot_seconds"] >= settings.trending_min_slot_s
    assert body["slot_seconds"] > 5
    assert body["visible_slots"] == settings.trending_visible_cap
    assert body["poll_seconds"] == settings.trending_poll_seconds


def test_trending_requires_token(client):
    assert client.get(f"/api/v1/trending?lat={_LAT}&lng={_LNG}").status_code == 401


def test_trending_rejects_out_of_range_coords(client):
    assert client.get("/api/v1/trending?lat=200&lng=0", headers=_buyer_auth()).status_code == 422
    assert client.get("/api/v1/trending?lat=0&lng=999", headers=_buyer_auth()).status_code == 422


def test_trending_card_carries_no_pii(client, db_session):
    # The card must surface only opaque ids + seller-published fields — never the weespas user id /
    # any account PII. The seller's user_uuid must NOT leak into the payload.
    _, _, listing = _seed_shop(db_session, user_uuid="secret-user-uuid-123")
    boost.grant_boost(db_session, "secret-user-uuid-123", target_type=TARGET_LISTING,
                      target_id=listing.id, tier=BOOST_MTAA)
    body = client.get(f"/api/v1/trending?lat={_LAT}&lng={_LNG}", headers=_buyer_auth()).json()
    assert "secret-user-uuid-123" not in json.dumps(body)
    allowed = {"listing_id", "seller_id", "title", "price_cents", "currency", "category",
               "property_uuid", "distance_m", "boost_tier", "image_url"}
    assert body["cards"], "expected at least one card to assert the shape against"
    for card in body["cards"]:
        assert set(card.keys()) == allowed


def test_trending_fails_open_when_cache_unavailable(db_session):
    # If Redis is down, get() raises internally and is swallowed → None → recompute from DB. We
    # simulate the cache module returning None + a no-op set and assert the endpoint still serves.
    app.dependency_overrides.clear()

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    _, _, listing = _seed_shop(db_session, user_uuid="seller-A")
    boost.grant_boost(db_session, "seller-A", target_type=TARGET_LISTING, target_id=listing.id,
                      tier=BOOST_MTAA)
    try:
        with patch("PE.commerce.services.trending_cache.get", return_value=None), \
             patch("PE.commerce.services.trending_cache.set"):
            c = TestClient(app, raise_server_exceptions=False)
            r = c.get(f"/api/v1/trending?lat={_LAT}&lng={_LNG}", headers=_buyer_auth())
        assert r.status_code == 200
        assert any(card["listing_id"] == listing.id for card in r.json()["cards"])
    finally:
        app.dependency_overrides.clear()


def test_trending_cache_ttl_is_the_poll_window(db_session):
    # The cache TTL must be poll_seconds (queue membership changes slowly; decay is client-local).
    app.dependency_overrides.clear()

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    _, _, listing = _seed_shop(db_session, user_uuid="seller-A")
    boost.grant_boost(db_session, "seller-A", target_type=TARGET_LISTING, target_id=listing.id,
                      tier=BOOST_MTAA)
    captured = {}
    try:
        def _capture_set(bucket, payload, ttl, *a, **kw):
            captured["ttl"] = ttl

        with patch("PE.commerce.services.trending_cache.get", return_value=None), \
             patch("PE.commerce.services.trending_cache.set", side_effect=_capture_set):
            c = TestClient(app, raise_server_exceptions=False)
            r = c.get(f"/api/v1/trending?lat={_LAT}&lng={_LNG}", headers=_buyer_auth())
        assert r.status_code == 200
        assert captured["ttl"] == settings.trending_poll_seconds
    finally:
        app.dependency_overrides.clear()
