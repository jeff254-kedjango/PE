"""Shop ranking service tests (§8). Pure-function tests against directly-seeded DB rows —
faster and more precise than exercising the endpoint. Endpoint-level tests come in B-iii.

The ranking function is a deterministic pure function of stored signals + a fixed ``now``, so
we can pin the expected order exactly. Each test seeds a small peer set (2–4 shops) with
carefully-chosen signals so we can verify:
  * sales/revenue dominates (weight 0.6)
  * composite fills the gap for cold-start shops
  * peer-normalization is per-radius (a shop with $100 revenue is "top" in a poor peer set,
    "bottom" in a rich one)
  * unrated shops score 0 on the rating term (never a misleading 0-star)
  * ties break by shop_id for determinism
  * an out-of-radius shop is excluded
  * a caller with no shop returns None
"""
from datetime import datetime, timedelta, timezone

import pytest

from PE.commerce.models.engagement import SavedListing
from PE.commerce.models.listing import Listing
from PE.commerce.models.order import Order, STATUS_SETTLED
from PE.commerce.models.review import Review
from PE.commerce.models.seller import Seller, Shop, ShopSubscription
from PE.commerce.services import shop_ranking
from PE.commerce.services.proximity import set_location

# Fixed "now" so freshness/recency terms are deterministic across CI runs.
_NOW = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def _mk_seller(db, sub: str, display_name: str) -> Seller:
    s = Seller(user_uuid=sub, display_name=display_name)
    db.add(s)
    db.flush()
    return s


def _mk_shop(db, seller: Seller, name: str, lat: float, lng: float,
             created_days_ago: float = 30.0) -> Shop:
    shop = Shop(
        seller_id=seller.id,
        name=name,
        lat=lat,
        lng=lng,
        created_at=_NOW - timedelta(days=created_days_ago),
    )
    set_location(shop, lat, lng)
    db.add(shop)
    db.flush()
    return shop


def _mk_listing(db, shop: Shop, seller: Seller, title="Item", price_cents=1000) -> Listing:
    li = Listing(
        shop_id=shop.id,
        seller_id=seller.id,
        title=title,
        price_cents=price_cents,
        stock_qty=5,
        pricing_mode="fixed",
        post_kind="listing",
        media_urls="[]",
        lat=shop.lat,
        lng=shop.lng,
    )
    set_location(li, shop.lat, shop.lng)
    db.add(li)
    db.flush()
    return li


def _mk_settled_order(db, listing: Listing, seller: Seller, buyer_sub: str,
                     locked_cents: int, days_ago: float) -> Order:
    o = Order(
        listing_id=listing.id,
        seller_id=seller.id,
        buyer_uuid=buyer_sub,
        pricing_mode="fixed",
        status=STATUS_SETTLED,
        reference_price_cents=locked_cents,
        locked_price_cents=locked_cents,
        commission_cents=int(locked_cents * 0.03),
        version=1,
        created_at=_NOW - timedelta(days=days_ago),
    )
    db.add(o)
    db.flush()
    return o


def _mk_review(db, listing: Listing, seller: Seller, buyer_sub: str, order_id: str,
              rating: int, days_ago: float) -> Review:
    r = Review(
        order_id=order_id,
        seller_id=seller.id,
        listing_id=listing.id,
        reviewer_uuid=buyer_sub,
        rating=rating,
        body=None,
        created_at=_NOW - timedelta(days=days_ago),
    )
    db.add(r)
    db.flush()
    return r


def _mk_follower(db, shop: Shop, user_sub: str) -> ShopSubscription:
    sub = ShopSubscription(shop_id=shop.id, user_uuid=user_sub)
    db.add(sub)
    db.flush()
    return sub


def _mk_save(db, listing: Listing, user_sub: str, seq: int = 1) -> SavedListing:
    s = SavedListing(listing_id=listing.id, user_uuid=user_sub, seq=seq)
    db.add(s)
    db.flush()
    return s


# ─────────────────────── the peer-score function ───────────────────────

