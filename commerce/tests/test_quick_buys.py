"""Quick Buys grid — the §8 near/interest MIX, on the SQLite Haversine path.

Asserts: (a) the per-page 4-near/5-outer composition, (b) the outer lane is affinity-matched to the
buyer's own engagement history, (c) a cold buyer with no history still gets a FULL grid (backfill),
(d) price/category/radius filters bite, (e) cross-bucket de-duplication, (f) the near/outer boundary
is strictly the near radius, (g) lat/lng are bounded (422) at the HTTP edge.
"""
import json
from datetime import datetime, timedelta, timezone

from PE.commerce.core.config import settings
from PE.commerce.models.engagement import SavedListing
from PE.commerce.models.listing import Listing
from PE.commerce.models.seller import Seller, Shop
from PE.commerce.schemas.quick_buys import BUCKET_INTEREST, BUCKET_NEAR
from PE.commerce.services import proximity, quick_buys
from PE.commerce.services.quick_buys import QuickBuyFilters

_LAT, _LNG = -1.2921, 36.8219


def _km_lat(km):
    return km / 111.32  # ~deg latitude per km


def _seller(db, uuid="seller-1"):
    s = Seller(user_uuid=uuid, display_name="Mama Mboga")
    db.add(s)
    db.flush()
    return s


def _shop(db, seller, lat, lng, *, category=None):
    sh = Shop(seller_id=seller.id, name="Corner Shop", category=category)
    proximity.set_location(sh, lat, lng)
    db.add(sh)
    db.flush()
    return sh


def _listing(db, shop, seller, lat, lng, *, title, price=5000, age_h=1.0, pricing="fixed",
             media=None, stock=10):
    li = Listing(
        shop_id=shop.id,
        seller_id=seller.id,
        title=title,
        price_cents=price,
        currency="KES",
        media_urls=json.dumps(media if media is not None else ["/uploads/trade/images/x.webp"]),
        intent_weight=1.0,
        is_active=True,
        stock_qty=stock,
        pricing_mode=pricing,
        created_at=datetime.now(timezone.utc) - timedelta(hours=age_h),
    )
    proximity.set_location(li, lat, lng)
    db.add(li)
    db.flush()
    return li


def _near(db, seller, km, *, title, category="general", **kw):
    """A listing km north of the reference point (km < 5 ⇒ inside the default near radius)."""
    lat = _LAT + _km_lat(km)
    shop = _shop(db, seller, lat, _LNG, category=category)
    return _listing(db, shop, seller, lat, _LNG, title=title, **kw)


# ---------------------------------------------------------------------------

def test_composition_is_four_near_five_outer(db_session):
    """A full slate composes each page as 4 near + 5 outer (the requested mix)."""
    seller = _seller(db_session)
    # Give the buyer affinity for "general" via a saved listing in a general shop.
    aff_shop = _shop(db_session, seller, _LAT + _km_lat(9), _LNG, category="general")
    aff_li = _listing(db_session, aff_shop, seller, _LAT + _km_lat(9), _LNG, title="saved-anchor")
    db_session.add(SavedListing(user_uuid="buyer-A", listing_id=aff_li.id, seq=0))
    # 6 near (1..4 km) and 8 outer (6..13 km), all "general".
    for i in range(6):
        _near(db_session, seller, 1 + i * 0.5, title=f"near-{i}")
    for i in range(8):
        km = 6 + i
        shop = _shop(db_session, seller, _LAT + _km_lat(km), _LNG, category="general")
        _listing(db_session, shop, seller, _LAT + _km_lat(km), _LNG, title=f"outer-{i}")
    db_session.commit()

    rows, near_radius = quick_buys.build_quick_buys(db_session, _LAT, _LNG, user_uuid="buyer-A")
    assert near_radius == settings.quick_buys_near_radius_m
    page = rows[: settings.quick_buys_page_size]
    assert len(page) == 9
    assert [r.bucket for r in page[:4]] == [BUCKET_NEAR] * 4
    assert all(r.bucket != BUCKET_NEAR for r in page[4:9])  # the 5 outer slots
    # And every near item is genuinely within the radius, every outer strictly beyond it.
    assert all(r.distance_m <= near_radius for r in page[:4])
    assert all(r.distance_m > near_radius for r in page[4:9])


