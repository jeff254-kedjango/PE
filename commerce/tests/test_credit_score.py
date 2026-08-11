"""WeesStock credit-profile scoring (§WeesStock F1) — services/credit_score.py.

The service is a PURE function of stored rows + an injected ``now``, so these tests build ORM
rows directly and call it. No HTTP, no auth, no clock: every threshold is pinned at its exact
boundary, which is the whole reason ``now`` is a parameter.

What is being defended here, in priority order:
  1. Verified revenue comes from RECEIPTS, never from orders (a locked order that failed on the
     rail must not read as income).
  2. The score is ABSOLUTE — one seller's numbers never move another's.
  3. A thin file yields score=None, never a low score.
"""
from datetime import datetime, timedelta, timezone

import pytest

from PE.commerce.models.listing import Listing
from PE.commerce.models.order import (
    Order,
    STATUS_CANCELLED,
    STATUS_PRICE_LOCKED,
    STATUS_SETTLED,
    STATUS_SETTLEMENT_FAILED,
)
from PE.commerce.models.receipt import Receipt
from PE.commerce.models.review import Review
from PE.commerce.models.seller import Seller, Shop
from PE.commerce.services import credit_score as cs

NOW = datetime(2026, 8, 11, 12, 0, 0, tzinfo=timezone.utc)
_LAT, _LNG = -1.2920, 36.8219


# ─────────────────────────── row builders ───────────────────────────

def _seller(db, *, sub="seller-1", tenure_days=400.0) -> Seller:
    s = Seller(user_uuid=sub, display_name=f"Seller {sub}",
               created_at=NOW - timedelta(days=tenure_days))
    db.add(s)
    db.flush()
    return s


def _shop(db, seller: Seller) -> Shop:
    sh = Shop(seller_id=seller.id, name="Mama Mboga", lat=_LAT, lng=_LNG,
              created_at=NOW - timedelta(days=300))
    db.add(sh)
    db.flush()
    return sh


def _listing(db, shop: Shop) -> Listing:
    li = Listing(shop_id=shop.id, seller_id=shop.seller_id, title="Maize flour 2kg",
                 price_cents=12000, stock_qty=10, lat=_LAT, lng=_LNG)
    db.add(li)
    db.flush()
    return li


def _sale(db, seller, listing, *, buyer="buyer-1", days_ago=10, gross=12000,
          settled=True, receipt=True):
    """One order, optionally settled, optionally with its receipt.

    ``settled=True, receipt=False`` is the important adversarial combination: an order the
    system believes settled but for which no receipt exists. Revenue must ignore it.
    """
    when = NOW - timedelta(days=days_ago)
    commission = gross * 3 // 100
    # Order carries no shop_id — a sale reaches its shop through the listing.
    o = Order(
        listing_id=listing.id, seller_id=seller.id,
        buyer_uuid=buyer, pricing_mode="fixed",
        status=STATUS_SETTLED if settled else STATUS_CANCELLED,
        reference_price_cents=gross, locked_price_cents=gross,
        commission_cents=commission, created_at=when,
    )
    db.add(o)
    db.flush()
    if settled and receipt:
        db.add(Receipt(
            order_id=o.id, buyer_uuid=buyer, seller_id=seller.id, listing_id=listing.id,
            listing_title=listing.title, currency="KES",
            gross_cents=gross, commission_cents=commission,
            net_to_seller_cents=gross - commission,
            chain_tip_hash="0" * 64, receipt_hash=f"h{o.id}"[:64],
            issued_at=when,
        ))
        db.flush()
    return o


def _failed(db, seller, listing, *, status=STATUS_CANCELLED, days_ago=5):
    o = Order(
        listing_id=listing.id, seller_id=seller.id,
        buyer_uuid="buyer-x", pricing_mode="fixed", status=status,
        reference_price_cents=12000, created_at=NOW - timedelta(days=days_ago),
    )
    db.add(o)
    db.flush()
    return o