class TestComputeShopScores:
    def test_empty_radius_returns_empty(self, db_session):
        # No shops seeded → empty list, never a null-pointer or exception.
        got = shop_ranking.compute_shop_scores(
            db_session, center_lat=-1.29, center_lng=36.82, radius_km=10.0, now=_NOW,
        )
        assert got == []

    def test_out_of_radius_shop_is_excluded(self, db_session):
        # A shop 100 km away is not in a 10 km radius.
        s = _mk_seller(db_session, "seller-far", "Far")
        _mk_shop(db_session, s, "Far", lat=-2.30, lng=36.82)
        got = shop_ranking.compute_shop_scores(
            db_session, center_lat=-1.29, center_lng=36.82, radius_km=10.0, now=_NOW,
        )
        assert got == []

    def test_sales_dominates_composite(self, db_session):
        # Shop A: huge revenue, otherwise cold. Shop B: no revenue, great composite (rating,
        # followers, saves). Sales weight 0.6 beats composite 0.4 → A ranks first.
        sa = _mk_seller(db_session, "sa", "A")
        sb = _mk_seller(db_session, "sb", "B")
        shop_a = _mk_shop(db_session, sa, "A", -1.29, 36.82)
        shop_b = _mk_shop(db_session, sb, "B", -1.291, 36.821)
        li_a = _mk_listing(db_session, shop_a, sa)
        li_b = _mk_listing(db_session, shop_b, sb)
        # A: one big settled order.
        _mk_settled_order(db_session, li_a, sa, "buyer1", locked_cents=100_000, days_ago=5)
        # B: strong composite — rating 5.0, many followers, many saves — but ZERO settled orders.
        _mk_review(db_session, li_b, sb, "buyer2", "order-fake-1", rating=5, days_ago=2)
        _mk_review(db_session, li_b, sb, "buyer3", "order-fake-2", rating=5, days_ago=2)
        for i in range(20):
            _mk_follower(db_session, shop_b, f"follower{i}")
        for i in range(50):
            _mk_save(db_session, li_b, f"saver{i}", seq=i + 1)
        db_session.flush()

        got = shop_ranking.compute_shop_scores(
            db_session, center_lat=-1.29, center_lng=36.82, radius_km=10.0, now=_NOW,
        )
        assert [s.shop_id for s in got] == [str(shop_a.id), str(shop_b.id)]
        # Explainability: the winner's sales_score should carry most of the score.
        assert got[0].sales_score > got[0].composite_score

    def test_composite_settles_ties_when_no_sales(self, db_session):
        # No sales anywhere → the composite is the only signal. Peer normalization: whoever has
        # the best rating (in a peer set where rating varies) climbs. This is the cold-start
        # case — a brand-new market where no shop has settled yet.
        sa = _mk_seller(db_session, "sa", "A")
        sb = _mk_seller(db_session, "sb", "B")
        shop_a = _mk_shop(db_session, sa, "A", -1.29, 36.82)
        shop_b = _mk_shop(db_session, sb, "B", -1.291, 36.821)
        li_a = _mk_listing(db_session, shop_a, sa)
        li_b = _mk_listing(db_session, shop_b, sb)
        _mk_review(db_session, li_b, sb, "buyer1", "order-fake-1", rating=5, days_ago=1)
        db_session.flush()

        got = shop_ranking.compute_shop_scores(
            db_session, center_lat=-1.29, center_lng=36.82, radius_km=10.0, now=_NOW,
        )
        assert [s.shop_id for s in got] == [str(shop_b.id), str(shop_a.id)]

    def test_unrated_shop_scores_zero_on_rating_term(self, db_session):
        # An unrated shop (rating_count == 0) contributes 0 to the composite's rating term, not
        # a misleading 0.0-star average. Peer-normalization ensures a peer with a real rating
        # still climbs.
        sa = _mk_seller(db_session, "sa", "A")
        shop_a = _mk_shop(db_session, sa, "A", -1.29, 36.82)
        _mk_listing(db_session, shop_a, sa)
        db_session.flush()

        got = shop_ranking.compute_shop_scores(
            db_session, center_lat=-1.29, center_lng=36.82, radius_km=10.0, now=_NOW,
        )
        assert len(got) == 1
        # No rating, no sales, no followers, no saves → composite is just the recency term.
        # created_days_ago = 30 in _mk_shop; recency = 0.5^(30/30) = 0.5.
        expected_composite = shop_ranking.W_COMPOSITE * shop_ranking.W_C_RECENCY * 0.5
        assert got[0].composite_score == pytest.approx(expected_composite, abs=1e-9)
        assert got[0].sales_score == 0.0

    def test_ties_break_by_shop_id_for_determinism(self, db_session):
        # Two identical shops → score is identical → sort key breaks on shop_id ASC.
        sa = _mk_seller(db_session, "sa", "A")
        sb = _mk_seller(db_session, "sb", "B")
        # Force known ids by inserting shops one at a time and reading them back sorted.
        shop_a = _mk_shop(db_session, sa, "A", -1.29, 36.82)
        shop_b = _mk_shop(db_session, sb, "B", -1.291, 36.821)
        got = shop_ranking.compute_shop_scores(
            db_session, center_lat=-1.29, center_lng=36.82, radius_km=10.0, now=_NOW,
        )
        # Both scores equal (same signals: none); their order is the ASC sort of shop_id.
        assert got[0].score == pytest.approx(got[1].score, abs=1e-9)
        assert got[0].shop_id < got[1].shop_id


