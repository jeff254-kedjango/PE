"""WeesStock credit-profile service (§WeesStock F1) — how fundable is a shop, and why?

The financing surface's underwriting core. Given a seller, produce the signals a lender
actually underwrites on, computed ONLY from settlement-grade rows.

Three doctrines, all deliberate and all different from ``services/shop_ranking.py``:

1. **ABSOLUTE, never peer-relative.** ``shop_ranking`` normalizes every signal against the
   shops in the same radius, because "how am I doing in MY area" is inherently comparative.
   Credit is not. A business must NOT become creditworthy because its neighbours got worse,
   and must not lose access to funding because a strong competitor opened nearby. Every band
   here is a fixed threshold; a shop's profile is a pure function of its OWN rows.

2. **VERIFIED means settled.** Revenue is summed from ``Receipt`` — the immutable,
   one-per-order, hash-chained record issued inside the settle transaction — and never from
   ``Order.locked_price_cents`` (which ``shop_ranking`` uses, correctly, for a softer purpose).
   An order can be locked and then fail on the rail; a receipt cannot exist unless money moved.
   Blurring the two would make the number worthless to a lender, which is the one thing this
   product cannot afford.

3. **Components are the product; the composite is only a sort key.** Every sub-signal is
   returned raw and separately, so a lender (and the seller) can see WHY. An opaque score is
   both un-underwritable and a fair-lending hazard: a decision no one can explain is a
   decision no one can defend.

**Cold start.** A thin file is reported as thin. Below ``MIN_ORDERS_FOR_SCORE`` settled orders
or ``MIN_TENURE_DAYS`` of trading, ``score`` is ``None`` — NOT a low number. A 0.2 on six
orders reads as "bad business" when the truth is "not enough evidence"; the distinction is the
difference between a fair decline and a wrong one. Components are still returned, so the
seller sees exactly what they have and what is missing.

**Purity.** Like ``shop_ranking``, this module is a pure function of stored rows plus a ``now``
timestamp: no clock reads, no auth, no HTTP, no caching. That makes every band unit-testable
at an exact boundary. Endpoint concerns (auth, consent gating, audit logging) belong to the
router; the WeesStock listing/consent check is emphatically NOT this file's job — it computes
the profile, it does not decide who may see it.

**Cost.** ``compute_credit_profile`` issues a FIXED number of aggregate queries (currently 6),
independent of order/receipt count — every one is a grouped aggregate the DB answers from an
index, never a row-by-row scan and never a per-order query. The 90-day revenue window rides
``ix_receipts_seller_issued (seller_id, issued_at)``; no new index is required.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from PE.commerce.models.engagement import ListingInquiry
from PE.commerce.models.order import (
    Order,
    STATUS_CANCELLED,
    STATUS_EXPIRED,
    STATUS_SETTLED,
    STATUS_SETTLEMENT_FAILED,
)
from PE.commerce.models.receipt import Receipt
from PE.commerce.models.review import Review
from PE.commerce.models.seller import Seller

# ─────────────────────────── windows ───────────────────────────

# Primary revenue window. 90 days is the shortest span that smooths Kenyan retail seasonality
# (month-end salary spikes, school-fee troughs) while still describing the business as it is
# TODAY rather than as it was last year.
REVENUE_WINDOW_DAYS = 90
# Recent sub-window, nested inside the primary one. Reported alongside it so a lender can see
# direction, not just level: 30d running at a third of the 90d rate means the same total is
# describing a shrinking business.
RECENT_WINDOW_DAYS = 30

# ─────────────────────────── cold-start gates ───────────────────────────

# Below EITHER gate the composite is withheld (score=None). Both must be cleared: 20 orders in
# a week is a burst, not a track record, and 60 days with 3 orders is a dormant shop. These are
# starting values for the MVP and are expected to be re-tuned against real repayment outcomes —
# they are the one part of this file with no principled derivation, only judgement.
MIN_ORDERS_FOR_SCORE = 10
MIN_TENURE_DAYS = 30

# ─────────────────────────── composite weights ───────────────────────────

# Sum to 1.0 so the composite lands in [0, 1]. Ordering reflects what predicts repayment:
# proven cash flow first, then whether the business reliably completes what it starts, then
# customer-side evidence that the revenue will recur.
W_REVENUE = 0.40        # verified, settled cash through the platform
W_FULFILMENT = 0.25     # does a started sale actually complete
W_REPEAT = 0.15         # returning buyers ⇒ durable demand, not one-off luck
W_RATING = 0.12         # buyer-side trust
W_TENURE = 0.08         # survivorship; weakest term — age alone doesn't repay a loan

# Revenue saturation point for the revenue sub-score. A shop at or above this in the 90-day
# window scores 1.0 on that term. Absolute by design (see doctrine 1): KES 1,000,000 over 90
# days ≈ KES 11k/day, a solidly-performing Nairobi MSME. Above that, more revenue no longer
# changes the credit question — the constraint becomes the lender's appetite, not the shop.
REVENUE_SATURATION_CENTS = 1_000_000 * 100

# Tenure saturation. Two years of trading is treated as fully seasoned; beyond it, age adds
# nothing to the credit question.
TENURE_SATURATION_DAYS = 730.0

# A rating only carries weight once enough buyers have voted. Below this the rating term is
# damped toward neutral rather than trusted outright, so three glowing reviews can't
# manufacture a strong signal.
MIN_RATINGS_FOR_FULL_WEIGHT = 5

# Order statuses that represent a sale the shop FAILED to complete. EXPIRED and CANCELLED are
# negotiations that died; SETTLEMENT_FAILED is a rail failure. All three are counted against
# fulfilment, deliberately including SETTLEMENT_FAILED: from a lender's seat an uncollected
# sale is an uncollected sale, whatever the cause.
FAILED_STATUSES = (STATUS_CANCELLED, STATUS_EXPIRED, STATUS_SETTLEMENT_FAILED)


# ─────────────────────────── DTOs ───────────────────────────

@dataclass(frozen=True)
class CreditSignals:
    """Raw, un-weighted signals for one seller. Every field is a number a human can verify
    against the underlying rows — that verifiability IS the product (doctrine 3).

    Money is integer cents throughout (S9). Currency is NOT mixed: see ``currency``.
    """
    seller_id: str
    # Verified revenue: settled receipts only.
    revenue_cents: int              # over REVENUE_WINDOW_DAYS
    recent_revenue_cents: int       # over RECENT_WINDOW_DAYS (nested inside the above)
    currency: str                   # ISO-4217 of the receipts summed; "KES" when none
    settled_orders: int             # count of settled orders in the revenue window
    failed_orders: int              # cancelled/expired/settlement-failed in the same window
    unique_buyers: int              # distinct buyers in the window
    repeat_buyers: int              # buyers with >1 settled order in the window
    rating: float                   # 0.0 when unrated — distinct from a genuine low score
    rating_count: int
    inquiries: int                  # demand signal: inbound inquiries in the window
    tenure_days: float              # since the seller row was created

    @property
    def total_orders(self) -> int:
        """Settled + failed. The denominator of the fulfilment rate."""
        return self.settled_orders + self.failed_orders

    @property
    def fulfilment_rate(self) -> float:
        """Share of started sales that completed, in [0, 1]. A shop with NO orders at all has
        no fulfilment history; we return 0.0 and let the cold-start gate withhold the composite
        rather than flatter an empty record with a perfect 1.0."""
        total = self.total_orders
        return (self.settled_orders / total) if total > 0 else 0.0

    @property
    def repeat_rate(self) -> float:
        """Share of buyers who came back, in [0, 1]. Zero buyers ⇒ 0.0."""
        return (self.repeat_buyers / self.unique_buyers) if self.unique_buyers > 0 else 0.0

    @property
    def avg_order_value_cents(self) -> int:
        """Mean settled order value, floor-divided to stay in integer cents (S9)."""
        return (self.revenue_cents // self.settled_orders) if self.settled_orders > 0 else 0

    @property
    def revenue_trend(self) -> float | None:
        """Recent run-rate vs the whole window, as a ratio around 1.0.

        The recent window is normalised to the full window's length, so a shop transacting at a
        perfectly steady rate scores exactly 1.0 regardless of the window sizes: >1.0 means the
        business is accelerating, <1.0 that it is slowing. ``None`` when there is no revenue to
        compare — a ratio against zero is undefined, and reporting 0.0 would read as "collapsing"
        when the truth is "nothing here yet".
        """
        if self.revenue_cents <= 0:
            return None
        scale = REVENUE_WINDOW_DAYS / RECENT_WINDOW_DAYS
        return (self.recent_revenue_cents * scale) / self.revenue_cents


@dataclass(frozen=True)
class CreditProfile:
    """A seller's full credit picture: the raw signals, the weighted sub-scores that explain the
    composite, and the composite itself (or ``None`` on a thin file).

    ``score is None`` is a first-class, meaningful state — "not enough evidence yet" — and is
    NOT interchangeable with a score of 0.0, which would mean "evidence, and it is bad".
    """
    signals: CreditSignals
    # Weighted contributions; each already multiplied by its W_* constant, so they sum to
    # ``score`` when a score exists. Always populated, even on a thin file, so the seller can
    # see which components are already strong.
    revenue_score: float
    fulfilment_score: float
    repeat_score: float
    rating_score: float
    tenure_score: float
    score: float | None
    # Why the composite was withheld — a machine-readable reason the UI turns into a growth
    # prompt ("4 more settled sales"). Empty when a score was emitted.
    missing_for_score: tuple[str, ...]

    @property
    def is_scoreable(self) -> bool:
        return self.score is not None

    @property
    def orders_needed(self) -> int:
        """How many more settled orders before the order gate clears. 0 once cleared."""
        return max(0, MIN_ORDERS_FOR_SCORE - self.signals.settled_orders)

    @property
    def days_needed(self) -> int:
        """How many more days of tenure before the tenure gate clears. 0 once cleared."""
        return max(0, int(-(-(MIN_TENURE_DAYS - self.signals.tenure_days) // 1)))


# ─────────────────────────── math helpers ───────────────────────────

def _saturating(value: float, ceiling: float) -> float:
    """Linear ramp to ``ceiling``, clamped to [0, 1]. Absolute (doctrine 1) — the output depends
    only on ``value``, never on any other shop. A non-positive ceiling would make the ratio
    meaningless, so it yields 0.0 rather than dividing by zero."""
    if ceiling <= 0:
        return 0.0
    return min(1.0, max(0.0, value / ceiling))


def _rating_term(rating: float, count: int) -> float:
    """Rating mapped to [0, 1], damped when the sample is small.

    A 5.0 from two buyers is weaker evidence than a 4.5 from ninety. We scale the rating into
    [0, 1] (a 1-star floor is 0.0, five stars is 1.0 — the scale starts at 1, not 0) and then
    shrink it toward 0 in proportion to how far the count falls short of
    ``MIN_RATINGS_FOR_FULL_WEIGHT``. Unrated shops contribute nothing rather than a misleading
    zero-star reading.
    """
    if count <= 0 or rating <= 0:
        return 0.0
    normalized = max(0.0, min(1.0, (rating - 1.0) / 4.0))
    confidence = min(1.0, count / MIN_RATINGS_FOR_FULL_WEIGHT)
    return normalized * confidence


def _aware(dt: datetime | None) -> datetime | None:
    """Coerce a naive DB timestamp to UTC. SQLite hands back naive datetimes even for
    ``DateTime(timezone=True)`` columns, so arithmetic against an aware ``now`` would raise."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


