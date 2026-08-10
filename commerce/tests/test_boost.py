"""§8.3 Boost — reach economy: allowance ledger, grant idempotency, scope eligibility, the
sponsored lane interleaving, and authz. Mixes direct service-layer unit tests (the ledger /
eligibility maths) with full HTTP integration through real RS256 tokens (the same harness as
test_promotion).

The cardinal invariant under test: the ORGANIC lane is never re-ordered by a Boost — a Boost only
ADDS labelled sponsored slots. And the allowance ledger never over-spends, even on a replay.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from PE.commerce.core.database import get_db
from PE.commerce.main import app
from PE.commerce.models.boost import BOOST_MTAA, BOOST_SOVEREIGN
from PE.commerce.models.listing import Listing
from PE.commerce.models.seller import Seller, Shop
from PE.commerce.services import boost, proximity

_KEYS = Path(__file__).resolve().parents[3] / "PE" / "dev" / "keys"
_PRIVATE = (_KEYS / "insar_jwt_private.pem").read_text()

_LAT, _LNG = -1.2920, 36.8219          # Nairobi CBD-ish — buyer + local shop
_FAR_LAT, _FAR_LNG = -4.0435, 39.6682  # Mombasa — ~440 km away (outside any radius tier)


def _mint(sub, scopes):
    return jwt.encode(
        {"sub": sub, "role": "user", "scope": "commerce_trade", "scopes": list(scopes),
         "exp": datetime.now(timezone.utc) + timedelta(minutes=10)},
        _PRIVATE, algorithm="RS256",
    )


def _seller_auth(sub="seller-A"):
    return {"Authorization": f"Bearer {_mint(sub, ('read:feed', 'create:trades'))}"}


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


# ----------------------------- direct-model seeding (service unit tests) -----------------------------

def _seed_seller_listing(db, *, user_uuid="seller-A", lat=_LAT, lng=_LNG, title="Item", stock=5):
    seller = Seller(user_uuid=user_uuid, display_name="S")
    db.add(seller)
    db.flush()
    shop = Shop(seller_id=seller.id, name="Shop", lat=lat, lng=lng)
    proximity.set_location(shop, lat, lng)
    db.add(shop)
    db.flush()
    listing = Listing(shop_id=shop.id, seller_id=seller.id, title=title, price_cents=1000,
                      currency="KES", stock_qty=stock, is_active=True, lat=lat, lng=lng)
    proximity.set_location(listing, lat, lng)
    db.add(listing)
    db.commit()
    return seller, shop, listing


# ----------------------------- allowance ledger -----------------------------

def test_consume_decrements_until_cap_then_fails_closed(db_session):
    seller, _, _ = _seed_seller_listing(db_session)
    cap = boost.tier_daily_cap(BOOST_SOVEREIGN)  # smallest cap (3) — quick to exhaust
    bday = boost.business_date(datetime.now(timezone.utc))
    for i in range(cap):
        assert boost._consume_allowance(db_session, seller.id, BOOST_SOVEREIGN, bday) is True
        db_session.commit()
        assert boost.remaining_allowance(db_session, seller.id, BOOST_SOVEREIGN) == cap - (i + 1)
    # One past the cap → fail closed.
    assert boost._consume_allowance(db_session, seller.id, BOOST_SOVEREIGN, bday) is False
    assert boost.remaining_allowance(db_session, seller.id, BOOST_SOVEREIGN) == 0


def test_allowance_resets_on_a_new_business_day(db_session):
    seller, _, _ = _seed_seller_listing(db_session)
    today = datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc)
    tomorrow = today + timedelta(days=1)
    # Spend today's full Sovereign quota.
    bday = boost.business_date(today)
    for _ in range(boost.tier_daily_cap(BOOST_SOVEREIGN)):
        assert boost._consume_allowance(db_session, seller.id, BOOST_SOVEREIGN, bday)
        db_session.commit()
    assert boost.remaining_allowance(db_session, seller.id, BOOST_SOVEREIGN, now=today) == 0
    # Tomorrow is a fresh row at full cap (the midnight reset, no job).
    assert boost.remaining_allowance(db_session, seller.id, BOOST_SOVEREIGN, now=tomorrow) \
        == boost.tier_daily_cap(BOOST_SOVEREIGN)


# ----------------------------- grant_boost: idempotency + ownership + bounds -----------------------------

def test_grant_is_idempotent_and_does_not_double_charge(db_session):
    seller, _, listing = _seed_seller_listing(db_session)
    before = boost.remaining_allowance(db_session, seller.id, BOOST_MTAA)
    g1 = boost.grant_boost(db_session, "seller-A", target_type="listing", target_id=listing.id, tier=BOOST_MTAA)
    g2 = boost.grant_boost(db_session, "seller-A", target_type="listing", target_id=listing.id, tier=BOOST_MTAA)
    assert g1.id == g2.id                       # replay returns the same grant
    after = boost.remaining_allowance(db_session, seller.id, BOOST_MTAA)
    assert after == before - 1                  # exactly one chance spent, not two


def test_grant_unknown_target_is_not_owned_returns_none(db_session):
    _seed_seller_listing(db_session)
    # seller-B owns nothing → 404 path (None), and no chance is spent (no seller row created).
    assert boost.grant_boost(db_session, "seller-B", target_type="listing",
                             target_id="does-not-exist", tier=BOOST_MTAA) is None


def test_grant_cross_owner_target_returns_none(db_session):
    _, _, listing = _seed_seller_listing(db_session, user_uuid="seller-A")
    other = Seller(user_uuid="seller-B", display_name="B")
    db_session.add(other)
    db_session.commit()
    # seller-B targeting seller-A's listing → not owned → None (router 404, no leak).
    assert boost.grant_boost(db_session, "seller-B", target_type="listing",
                             target_id=listing.id, tier=BOOST_MTAA) is None


def test_grant_bad_tier_and_duration_raise(db_session):
    _, _, listing = _seed_seller_listing(db_session)
    with pytest.raises(boost.BoostError):
        boost.grant_boost(db_session, "seller-A", target_type="listing", target_id=listing.id, tier="galaxy")
    with pytest.raises(boost.BoostError):
        boost.grant_boost(db_session, "seller-A", target_type="listing", target_id=listing.id,
                          tier=BOOST_MTAA, duration_seconds=1)  # below min


def test_grant_quota_exceeded_raises(db_session):
    seller, _, listing = _seed_seller_listing(db_session)
    # Pre-spend the whole Sovereign quota directly, then a real grant must 429 (and not crash).
    bday = boost.business_date(datetime.now(timezone.utc))
    for _ in range(boost.tier_daily_cap(BOOST_SOVEREIGN)):
        boost._consume_allowance(db_session, seller.id, BOOST_SOVEREIGN, bday)
        db_session.commit()
    with pytest.raises(boost.QuotaExceeded):
        boost.grant_boost(db_session, "seller-A", target_type="listing",
                          target_id=listing.id, tier=BOOST_SOVEREIGN)


# ----------------------------- scope eligibility -----------------------------

def test_mtaa_reaches_near_not_far(db_session):
    _, _, listing = _seed_seller_listing(db_session, lat=_LAT, lng=_LNG)
    boost.grant_boost(db_session, "seller-A", target_type="listing", target_id=listing.id, tier=BOOST_MTAA)
    # A buyer 1 km away is inside the 10 km Mtaa radius; a buyer in Mombasa is not.
    near = boost.eligible_grants(db_session, _LAT + 0.005, _LNG)
    far = boost.eligible_grants(db_session, _FAR_LAT, _FAR_LNG)
    assert len(near) == 1 and len(far) == 0


def test_sovereign_reaches_nationwide(db_session):
    _, _, listing = _seed_seller_listing(db_session, lat=_LAT, lng=_LNG)
    boost.grant_boost(db_session, "seller-A", target_type="listing", target_id=listing.id, tier=BOOST_SOVEREIGN)
    # A far buyer (different region) still sees a Sovereign grant — that is the point of the tier.
    far = boost.eligible_grants(db_session, _FAR_LAT, _FAR_LNG)
    assert len(far) == 1 and far[0].scope_kind == "nation"


def _add_listing(db, shop, seller, *, title, stock=5):
    listing = Listing(shop_id=shop.id, seller_id=seller.id, title=title, price_cents=1000,
                      currency="KES", stock_qty=stock, is_active=True, lat=shop.lat, lng=shop.lng)
    proximity.set_location(listing, shop.lat, shop.lng)
    db.add(listing)
    db.flush()
    return listing


def test_per_shop_fairness_cap_bounds_one_shops_sponsored_slots(db_session):
    # A single shop boosting MANY listings must not flood the sponsored lane — the per-shop cap
    # (default 2) bounds how many slots that one shop can occupy, so other boosted shops aren't
    # crowded out (§8.3 Boost = paid REACH, not a takeover).
    from PE.commerce.core.config import settings
    from PE.commerce.services import feed as feed_service

    seller, shop, _ = _seed_seller_listing(db_session, user_uuid="seller-flood", title="L0")
    for i in range(1, 4):  # shop now has 4 listings total (L0..L3)
        _add_listing(db_session, shop, seller, title=f"L{i}")
    # A shop-target Sovereign grant expands to ALL of the shop's listings — the flood scenario.
    boost.grant_boost(db_session, "seller-flood", target_type="shop", target_id=shop.id,
                      tier=BOOST_SOVEREIGN)
    db_session.commit()

    # A FAR buyer (nothing local) so every one of the shop's listings is a SPONSORED candidate.
    sponsored = feed_service._sponsored_listings(
        db_session, _FAR_LAT, _FAR_LNG, exclude_ids=set(),
        now=datetime.now(timezone.utc),
    )
    assert len(sponsored) == settings.feed_sponsored_max_per_shop  # capped, not all 4
    assert all(str(s.listing.shop_id) == str(shop.id) for s in sponsored)


def test_per_shop_cap_disabled_returns_all(db_session, monkeypatch):
    # cap <= 0 disables the fairness pass — every eligible slot is returned (the escape hatch).
    from PE.commerce.core.config import settings
    from PE.commerce.services import feed as feed_service

    monkeypatch.setattr(settings, "feed_sponsored_max_per_shop", 0)
    seller, shop, _ = _seed_seller_listing(db_session, user_uuid="seller-flood", title="L0")
    for i in range(1, 4):
        _add_listing(db_session, shop, seller, title=f"L{i}")
    boost.grant_boost(db_session, "seller-flood", target_type="shop", target_id=shop.id,
                      tier=BOOST_SOVEREIGN)
    db_session.commit()

    sponsored = feed_service._sponsored_listings(
        db_session, _FAR_LAT, _FAR_LNG, exclude_ids=set(),
        now=datetime.now(timezone.utc),
    )
    assert len(sponsored) == 4  # all of the shop's listings, no cap applied


def test_approved_override_raises_a_shops_cap_above_default(db_session):
    # An APPROVED per-shop override (item 1) lets one shop occupy MORE than the global default
    # sponsored slots — without touching the default for everyone else.
    from PE.commerce.core.config import settings
    from PE.commerce.services import boost_cap
    from PE.commerce.services import feed as feed_service

    assert settings.feed_sponsored_max_per_shop == 2  # baseline the override rises above
    seller, shop, _ = _seed_seller_listing(db_session, user_uuid="seller-flood", title="L0")
    for i in range(1, 4):  # 4 listings total
        _add_listing(db_session, shop, seller, title=f"L{i}")
    boost.grant_boost(db_session, "seller-flood", target_type="shop", target_id=shop.id,
                      tier=BOOST_SOVEREIGN)
    # Approve an absolute cap of 4 for this shop.
    row = boost_cap.apply_for_override(db_session, "seller-flood", shop.id, requested_cap=4)
    boost_cap.decide_override(db_session, "staff-1", row.id, approve=True)
    db_session.commit()

    sponsored = feed_service._sponsored_listings(
        db_session, _FAR_LAT, _FAR_LNG, exclude_ids=set(),
        now=datetime.now(timezone.utc),
    )
    assert len(sponsored) == 4  # override cap (4), not the default (2)


def test_pending_override_does_not_change_the_default_cap(db_session):
    # A PENDING (un-approved) override is inert — the shop still gets only the default slots.
    from PE.commerce.core.config import settings
    from PE.commerce.services import boost_cap
    from PE.commerce.services import feed as feed_service

    seller, shop, _ = _seed_seller_listing(db_session, user_uuid="seller-flood", title="L0")
    for i in range(1, 4):
        _add_listing(db_session, shop, seller, title=f"L{i}")
    boost.grant_boost(db_session, "seller-flood", target_type="shop", target_id=shop.id,
                      tier=BOOST_SOVEREIGN)
    boost_cap.apply_for_override(db_session, "seller-flood", shop.id, requested_cap=4)  # left pending
    db_session.commit()

    sponsored = feed_service._sponsored_listings(
        db_session, _FAR_LAT, _FAR_LNG, exclude_ids=set(),
        now=datetime.now(timezone.utc),
    )
    assert len(sponsored) == settings.feed_sponsored_max_per_shop  # unchanged default


# ----------------------------- fill-rate lottery (§8.3, OFF by default) -----------------------------

def _mk_scored(feed_service, tier, dist, lid):
    """A minimal ScoredListing carrying just the fields the lottery reads (tier) + a listing stub
    with an id/shop_id, so we can unit-test _lottery_order without touching the DB."""
    class _L:
        id = lid
        shop_id = lid  # unique shop per item → the per-shop cap never interferes here
    return feed_service.ScoredListing(
        listing=_L(), distance_m=dist, score=0.0, is_sponsored=True, boost_tier=tier,
    )


def test_lottery_is_deterministic_for_a_fixed_seed(monkeypatch):
    from PE.commerce.core.config import settings
    from PE.commerce.services import feed as feed_service

    monkeypatch.setattr(settings, "feed_sponsored_lottery_seed", 12345)
    items = [_mk_scored(feed_service, BOOST_MTAA, i, f"L{i}") for i in range(8)]
    a = feed_service._lottery_order(list(items), _LAT, _LNG, datetime.now(timezone.utc))
    b = feed_service._lottery_order(list(items), _LAT, _LNG, datetime.now(timezone.utc))
    assert [x.listing.id for x in a] == [x.listing.id for x in b]  # same seed → same order


def test_lottery_is_a_permutation_no_drops(monkeypatch):
    from PE.commerce.core.config import settings
    from PE.commerce.services import feed as feed_service

    monkeypatch.setattr(settings, "feed_sponsored_lottery_seed", 7)
    items = [_mk_scored(feed_service, BOOST_MTAA, i, f"L{i}") for i in range(10)]
    out = feed_service._lottery_order(list(items), _LAT, _LNG, datetime.now(timezone.utc))
    assert sorted(x.listing.id for x in out) == sorted(x.listing.id for x in items)


def test_lottery_favours_wider_tiers_over_a_sample(monkeypatch):
    # The point of the tier-weighted lottery: a wider (Sovereign) tier should land in the FIRST slot
    # more often than a narrow (Mtaa) tier across many seeds — without ever fully locking the narrow
    # one out. We vary the seed and count first-slot wins.
    from PE.commerce.core.config import settings
    from PE.commerce.services import feed as feed_service

    wide_first = 0
    narrow_first = 0
    trials = 200
    for seed in range(trials):
        monkeypatch.setattr(settings, "feed_sponsored_lottery_seed", seed)
        items = [
            _mk_scored(feed_service, BOOST_SOVEREIGN, 100.0, "WIDE"),
            _mk_scored(feed_service, BOOST_MTAA, 100.0, "NARROW"),
        ]
        winner = feed_service._lottery_order(items, _LAT, _LNG, datetime.now(timezone.utc))[0]
        if winner.listing.id == "WIDE":
            wide_first += 1
        else:
            narrow_first += 1
    assert wide_first > narrow_first  # wider tier wins the top slot more often
    assert narrow_first > 0           # ...but never fully locked out (the fill-rate the lottery buys)


def test_lottery_off_by_default_keeps_deterministic_order(db_session):
    # With the flag OFF (default), the lane stays in the tier→distance→id deterministic order —
    # the lottery code path is skipped entirely (cursor-safe, reproducible).
    from PE.commerce.core.config import settings
    from PE.commerce.services import feed as feed_service

    assert settings.feed_sponsored_lottery_enabled is False
    seller, shop, _ = _seed_seller_listing(db_session, user_uuid="s", title="L0")
    for i in range(1, 4):
        _add_listing(db_session, shop, seller, title=f"L{i}")
    boost.grant_boost(db_session, "s", target_type="shop", target_id=shop.id, tier=BOOST_SOVEREIGN)
    db_session.commit()
    a = feed_service._sponsored_listings(db_session, _FAR_LAT, _FAR_LNG, exclude_ids=set(),
                                         now=datetime.now(timezone.utc))
    b = feed_service._sponsored_listings(db_session, _FAR_LAT, _FAR_LNG, exclude_ids=set(),
                                         now=datetime.now(timezone.utc))
    assert [x.listing.id for x in a] == [x.listing.id for x in b]


# ----------------------------- sponsored lane through the feed (HTTP) -----------------------------

def _create_listing_http(client, *, title, lat=_LAT, lng=_LNG, stock=5, sub="seller-A",
                         is_short_video=False):
    shop = client.post("/api/v1/shops",
                       json={"name": "Shop", "lat": lat, "lng": lng, "display_name": "S"},
                       headers=_seller_auth(sub)).json()
    li = client.post(f"/api/v1/shops/{shop['id']}/listings",
                     json={"title": title, "price_cents": 1000, "stock_qty": stock,
                           "is_short_video": is_short_video},
                     headers=_seller_auth(sub)).json()
    return shop, li


def test_far_sovereign_listing_appears_sponsored_without_reordering_organic(client):
    # A local seller with one in-stock listing (organic). A FAR seller boosts Sovereign — their
    # listing must appear in the local buyer's feed as a SPONSORED slot, while the local organic
    # item stays exactly where it was.
    _create_listing_http(client, title="Local Tomatoes", lat=_LAT, lng=_LNG, sub="seller-local")
    _, far = _create_listing_http(client, title="Mombasa Mangoes", lat=_FAR_LAT, lng=_FAR_LNG, sub="seller-far")

    # Without a boost, the far listing is NOT in the local feed (out of radius).
    feed = client.get(f"/api/v1/feed?lat={_LAT}&lng={_LNG}", headers=_buyer_auth()).json()
    titles = [i["title"] for i in feed["items"]]
    assert "Local Tomatoes" in titles and "Mombasa Mangoes" not in titles

    # Far seller buys a Sovereign boost on their listing.
    r = client.post("/api/v1/boosts",
                    json={"target_type": "listing", "target_id": far["id"], "tier": "sovereign"},
                    headers=_seller_auth("seller-far"))
    assert r.status_code == 201, r.text

    feed = client.get(f"/api/v1/feed?lat={_LAT}&lng={_LNG}", headers=_buyer_auth()).json()
    by_title = {i["title"]: i for i in feed["items"]}
    # The local organic item is present and NOT sponsored.
    assert by_title["Local Tomatoes"]["is_sponsored"] is False
    # The far item now appears, flagged sponsored with its tier.
    assert "Mombasa Mangoes" in by_title
    assert by_title["Mombasa Mangoes"]["is_sponsored"] is True
    assert by_title["Mombasa Mangoes"]["boost_tier"] == "sovereign"


def test_sponsored_lane_honours_the_kind_toggle(client):
    # The §8 Videos/Listings toggle filters the SPONSORED lane too (in SQL now, not a Python post-
    # pass): a far seller boosts a short-VIDEO post; it must appear under ?kind=videos but be absent
    # under ?kind=listings — the sponsored lane can't leak the wrong post kind into a filtered view.
    _create_listing_http(client, title="Local Tomatoes", lat=_LAT, lng=_LNG, sub="seller-local")
    _, far = _create_listing_http(client, title="Mombasa Reel", lat=_FAR_LAT, lng=_FAR_LNG,
                                  sub="seller-far", is_short_video=True)
    r = client.post("/api/v1/boosts",
                    json={"target_type": "listing", "target_id": far["id"], "tier": "sovereign"},
                    headers=_seller_auth("seller-far"))
    assert r.status_code == 201, r.text

    def titles(kind):
        url = f"/api/v1/feed?lat={_LAT}&lng={_LNG}&kind={kind}"
        return [i["title"] for i in client.get(url, headers=_buyer_auth()).json()["items"]]

    # Videos view surfaces the boosted video; Listings view filters it out entirely.
    assert "Mombasa Reel" in titles("videos")
    assert "Mombasa Reel" not in titles("listings")


def test_sponsored_does_not_double_show_a_local_listing(client):
    # If a listing is BOTH organic (close) and boosted, it appears once — in the organic lane.
    _, li = _create_listing_http(client, title="Corner Maize", lat=_LAT, lng=_LNG, sub="seller-A")
    client.post("/api/v1/boosts",
                json={"target_type": "listing", "target_id": li["id"], "tier": "mtaa"},
                headers=_seller_auth("seller-A"))
    feed = client.get(f"/api/v1/feed?lat={_LAT}&lng={_LNG}", headers=_buyer_auth()).json()
    matches = [i for i in feed["items"] if i["title"] == "Corner Maize"]
    assert len(matches) == 1
    assert matches[0]["is_sponsored"] is False  # organic placement wins


def test_sold_out_boosted_listing_is_not_shown_sponsored(client):
    _create_listing_http(client, title="Local Tomatoes", lat=_LAT, lng=_LNG, sub="seller-local")
    _, far = _create_listing_http(client, title="Sold Out Far", lat=_FAR_LAT, lng=_FAR_LNG, stock=0, sub="seller-far")
    client.post("/api/v1/boosts",
                json={"target_type": "listing", "target_id": far["id"], "tier": "sovereign"},
                headers=_seller_auth("seller-far"))
    feed = client.get(f"/api/v1/feed?lat={_LAT}&lng={_LNG}", headers=_buyer_auth()).json()
    titles = [i["title"] for i in feed["items"]]
    assert "Sold Out Far" not in titles  # a boost cannot resurrect an out-of-stock listing


def test_sovereign_listing_reaches_a_buyer_with_no_local_organic_feed(client):
    # The regression this guards: a buyer in an EMPTY locality (nothing in local radius) must still
    # see a nationwide (Sovereign) boost. The sponsored lane previously required a non-empty organic
    # page to interleave into, so a far buyer got a wholly empty feed — silently dropping exactly the
    # promotions Sovereign reach is sold to deliver.
    _, far = _create_listing_http(client, title="Nairobi Nyama", lat=_LAT, lng=_LNG, sub="seller-nbo")
    client.post("/api/v1/boosts",
                json={"target_type": "listing", "target_id": far["id"], "tier": "sovereign"},
                headers=_seller_auth("seller-nbo"))

    # Buyer sits in Mombasa — the Nairobi shop is ~440 km away, so nothing is organically in range.
    feed = client.get(f"/api/v1/feed?lat={_FAR_LAT}&lng={_FAR_LNG}", headers=_buyer_auth()).json()
    by_title = {i["title"]: i for i in feed["items"]}
    assert "Nairobi Nyama" in by_title, "Sovereign boost must reach a buyer with an empty local feed"
    assert by_title["Nairobi Nyama"]["is_sponsored"] is True
    assert by_title["Nairobi Nyama"]["boost_tier"] == "sovereign"


def test_local_only_boost_does_not_reach_a_far_empty_buyer(client):
    # The counter-invariant: the empty-feed floor must NOT leak a RADIUS-scoped boost (mtaa/hustle)
    # to a buyer outside its circle. Only nation-scope grants reach a far buyer; the fix widens
    # reach for Sovereign, not for local tiers.
    _, li = _create_listing_http(client, title="Kangemi Mahindi", lat=_LAT, lng=_LNG, sub="seller-nbo")
    client.post("/api/v1/boosts",
                json={"target_type": "listing", "target_id": li["id"], "tier": "mtaa"},
                headers=_seller_auth("seller-nbo"))

    feed = client.get(f"/api/v1/feed?lat={_FAR_LAT}&lng={_FAR_LNG}", headers=_buyer_auth()).json()
    titles = [i["title"] for i in feed["items"]]
    assert "Kangemi Mahindi" not in titles  # a 10 km mtaa boost cannot reach 440 km away


# ----------------------------- HTTP authz + quota surface -----------------------------

def test_promote_requires_write_scope(client):
    _, li = _create_listing_http(client, title="X", sub="seller-A")
    r = client.post("/api/v1/boosts",
                    json={"target_type": "listing", "target_id": li["id"], "tier": "mtaa"},
                    headers=_buyer_auth())  # read-only token
    assert r.status_code == 403


def test_boost_cross_owner_is_404(client):
    _, li = _create_listing_http(client, title="X", sub="seller-A")
    r = client.post("/api/v1/boosts",
                    json={"target_type": "listing", "target_id": li["id"], "tier": "mtaa"},
                    headers=_seller_auth("seller-B"))
    assert r.status_code == 404


def test_quota_endpoint_reflects_spend(client):
    _, li = _create_listing_http(client, title="X", sub="seller-A")
    before = client.get("/api/v1/boosts/allowances", headers=_seller_auth("seller-A")).json()
    mtaa_before = next(t["remaining"] for t in before["tiers"] if t["tier"] == "mtaa")
    client.post("/api/v1/boosts",
                json={"target_type": "listing", "target_id": li["id"], "tier": "mtaa"},
                headers=_seller_auth("seller-A"))
    after = client.get("/api/v1/boosts/allowances", headers=_seller_auth("seller-A")).json()
    mtaa_after = next(t["remaining"] for t in after["tiers"] if t["tier"] == "mtaa")
    assert mtaa_after == mtaa_before - 1


def test_unknown_tier_is_422(client):
    _, li = _create_listing_http(client, title="X", sub="seller-A")
    r = client.post("/api/v1/boosts",
                    json={"target_type": "listing", "target_id": li["id"], "tier": "galaxy"},
                    headers=_seller_auth("seller-A"))
    assert r.status_code == 422


def test_revoke_boost(client):
    _, li = _create_listing_http(client, title="X", sub="seller-A")
    g = client.post("/api/v1/boosts",
                    json={"target_type": "listing", "target_id": li["id"], "tier": "mtaa"},
                    headers=_seller_auth("seller-A")).json()
    # cross-owner revoke → 404
    assert client.delete(f"/api/v1/boosts/{g['id']}", headers=_seller_auth("seller-B")).status_code == 404
    # owner revoke → 204
    assert client.delete(f"/api/v1/boosts/{g['id']}", headers=_seller_auth("seller-A")).status_code == 204
    # gone
    assert client.delete(f"/api/v1/boosts/{g['id']}", headers=_seller_auth("seller-A")).status_code == 404


# ----------------------------- tier metadata (GET /boosts/tiers) -----------------------------

def test_boost_tiers_metadata_is_server_authoritative(client):
    r = client.get("/api/v1/boosts/tiers", headers=_seller_auth("seller-A"))
    assert r.status_code == 200, r.text
    tiers = {t["tier"]: t for t in r.json()["tiers"]}
    assert set(tiers) == {"mtaa", "hustle", "sovereign"}
    # Sovereign is nationwide → no radius; local tiers carry a positive radius.
    assert tiers["sovereign"]["radius_m"] is None
    assert tiers["mtaa"]["radius_m"] > 0
    # Nominal price is present (0 by default — free today) and each tier has its free cap + window.
    for t in tiers.values():
        assert t["price_kes"] == 0
        assert t["daily_free_cap"] >= 0
        assert t["duration_default_seconds"] > 0


def test_boost_tiers_requires_write_scope(client):
    assert client.get("/api/v1/boosts/tiers", headers=_buyer_auth()).status_code == 403


# ----------------------------- per-shop sponsored-cap override (item 1) -----------------------------

def _staff_auth(sub="staff-1"):
    tok = jwt.encode(
        {"sub": sub, "role": "staff", "scope": "commerce_trade",
         "scopes": ["read:feed", "create:trades"],
         "exp": datetime.now(timezone.utc) + timedelta(minutes=10)},
        _PRIVATE, algorithm="RS256",
    )
    return {"Authorization": f"Bearer {tok}"}


def test_apply_sponsored_cap_owner_then_staff_approves(client):
    shop, _ = _create_listing_http(client, title="X", sub="seller-A")
    # Owner applies.
    r = client.post(f"/api/v1/shops/{shop['id']}/sponsored-cap",
                    json={"requested_cap": 5}, headers=_seller_auth("seller-A"))
    assert r.status_code == 200, r.text
    ov = r.json()
    assert ov["status"] == "pending" and ov["requested_cap"] == 5

    # Appears in the staff pending queue.
    pending = client.get("/api/v1/admin/sponsored-caps", headers=_staff_auth()).json()["overrides"]
    assert any(o["id"] == ov["id"] for o in pending)

    # Staff approves with an explicit cap.
    d = client.post(f"/api/v1/admin/sponsored-caps/{ov['id']}/decide",
                    json={"approve": True, "approved_cap": 4}, headers=_staff_auth())
    assert d.status_code == 200, d.text
    assert d.json()["status"] == "approved" and d.json()["approved_cap"] == 4
    # No longer pending.
    pending2 = client.get("/api/v1/admin/sponsored-caps", headers=_staff_auth()).json()["overrides"]
    assert all(o["id"] != ov["id"] for o in pending2)


def test_get_sponsored_cap_status_no_override_exposes_bounds(client):
    from PE.commerce.core.config import settings
    shop, _ = _create_listing_http(client, title="X", sub="seller-A")
    r = client.get(f"/api/v1/shops/{shop['id']}/sponsored-cap", headers=_seller_auth("seller-A"))
    assert r.status_code == 200, r.text
    body = r.json()
    # Never applied → null override, plus the server-authoritative bounds (anti-drift).
    assert body["override"] is None
    assert body["max_cap"] == settings.boost_cap_override_max
    assert body["default_cap"] == settings.feed_sponsored_max_per_shop


def test_get_sponsored_cap_status_is_non_destructive(client):
    # The GET must not knock an approved override back to pending (contrast the POST).
    shop, _ = _create_listing_http(client, title="X", sub="seller-A")
    ov = client.post(f"/api/v1/shops/{shop['id']}/sponsored-cap",
                     json={"requested_cap": 6}, headers=_seller_auth("seller-A")).json()
    client.post(f"/api/v1/admin/sponsored-caps/{ov['id']}/decide",
                json={"approve": True, "approved_cap": 4}, headers=_staff_auth())
    # Reading status repeatedly leaves it approved.
    for _ in range(3):
        body = client.get(f"/api/v1/shops/{shop['id']}/sponsored-cap",
                          headers=_seller_auth("seller-A")).json()
        assert body["override"]["status"] == "approved"
        assert body["override"]["approved_cap"] == 4


def test_get_sponsored_cap_status_cross_owner_is_404(client):
    shop, _ = _create_listing_http(client, title="X", sub="seller-A")
    r = client.get(f"/api/v1/shops/{shop['id']}/sponsored-cap", headers=_seller_auth("seller-B"))
    assert r.status_code == 404


def test_admin_pending_list_carries_max_cap(client):
    from PE.commerce.core.config import settings
    shop, _ = _create_listing_http(client, title="X", sub="seller-A")
    client.post(f"/api/v1/shops/{shop['id']}/sponsored-cap",
                json={"requested_cap": 3}, headers=_seller_auth("seller-A"))
    body = client.get("/api/v1/admin/sponsored-caps", headers=_staff_auth()).json()
    assert body["max_cap"] == settings.boost_cap_override_max


def test_apply_sponsored_cap_cross_owner_is_404(client):
    shop, _ = _create_listing_http(client, title="X", sub="seller-A")
    r = client.post(f"/api/v1/shops/{shop['id']}/sponsored-cap",
                    json={"requested_cap": 5}, headers=_seller_auth("seller-B"))
    assert r.status_code == 404


def test_apply_sponsored_cap_requires_write_scope(client):
    shop, _ = _create_listing_http(client, title="X", sub="seller-A")
    r = client.post(f"/api/v1/shops/{shop['id']}/sponsored-cap",
                    json={"requested_cap": 5}, headers=_buyer_auth())  # read-only
    assert r.status_code == 403


def test_apply_sponsored_cap_bad_value_is_422(client):
    shop, _ = _create_listing_http(client, title="X", sub="seller-A")
    r = client.post(f"/api/v1/shops/{shop['id']}/sponsored-cap",
                    json={"requested_cap": 0}, headers=_seller_auth("seller-A"))
    assert r.status_code == 422


def test_admin_endpoints_reject_non_staff(client):
    shop, _ = _create_listing_http(client, title="X", sub="seller-A")
    ov = client.post(f"/api/v1/shops/{shop['id']}/sponsored-cap",
                     json={"requested_cap": 3}, headers=_seller_auth("seller-A")).json()
    # A create:trades seller token is NOT staff → 403 on both admin endpoints.
    assert client.get("/api/v1/admin/sponsored-caps",
                      headers=_seller_auth("seller-A")).status_code == 403
    assert client.post(f"/api/v1/admin/sponsored-caps/{ov['id']}/decide",
                       json={"approve": True}, headers=_seller_auth("seller-A")).status_code == 403


def test_decide_unknown_override_is_404(client):
    assert client.post("/api/v1/admin/sponsored-caps/nope/decide",
                       json={"approve": True}, headers=_staff_auth()).status_code == 404