@pytest.fixture
def seeded(db_session):
    """A seller seasoned enough to clear the tenure gate, with a shop and a listing."""
    s = _seller(db_session)
    sh = _shop(db_session, s)
    li = _listing(db_session, sh)
    return db_session, s, li


def _profile(db, seller, *, now=NOW):
    return cs.compute_credit_profile(db, seller, now=now)


# ─────────────────────────── doctrine 2: verified means settled ───────────────────────────

class TestVerifiedRevenue:
    """Revenue is summed from receipts only — the one number a lender cannot be wrong about."""

    def test_revenue_comes_from_receipts_not_orders(self, seeded):
        db, seller, li = seeded
        # Ten settled orders, but only SIX produced a receipt. The other four are orders that
        # locked and then died on the rail. A lender must see six sales' worth of income.
        for i in range(6):
            _sale(db, seller, li, buyer=f"b{i}", gross=10_000, receipt=True)
        for i in range(4):
            _sale(db, seller, li, buyer=f"z{i}", gross=10_000, receipt=False)

        p = _profile(db, seller)
        assert p.signals.settled_orders == 6
        # net of the 3% commission: 10000 - 300 = 9700 per sale.
        assert p.signals.revenue_cents == 6 * 9_700

    def test_price_locked_order_is_not_revenue(self, seeded):
        """PRICE_LOCKED is a promise, not a payment — it must not count as either a settled
        sale or a failure. It is simply in flight."""
        db, seller, li = seeded
        o = Order(listing_id=li.id, seller_id=seller.id,
                  buyer_uuid="b1", pricing_mode="fixed", status=STATUS_PRICE_LOCKED,
                  reference_price_cents=50_000, locked_price_cents=50_000,
                  created_at=NOW - timedelta(days=2))
        db.add(o)
        db.flush()

        p = _profile(db, seller)
        assert p.signals.revenue_cents == 0
        assert p.signals.settled_orders == 0
        assert p.signals.failed_orders == 0

    def test_revenue_window_excludes_older_receipts(self, seeded):
        db, seller, li = seeded
        _sale(db, seller, li, buyer="recent", days_ago=89, gross=10_000)
        _sale(db, seller, li, buyer="old", days_ago=91, gross=10_000)

        p = _profile(db, seller)
        assert p.signals.settled_orders == 1
        assert p.signals.revenue_cents == 9_700

    def test_recent_window_is_nested_in_the_main_window(self, seeded):
        db, seller, li = seeded
        _sale(db, seller, li, buyer="a", days_ago=10, gross=10_000)   # in both windows
        _sale(db, seller, li, buyer="b", days_ago=60, gross=10_000)   # 90d only

        p = _profile(db, seller)
        assert p.signals.revenue_cents == 2 * 9_700
        assert p.signals.recent_revenue_cents == 9_700

    def test_another_sellers_receipts_never_leak_in(self, db_session):
        """Absolute scoring (doctrine 1) is also an isolation guarantee: a second seller's
        rows must not appear in the first seller's revenue under any circumstance."""
        db = db_session
        mine = _seller(db, sub="seller-mine")
        theirs = _seller(db, sub="seller-theirs")
        my_li = _listing(db, _shop(db, mine))
        their_li = _listing(db, _shop(db, theirs))
        _sale(db, mine, my_li, buyer="b1", gross=10_000)
        for i in range(20):
            _sale(db, theirs, their_li, buyer=f"t{i}", gross=500_000)

        p = _profile(db, mine)
        assert p.signals.settled_orders == 1
        assert p.signals.revenue_cents == 9_700


# ─────────────────────────── cold start ───────────────────────────