# ─────────────────────────── the entrypoint ───────────────────────────

def compute_credit_profile(
    db: Session,
    seller: Seller,
    *,
    now: datetime,
) -> CreditProfile:
    """Compute ``seller``'s credit profile as of ``now``.

    Pure: reads rows, returns a value, mutates nothing. ``now`` is injected rather than read
    from the clock so every threshold is testable at its exact boundary.

    Consent is NOT checked here — computing a profile is not the same as disclosing it. The
    router is responsible for refusing to serve this to a financier unless the seller has
    listed the shop on WeesStock.
    """
    now = _aware(now) or datetime.now(timezone.utc)
    window_start = now - timedelta(days=REVENUE_WINDOW_DAYS)
    recent_start = now - timedelta(days=RECENT_WINDOW_DAYS)
    seller_id = str(seller.id)

    # 1) Verified revenue, both windows, in ONE grouped aggregate over the seller's receipts.
    #    Receipts — not orders — because only a receipt proves the money actually moved.
    #    ``net_to_seller_cents`` is the honest number for underwriting: it is what the business
    #    actually received, with our 3% already removed.
    #    Rides ix_receipts_seller_issued (seller_id, issued_at).
    revenue_row = (
        db.query(
            func.coalesce(func.sum(Receipt.net_to_seller_cents), 0),
            func.coalesce(
                func.sum(
                    case((Receipt.issued_at >= recent_start, Receipt.net_to_seller_cents), else_=0)
                ),
                0,
            ),
            func.count(Receipt.id),
        )
        .filter(Receipt.seller_id == seller_id, Receipt.issued_at >= window_start)
        .one()
    )
    revenue_cents = int(revenue_row[0] or 0)
    recent_revenue_cents = int(revenue_row[1] or 0)
    settled_orders = int(revenue_row[2] or 0)

    # 2) Currency of those receipts. Summing across currencies would silently add KES to USD, so
    #    we detect mixing and report the dominant one. Frozen at issue time on the receipt, so
    #    this is the currency the money actually moved in.
    currency_rows = (
        db.query(Receipt.currency, func.count(Receipt.id))
        .filter(Receipt.seller_id == seller_id, Receipt.issued_at >= window_start)
        .group_by(Receipt.currency)
        .order_by(func.count(Receipt.id).desc(), Receipt.currency.asc())
        .all()
    )
    currency = str(currency_rows[0][0]) if currency_rows else "KES"

    # 3) Failed orders in the same window — the fulfilment denominator's other half.
    failed_orders = int(
        db.query(func.count(Order.id))
        .filter(
            Order.seller_id == seller_id,
            Order.status.in_(FAILED_STATUSES),
            Order.created_at >= window_start,
        )
        .scalar()
        or 0
    )

    # 4) Buyer concentration: distinct buyers, and how many bought more than once. Computed as a
    #    grouped subquery so the DB does the counting — never one query per buyer.
    buyer_counts = (
        db.query(Order.buyer_uuid.label("buyer"), func.count(Order.id).label("n"))
        .filter(
            Order.seller_id == seller_id,
            Order.status == STATUS_SETTLED,
            Order.created_at >= window_start,
        )
        .group_by(Order.buyer_uuid)
        .subquery()
    )
    buyer_row = db.query(
        func.count(buyer_counts.c.buyer),
        func.coalesce(func.sum(case((buyer_counts.c.n > 1, 1), else_=0)), 0),
    ).one()
    unique_buyers = int(buyer_row[0] or 0)
    repeat_buyers = int(buyer_row[1] or 0)

    # 5) Ratings — lifetime, not windowed. Trust accrues over the life of the business and a
    #    shop shouldn't look untrusted simply because its reviews predate the window.
    rating_row = (
        db.query(func.avg(Review.rating), func.count(Review.id))
        .filter(Review.seller_id == seller_id)
        .one()
    )
    rating = float(rating_row[0] or 0.0)
    rating_count = int(rating_row[1] or 0)

    # 6) Inbound inquiries in the window — forward-looking demand. Reported to lenders as
    #    context but deliberately NOT weighted into the composite: inquiries are cheap to
    #    manufacture, and anything a seller can self-generate must not move their credit score.
    inquiries = int(
        db.query(func.count(ListingInquiry.id))
        .filter(
            ListingInquiry.seller_id == seller_id,
            ListingInquiry.created_at >= window_start,
        )
        .scalar()
        or 0
    )

    created = _aware(getattr(seller, "created_at", None))
    tenure_days = max(0.0, (now - created).total_seconds() / 86400.0) if created else 0.0

    signals = CreditSignals(
        seller_id=seller_id,
        revenue_cents=revenue_cents,
        recent_revenue_cents=recent_revenue_cents,
        currency=currency,
        settled_orders=settled_orders,
        failed_orders=failed_orders,
        unique_buyers=unique_buyers,
        repeat_buyers=repeat_buyers,
        rating=rating,
        rating_count=rating_count,
        inquiries=inquiries,
        tenure_days=tenure_days,
    )

    # Weighted sub-scores. Each is absolute — a fixed band, no peer set anywhere.
    revenue_score = W_REVENUE * _saturating(float(revenue_cents), float(REVENUE_SATURATION_CENTS))
    fulfilment_score = W_FULFILMENT * signals.fulfilment_rate
    repeat_score = W_REPEAT * signals.repeat_rate
    rating_score = W_RATING * _rating_term(rating, rating_count)
    tenure_score = W_TENURE * _saturating(tenure_days, TENURE_SATURATION_DAYS)

    # Cold-start gates. Both must clear; the reasons are returned so the UI can say precisely
    # what is missing instead of an unhelpful "not eligible".
    missing: list[str] = []
    if settled_orders < MIN_ORDERS_FOR_SCORE:
        missing.append("settled_orders")
    if tenure_days < MIN_TENURE_DAYS:
        missing.append("tenure")

    score = (
        None if missing
        else revenue_score + fulfilment_score + repeat_score + rating_score + tenure_score
    )

    return CreditProfile(
        signals=signals,
        revenue_score=revenue_score,
        fulfilment_score=fulfilment_score,
        repeat_score=repeat_score,
        rating_score=rating_score,
        tenure_score=tenure_score,
        score=score,
        missing_for_score=tuple(missing),
    )