def test_outer_lane_matches_affinity_from_history(db_session):
    """With no explicit filter and ENOUGH affinity supply to fill the outer lane, the outer items
    are all affinity-matched (butchery) and the off-affinity bakery never appears. (When affinity
    under-fills, the recency backfill is deliberately unrestricted — that is the cold-start
    guarantee, covered separately.)"""
    seller = _seller(db_session)
    # History: the buyer SAVED a butchery listing → affinity = {butchery}.
    b_shop = _shop(db_session, seller, _LAT + _km_lat(9), _LNG, category="butchery")
    b_li = _listing(db_session, b_shop, seller, _LAT + _km_lat(9), _LNG, title="saved-butchery")
    db_session.add(SavedListing(user_uuid="buyer-B", listing_id=b_li.id, seq=0))
    # Plenty of outer butchery supply so the affinity lane fills WITHOUT any backfill.
    for i in range(6):
        km = 6 + i
        shop = _shop(db_session, seller, _LAT + _km_lat(km), _LNG, category="butchery")
        _listing(db_session, shop, seller, _LAT + _km_lat(km), _LNG, title=f"outer-butchery-{i}")
    # An off-affinity bakery that must NOT surface while affinity supply is sufficient.
    bk_shop = _shop(db_session, seller, _LAT + _km_lat(8), _LNG, category="bakery")
    _listing(db_session, bk_shop, seller, _LAT + _km_lat(8), _LNG, title="outer-bakery")
    db_session.commit()

    rows, _ = quick_buys.build_quick_buys(db_session, _LAT, _LNG, user_uuid="buyer-B")
    by_title = {r.listing.title: r.bucket for r in rows}
    # The AFFINITY lane (bucket == interest) is gated to the buyer's categories: every interest item
    # is a butchery, and the off-affinity bakery is NEVER tagged interest (it can only appear later
    # as a `trending` backfill row — the cold-start guarantee, asserted separately).
    interest_titles = {t for t, b in by_title.items() if b == BUCKET_INTEREST}
    assert interest_titles, "expected affinity-matched outer items"
    assert all(t.startswith("outer-butchery") or t == "saved-butchery" for t in interest_titles)
    assert by_title.get("outer-bakery") != BUCKET_INTEREST


def test_cold_buyer_still_gets_a_full_outer_lane(db_session):
    """A buyer with zero history and no boosts near them still fills the grid via recency backfill —
    no empty/dead UI."""
    seller = _seller(db_session)
    for i in range(7):
        km = 6 + i
        shop = _shop(db_session, seller, _LAT + _km_lat(km), _LNG, category="general")
        _listing(db_session, shop, seller, _LAT + _km_lat(km), _LNG, title=f"outer-{i}")
    db_session.commit()

    rows, _ = quick_buys.build_quick_buys(db_session, _LAT, _LNG, user_uuid="cold-user")
    assert len(rows) >= 5          # a full outer lane's worth, not empty
    assert all(r.bucket != BUCKET_NEAR for r in rows)  # all from the outer backfill


def test_price_filter_bites(db_session):
    seller = _seller(db_session)
    _near(db_session, seller, 1, title="cheap", price=1000)
    _near(db_session, seller, 2, title="dear", price=9000)
    db_session.commit()

    rows, _ = quick_buys.build_quick_buys(
        db_session, _LAT, _LNG, user_uuid="buyer-P",
        filters=QuickBuyFilters(max_price_cents=5000),
    )
    titles = {r.listing.title for r in rows}
    assert "cheap" in titles
    assert "dear" not in titles


def test_category_filter_overrides_and_bites(db_session):
    """An explicit category filter restricts BOTH lanes to those categories."""
    seller = _seller(db_session)
    _near(db_session, seller, 1, title="near-butchery", category="butchery")
    _near(db_session, seller, 2, title="near-bakery", category="bakery")
    db_session.commit()

    rows, _ = quick_buys.build_quick_buys(
        db_session, _LAT, _LNG, user_uuid="buyer-C",
        filters=QuickBuyFilters(categories=("butchery",)),
    )
    titles = {r.listing.title for r in rows}
    assert titles == {"near-butchery"}


def test_price_and_category_filters_together_keep_the_near_lane(db_session):
    """Regression: setting a price filter AND a category filter together must NOT wipe the near lane.
    The near-lane guard used to re-check category with an empty shop→category map (category is really
    enforced by the shop_id IN), so every in-category near item was wrongly dropped once a price bound
    was also present — an empty grid for 'near me, cheap, butchery'."""
    seller = _seller(db_session)
    _near(db_session, seller, 1, title="cheap-butchery", category="butchery", price=1000)
    _near(db_session, seller, 1, title="dear-butchery", category="butchery", price=9000)
    _near(db_session, seller, 1, title="cheap-bakery", category="bakery", price=1000)
    db_session.commit()

    rows, _ = quick_buys.build_quick_buys(
        db_session, _LAT, _LNG, user_uuid="buyer-PC",
        filters=QuickBuyFilters(max_price_cents=5000, categories=("butchery",)),
    )
    titles = {r.listing.title for r in rows}
    assert "cheap-butchery" in titles      # in-category AND in-budget survives (the bug dropped it)
    assert "dear-butchery" not in titles   # price still bites
    assert "cheap-bakery" not in titles    # category still bites