class TestColdStart:
    """A thin file reports as thin. score=None ≠ score=0.0."""

    def test_no_history_yields_no_score_not_a_zero(self, seeded):
        db, seller, _ = seeded
        p = _profile(db, seller)
        assert p.score is None
        assert p.is_scoreable is False
        assert "settled_orders" in p.missing_for_score
        assert p.orders_needed == cs.MIN_ORDERS_FOR_SCORE

    def test_components_are_returned_even_without_a_score(self, seeded):
        """The seller must still see what they have — that is the growth prompt."""
        db, seller, li = seeded
        for i in range(3):
            _sale(db, seller, li, buyer=f"b{i}", gross=10_000)

        p = _profile(db, seller)
        assert p.score is None
        assert p.signals.settled_orders == 3
        assert p.signals.revenue_cents == 3 * 9_700
        assert p.revenue_score > 0.0          # component populated despite the withheld score
        assert p.fulfilment_score > 0.0
        assert p.orders_needed == cs.MIN_ORDERS_FOR_SCORE - 3

    def test_order_gate_clears_exactly_at_the_threshold(self, seeded):
        db, seller, li = seeded
        for i in range(cs.MIN_ORDERS_FOR_SCORE - 1):
            _sale(db, seller, li, buyer=f"b{i}", gross=10_000)
        assert _profile(db, seller).score is None      # one short

        _sale(db, seller, li, buyer="last", gross=10_000)
        p = _profile(db, seller)
        assert p.score is not None
        assert p.missing_for_score == ()

    def test_tenure_gate_blocks_a_burst_of_orders_from_a_new_shop(self, db_session):
        """Twenty sales in a week is a burst, not a track record."""
        db = db_session
        seller = _seller(db, sub="new-seller", tenure_days=10.0)
        li = _listing(db, _shop(db, seller))
        for i in range(20):
            _sale(db, seller, li, buyer=f"b{i}", days_ago=3, gross=50_000)

        p = _profile(db, seller)
        assert p.score is None
        assert p.missing_for_score == ("tenure",)
        assert p.orders_needed == 0
        assert p.days_needed == cs.MIN_TENURE_DAYS - 10

    def test_both_gates_reported_together(self, db_session):
        db = db_session
        seller = _seller(db, sub="brand-new", tenure_days=2.0)
        p = _profile(db, seller)
        assert set(p.missing_for_score) == {"settled_orders", "tenure"}


# ─────────────────────────── fulfilment ───────────────────────────

class TestFulfilment:
    def test_failed_orders_lower_the_rate(self, seeded):
        db, seller, li = seeded
        for i in range(8):
            _sale(db, seller, li, buyer=f"b{i}")
        for _ in range(2):
            _failed(db, seller, li)

        p = _profile(db, seller)
        assert p.signals.total_orders == 10
        assert p.signals.fulfilment_rate == pytest.approx(0.8)

    def test_settlement_failure_counts_against_fulfilment(self, seeded):
        """From a lender's seat an uncollected sale is uncollected whatever the cause."""
        db, seller, li = seeded
        _sale(db, seller, li, buyer="ok")
        _failed(db, seller, li, status=STATUS_SETTLEMENT_FAILED)

        p = _profile(db, seller)
        assert p.signals.failed_orders == 1
        assert p.signals.fulfilment_rate == pytest.approx(0.5)

    def test_no_orders_gives_zero_not_a_flattering_one(self, seeded):
        db, seller, _ = seeded
        p = _profile(db, seller)
        assert p.signals.fulfilment_rate == 0.0


# ─────────────────────────── repeat buyers ───────────────────────────

class TestRepeatBuyers:
    def test_repeat_rate_counts_buyers_not_orders(self, seeded):
        db, seller, li = seeded
        # buyer-a bought three times, buyer-b twice, buyer-c once ⇒ 2 of 3 buyers repeated.
        for _ in range(3):
            _sale(db, seller, li, buyer="a")
        for _ in range(2):
            _sale(db, seller, li, buyer="b")
        _sale(db, seller, li, buyer="c")

        p = _profile(db, seller)
        assert p.signals.unique_buyers == 3
        assert p.signals.repeat_buyers == 2
        assert p.signals.repeat_rate == pytest.approx(2 / 3)

    def test_no_buyers_is_zero_not_a_division_error(self, seeded):
        db, seller, _ = seeded
        assert _profile(db, seller).signals.repeat_rate == 0.0


