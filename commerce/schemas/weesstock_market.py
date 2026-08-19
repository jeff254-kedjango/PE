"""WeesStock market DTOs (§WeesStock F4) — the investor-facing discovery/analytics surface.

Discovery/analytics ONLY. Nothing here transacts; a future investment action lives behind a
separate, clearly-labelled, regulatory-aware surface (Kenya: Capital Markets
(Investment-Based Crowdfunding) Regulations 2022). The market exposes seller aggregates to
authenticated viewers strictly on the seller's opt-in (``Seller.weesstock_listed``).

Same money discipline as the credit profile (S9): integer cents + explicit ISO-4217
``currency``, never floats or formatted strings. Same privacy boundary: aggregates only —
never buyer identities, never per-order lines (the financier-facing shape is F4).
"""
from __future__ import annotations

from pydantic import BaseModel

from PE.commerce.schemas.weesstock import CreditProfileOut


class ListingToggleIn(BaseModel):
    """Body of POST /weesstock/me/listing — the seller's consent switch."""
    listed: bool


class ListingToggleOut(BaseModel):
    listed: bool


class RevenueSeries(BaseModel):
    """Weekly verified-revenue buckets over the 90-day window, oldest→newest.

    The last bucket is the current (partial) week. ``series_cents`` has exactly
    ``bucket_count`` entries; points are aggregate NET-to-seller cents (3% already removed),
    the same money the credit score is built from — a chart and a score that disagree would
    be a lie in one of two places.
    """
    series_cents: list[int]
    bucket_days: int
    bucket_count: int
    window_days: int
    currency: str


class MarketEntryOut(BaseModel):
    """One row of the market list — a ticker in the investor UI.

    Deliberately compact (this is a scrollable list): the score (the sort key), the money
    the market reads (90-day verified revenue), the momentum (30d vs 90d run-rate), the
    buyer-side rating, and the sparkline series. Everything a row needs; nothing more.
    """
    seller_id: str
    seller_name: str
    shop_name: str
    category: str | None
    # The composite, withheld on a thin file exactly as on the seller's own card.
    score: float | None
    is_scoreable: bool
    currency: str
    revenue_cents: int
    revenue_trend: float | None
    rating: float
    rating_count: int
    series: RevenueSeries


class MarketListOut(BaseModel):
    """GET /weesstock/markets — every consenting seller, bounded and deterministic."""
    entries: list[MarketEntryOut]
    # Window echoed so the UI can say "last 90 days" without hard-coding it.
    window_days: int
    # The reference a full revenue bar means (KES 50k/month — see services/credit_score.py).
    revenue_saturation_cents: int


class MarketSeller(BaseModel):
    seller_id: str
    seller_name: str
    shop_name: str
    category: str | None


class MarketDetailOut(BaseModel):
    """GET /weesstock/markets/{seller_id} — the full investor deep-dive.

    The complete credit profile (same shape the seller sees on their own card — no divergence
    between what the seller is told and what the investor is shown) plus the shop meta and the
    revenue series for the chart.
    """
    seller: MarketSeller
    profile: CreditProfileOut
    series: RevenueSeries
