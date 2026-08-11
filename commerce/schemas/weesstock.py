"""WeesStock DTOs (§WeesStock F2) — the seller's own credit profile.

The response deliberately mirrors ``services.credit_score.CreditProfile`` one-for-one rather
than flattening it into a single number: components ARE the product, the composite is only a
sort key. A lender must be able to see WHY, and so must the seller.

Money crosses the wire as integer cents plus an explicit ISO-4217 ``currency`` (S9) — never a
formatted string and never a float. Rendering is the client's job.

Nothing here carries buyer PII. Everything is an aggregate over the caller's OWN rows: counts,
rates, and sums. That is a hard boundary — this same shape is what a financier will eventually
see (F4), and buyers never consented to appear in a seller's funding application.
"""
from __future__ import annotations

from pydantic import BaseModel

from PE.commerce.services import credit_score as cs


class CreditComponent(BaseModel):
    """One weighted contribution to the composite, with everything needed to explain it.

    ``value`` is the underlying measurement in its natural unit (a rate in [0, 1], or cents for
    revenue); ``weighted`` is that value's contribution AFTER its weight, so the components sum
    to the composite exactly. ``weight`` is echoed so the UI can show "revenue counts for 40%"
    without hard-coding constants that live in the service.
    """
    key: str
    label: str
    weighted: float
    weight: float


class CreditProfileOut(BaseModel):
    """Response of GET /sellers/me/credit-profile.

    ``score`` is ``None`` on a thin file — a first-class state meaning "not enough evidence
    yet", NOT a low score. The client MUST branch on it rather than coercing to 0: rendering a
    0 would tell a healthy new shop it is uncreditworthy. ``missing_for_score`` /
    ``orders_needed`` / ``days_needed`` carry exactly what is still required, so the empty
    state can be a growth prompt instead of a dead end.
    """
    # Composite — absent on a thin file.
    score: float | None
    is_scoreable: bool
    missing_for_score: list[str]
    orders_needed: int
    days_needed: int

    # Verified money (settled receipts only — never locked-but-unsettled orders).
    currency: str
    revenue_cents: int
    recent_revenue_cents: int
    avg_order_value_cents: int
    # Recent run-rate vs the full window, normalised so steady trading reads 1.0. None when
    # there is no revenue to compare — a ratio against zero is undefined.
    revenue_trend: float | None

    # Execution + demand.
    settled_orders: int
    failed_orders: int
    fulfilment_rate: float
    unique_buyers: int
    repeat_buyers: int
    repeat_rate: float
    rating: float
    rating_count: int
    # Reported for context but weighted ZERO in the composite: a seller can generate inquiries
    # at will, and nothing self-generatable may move a credit score.
    inquiries: int
    tenure_days: int

    # The explainable breakdown. Sums to ``score`` when a score exists.
    components: list[CreditComponent]

    # Window sizes, echoed so the UI never hard-codes "90 days" in copy that could drift from
    # the service constants.
    window_days: int
    recent_window_days: int


def to_credit_profile_out(profile: cs.CreditProfile) -> CreditProfileOut:
    """Map the service DTO to the wire shape. Pure; no DB access."""
    s = profile.signals
    return CreditProfileOut(
        score=profile.score,
        is_scoreable=profile.is_scoreable,
        missing_for_score=list(profile.missing_for_score),
        orders_needed=profile.orders_needed,
        days_needed=profile.days_needed,
        currency=s.currency,
        revenue_cents=s.revenue_cents,
        recent_revenue_cents=s.recent_revenue_cents,
        avg_order_value_cents=s.avg_order_value_cents,
        revenue_trend=s.revenue_trend,
        settled_orders=s.settled_orders,
        failed_orders=s.failed_orders,
        fulfilment_rate=s.fulfilment_rate,
        unique_buyers=s.unique_buyers,
        repeat_buyers=s.repeat_buyers,
        repeat_rate=s.repeat_rate,
        rating=s.rating,
        rating_count=s.rating_count,
        inquiries=s.inquiries,
        # Whole days: the UI shows "14 months" / "63 days", never a fractional day.
        tenure_days=int(s.tenure_days),
        components=[
            CreditComponent(key="revenue", label="Verified revenue",
                            weighted=profile.revenue_score, weight=cs.W_REVENUE),
            CreditComponent(key="fulfilment", label="Fulfilment rate",
                            weighted=profile.fulfilment_score, weight=cs.W_FULFILMENT),
            CreditComponent(key="repeat", label="Repeat buyers",
                            weighted=profile.repeat_score, weight=cs.W_REPEAT),
            CreditComponent(key="rating", label="Buyer rating",
                            weighted=profile.rating_score, weight=cs.W_RATING),
            CreditComponent(key="tenure", label="Trading history",
                            weighted=profile.tenure_score, weight=cs.W_TENURE),
        ],
        window_days=cs.REVENUE_WINDOW_DAYS,
        recent_window_days=cs.RECENT_WINDOW_DAYS,
    )