# ─────────────────────────── rating damping ───────────────────────────

class TestRatingTerm:
    def test_small_sample_is_damped_below_a_large_one(self, db_session):
        """A perfect 5.0 from two buyers must not outscore a 4.5 from many."""
        db = db_session
        few = _seller(db, sub="few-ratings")
        many = _seller(db, sub="many-ratings")
        few_li = _listing(db, _shop(db, few))
        many_li = _listing(db, _shop(db, many))
        for i in range(2):
            o = _sale(db, few, few_li, buyer=f"f{i}")
            db.add(Review(order_id=o.id, seller_id=few.id, listing_id=few_li.id,
                          reviewer_uuid=f"f{i}", rating=5))
        for i in range(20):
            o = _sale(db, many, many_li, buyer=f"m{i}")
            db.add(Review(order_id=o.id, seller_id=many.id, listing_id=many_li.id,
                          reviewer_uuid=f"m{i}", rating=4))
        db.flush()

        assert _profile(db, few).rating_score < _profile(db, many).rating_score

    def test_unrated_shop_contributes_nothing_rather_than_zero_stars(self, seeded):
        db, seller, li = seeded
        _sale(db, seller, li, buyer="b1")
        p = _profile(db, seller)
        assert p.signals.rating_count == 0
        assert p.rating_score == 0.0

    def test_ratings_are_lifetime_not_windowed(self, seeded):
        """Trust accrues over the life of the business; an old review still counts."""
        db, seller, li = seeded
        o = _sale(db, seller, li, buyer="b1", days_ago=200)
        db.add(Review(order_id=o.id, seller_id=seller.id, listing_id=li.id,
                      reviewer_uuid="b1", rating=5,
                      created_at=NOW - timedelta(days=200)))
        db.flush()
        p = _profile(db, seller)
        assert p.signals.rating_count == 1


# ─────────────────────────── doctrine 1: absolute, not relative ───────────────────────────

class TestAbsoluteScoring:
    def test_a_sellers_score_is_unchanged_by_other_sellers(self, db_session):
        """The core credit doctrine: creditworthiness must not move when a neighbour's does.

        This is what makes the model different from shop_ranking.py, and it is the property a
        lender relies on when comparing two profiles pulled a month apart.
        """
        db = db_session
        seller = _seller(db, sub="stable")
        li = _listing(db, _shop(db, seller))
        for i in range(12):
            _sale(db, seller, li, buyer=f"b{i}", gross=20_000)
        before = _profile(db, seller).score
        assert before is not None

        # A far stronger competitor appears, then a far weaker one.
        strong = _seller(db, sub="strong")
        strong_li = _listing(db, _shop(db, strong))
        for i in range(200):
            _sale(db, strong, strong_li, buyer=f"s{i}", gross=900_000)
        weak = _seller(db, sub="weak")
        _listing(db, _shop(db, weak))

        assert _profile(db, seller).score == before

    def test_revenue_term_saturates_and_never_exceeds_its_weight(self, db_session):
        db = db_session
        seller = _seller(db, sub="huge")
        li = _listing(db, _shop(db, seller))
        for i in range(15):
            _sale(db, seller, li, buyer=f"b{i}", gross=cs.REVENUE_SATURATION_CENTS)

        p = _profile(db, seller)
        assert p.revenue_score == pytest.approx(cs.W_REVENUE)
        assert p.score is not None and p.score <= 1.0

    def test_score_equals_the_sum_of_its_parts(self, seeded):
        """Explainability is contractual (doctrine 3): the components must ADD UP to the
        composite, or the breakdown shown to a lender is a lie."""
        db, seller, li = seeded
        for i in range(12):
            _sale(db, seller, li, buyer=f"b{i % 4}", gross=30_000)
        o = _sale(db, seller, li, buyer="rater", gross=30_000)
        db.add(Review(order_id=o.id, seller_id=seller.id, listing_id=li.id,
                      reviewer_uuid="rater", rating=5))
        db.flush()

        p = _profile(db, seller)
        assert p.score == pytest.approx(
            p.revenue_score + p.fulfilment_score + p.repeat_score
            + p.rating_score + p.tenure_score
        )

    def test_score_stays_within_zero_and_one(self, db_session):
        db = db_session
        seller = _seller(db, sub="maxed", tenure_days=5000.0)
        li = _listing(db, _shop(db, seller))
        for i in range(30):
            o = _sale(db, seller, li, buyer=f"b{i % 3}", gross=cs.REVENUE_SATURATION_CENTS)
            db.add(Review(order_id=o.id, seller_id=seller.id, listing_id=li.id,
                          reviewer_uuid=f"b{i}", rating=5))
        db.flush()

        p = _profile(db, seller)
        assert p.score is not None
        assert 0.0 <= p.score <= 1.0