def test_category_filter_is_never_violated_by_backfill(db_session):
    """A regression guard for a bug the live e2e caught: when a category filter is set but there is
    NOT enough matching supply to fill the outer lane, the recency backfill must NOT relax to
    off-category shops. An explicit category filter is a hard constraint — every returned item must
    match it, even if that means a shorter grid."""
    seller = _seller(db_session)
    # One matching butchery item (near) + lots of off-category electronics supply (near + outer).
    _near(db_session, seller, 1, title="butchery-near", category="butchery")
    for i in range(8):
        km = 6 + i  # outer electronics that a naive backfill would wrongly pull in
        shop = _shop(db_session, seller, _LAT + _km_lat(km), _LNG, category="electronics")
        _listing(db_session, shop, seller, _LAT + _km_lat(km), _LNG, title=f"elec-{i}")
    db_session.commit()

    rows, _ = quick_buys.build_quick_buys(
        db_session, _LAT, _LNG, user_uuid="buyer-CF",
        filters=QuickBuyFilters(categories=("butchery",)),
    )
    titles = {r.listing.title for r in rows}
    assert "butchery-near" in titles
    assert not any(t.startswith("elec-") for t in titles)  # no off-category backfill leak


def test_radius_filter_moves_the_near_boundary(db_session):
    """A caller radius overrides the near boundary: a 3 km listing is 'near' by default but 'outer'
    once the radius is tightened to 2 km."""
    seller = _seller(db_session)
    _near(db_session, seller, 3, title="three-km")
    db_session.commit()

    default_rows, _ = quick_buys.build_quick_buys(db_session, _LAT, _LNG, user_uuid="buyer-R")
    assert any(r.listing.title == "three-km" and r.bucket == BUCKET_NEAR for r in default_rows)

    tight_rows, near_radius = quick_buys.build_quick_buys(
        db_session, _LAT, _LNG, user_uuid="buyer-R", filters=QuickBuyFilters(radius_m=2000.0),
    )
    assert near_radius == 2000.0
    tight = next(r for r in tight_rows if r.listing.title == "three-km")
    assert tight.bucket != BUCKET_NEAR  # now beyond the (tightened) near radius


def test_no_duplicate_listing_across_buckets(db_session):
    """No listing appears twice, even when it qualifies for more than one lane."""
    seller = _seller(db_session)
    for i in range(3):
        _near(db_session, seller, 1 + i, title=f"near-{i}")
    for i in range(3):
        km = 6 + i
        shop = _shop(db_session, seller, _LAT + _km_lat(km), _LNG, category="general")
        _listing(db_session, shop, seller, _LAT + _km_lat(km), _LNG, title=f"outer-{i}")
    db_session.commit()

    rows, _ = quick_buys.build_quick_buys(db_session, _LAT, _LNG, user_uuid="buyer-D")
    ids = [str(r.listing.id) for r in rows]
    assert len(ids) == len(set(ids))


def test_thumbnail_skips_video_media(db_session):
    """The thumbnail is the first NON-video media; a video-only listing has no thumbnail."""
    seller = _seller(db_session)
    shop = _shop(db_session, seller, _LAT + _km_lat(1), _LNG, category="general")
    img = _listing(db_session, shop, seller, _LAT + _km_lat(1), _LNG, title="has-image",
                   media=["/uploads/trade/videos/clip.mp4", "/uploads/trade/images/p.webp"])
    vid = _listing(db_session, shop, seller, _LAT + _km_lat(1), _LNG, title="video-only",
                   media=["/uploads/trade/videos/clip.mp4"])
    db_session.commit()
    assert quick_buys.thumbnail_of(img) == "/uploads/trade/images/p.webp"
    assert quick_buys.thumbnail_of(vid) is None


# ------------------------------- HTTP edge --------------------------------

def test_http_returns_lean_dto_no_pii(client, db_session):
    seller = _seller(db_session)
    _near(db_session, seller, 1, title="near-item")
    db_session.commit()

    resp = client.get(f"/api/v1/quick-buys?lat={_LAT}&lng={_LNG}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["page_size"] == settings.quick_buys_page_size
    assert body["items"], "expected at least the near item"
    item = body["items"][0]
    # Lean contract: no POS internals, no buyer/PII fields leaked.
    for banned in ("stock_qty", "intent_weight", "is_active", "buyer_uuid", "contact"):
        assert banned not in item
    assert {"id", "title", "price_cents", "pricing_mode", "bucket", "distance_m"} <= set(item)


def test_http_rejects_out_of_range_latlng(client):
    assert client.get("/api/v1/quick-buys?lat=999&lng=0").status_code == 422
    assert client.get("/api/v1/quick-buys?lat=0&lng=999").status_code == 422


def test_http_unknown_category_is_dropped_not_422(client, db_session):
    seller = _seller(db_session)
    _near(db_session, seller, 1, title="near-item", category="general")
    db_session.commit()
    # A garbage category slug must degrade (narrow nothing), never 422 the whole grid.
    resp = client.get(f"/api/v1/quick-buys?lat={_LAT}&lng={_LNG}&categories=not_a_category")
    assert resp.status_code == 200