# ─────────────────────── the caller-rank entrypoint ───────────────────────

class TestComputeShopRank:
    def test_caller_with_no_seller_row_returns_none(self, db_session):
        got = shop_ranking.compute_shop_rank(
            db_session, seller_uuid="ghost-user", radius_km=10.0, now=_NOW,
        )
        assert got is None

    def test_caller_with_no_shop_returns_none(self, db_session):
        # A registered Seller without any Shop can't be ranked (no coordinates, no peer set).
        _mk_seller(db_session, "empty", "Empty")
        db_session.flush()
        got = shop_ranking.compute_shop_rank(
            db_session, seller_uuid="empty", radius_km=10.0, now=_NOW,
        )
        assert got is None

    def test_solo_shop_ranks_first(self, db_session):
        sa = _mk_seller(db_session, "sa", "A")
        _mk_shop(db_session, sa, "A", -1.29, 36.82)
        db_session.flush()
        got = shop_ranking.compute_shop_rank(
            db_session, seller_uuid="sa", radius_km=10.0, now=_NOW,
        )
        assert got is not None
        assert got.rank == 1
        assert got.peer_count == 1

    def test_high_revenue_shop_beats_low_revenue_neighbor(self, db_session):
        # Verifies the sales weight actually drives the outcome via the ENDPOINT SHAPE, not just
        # the scores list: the caller's rank IS 1 when they outperform on revenue.
        sa = _mk_seller(db_session, "sa", "A")
        sb = _mk_seller(db_session, "sb", "B")
        shop_a = _mk_shop(db_session, sa, "A", -1.29, 36.82)
        shop_b = _mk_shop(db_session, sb, "B", -1.291, 36.821)
        li_a = _mk_listing(db_session, shop_a, sa)
        li_b = _mk_listing(db_session, shop_b, sb)
        _mk_settled_order(db_session, li_a, sa, "buyer1", locked_cents=50_000, days_ago=5)
        _mk_settled_order(db_session, li_b, sb, "buyer2", locked_cents=5_000, days_ago=5)
        db_session.flush()

        rank_a = shop_ranking.compute_shop_rank(db_session, seller_uuid="sa", radius_km=10.0, now=_NOW)
        rank_b = shop_ranking.compute_shop_rank(db_session, seller_uuid="sb", radius_km=10.0, now=_NOW)
        assert rank_a is not None and rank_b is not None
        assert rank_a.rank == 1
        assert rank_b.rank == 2
        assert rank_a.peer_count == 2
        assert rank_a.signals.revenue_cents == 50_000

    def test_peer_set_scoped_to_radius(self, db_session):
        # A shop 20 km away is NOT a peer under a 10 km radius, but IS a peer at 30 km. Verify
        # the peer_count changes with radius (and by extension the rank can change too).
        sa = _mk_seller(db_session, "sa", "A")
        sfar = _mk_seller(db_session, "sf", "Far")
        _mk_shop(db_session, sa, "A", -1.29, 36.82)
        # ~22 km north (lat delta of ~0.2 ≈ 22 km).
        _mk_shop(db_session, sfar, "Far", -1.09, 36.82)
        db_session.flush()

        small = shop_ranking.compute_shop_rank(db_session, seller_uuid="sa", radius_km=10.0, now=_NOW)
        big = shop_ranking.compute_shop_rank(db_session, seller_uuid="sa", radius_km=30.0, now=_NOW)
        assert small is not None and big is not None
        assert small.peer_count == 1
        assert big.peer_count == 2