# ─────────────────────────── trend ───────────────────────────

class TestRevenueTrend:
    def test_steady_trading_reads_as_flat(self, seeded):
        """A shop transacting at a constant rate must score ~1.0 regardless of window sizes —
        otherwise the ratio would report a healthy business as shrinking."""
        db, seller, li = seeded
        for day in range(0, 90, 3):
            _sale(db, seller, li, buyer=f"b{day}", days_ago=day, gross=10_000)

        trend = _profile(db, seller).signals.revenue_trend
        assert trend == pytest.approx(1.0, abs=0.15)

    def test_accelerating_business_scores_above_one(self, seeded):
        db, seller, li = seeded
        _sale(db, seller, li, buyer="old", days_ago=80, gross=10_000)
        for i in range(5):
            _sale(db, seller, li, buyer=f"new{i}", days_ago=5, gross=10_000)

        assert _profile(db, seller).signals.revenue_trend > 1.0

    def test_no_revenue_yields_none_not_a_misleading_zero(self, seeded):
        db, seller, _ = seeded
        assert _profile(db, seller).signals.revenue_trend is None


# ─────────────────────────── derived reporting values ───────────────────────────

class TestDerivedValues:
    def test_average_order_value(self, seeded):
        db, seller, li = seeded
        _sale(db, seller, li, buyer="a", gross=10_000)
        _sale(db, seller, li, buyer="b", gross=20_000)

        p = _profile(db, seller)
        assert p.signals.avg_order_value_cents == (9_700 + 19_400) // 2

    def test_avg_order_value_is_zero_without_sales(self, seeded):
        db, seller, _ = seeded
        assert _profile(db, seller).signals.avg_order_value_cents == 0

    def test_inquiries_are_reported_but_do_not_move_the_score(self, seeded):
        """Inquiries are self-generatable, so they inform a lender without being weighted."""
        from PE.commerce.models.engagement import ListingInquiry

        db, seller, li = seeded
        for i in range(12):
            _sale(db, seller, li, buyer=f"b{i}", gross=10_000)
        baseline = _profile(db, seller).score

        for i in range(50):
            db.add(ListingInquiry(listing_id=li.id, seller_id=seller.id,
                                  from_user_uuid=f"nosy{i}", message="how much?", seq=i,
                                  created_at=NOW - timedelta(days=1)))
        db.flush()

        p = _profile(db, seller)
        assert p.signals.inquiries == 50
        assert p.score == baseline

    def test_tenure_is_measured_from_the_seller_row(self, db_session):
        db = db_session
        seller = _seller(db, sub="aged", tenure_days=365.0)
        p = _profile(db, seller)
        assert p.signals.tenure_days == pytest.approx(365.0, abs=0.01)

    def test_currency_defaults_to_kes_without_receipts(self, seeded):
        db, seller, _ = seeded
        assert _profile(db, seller).signals.currency == "KES"
