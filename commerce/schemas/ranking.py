"""Ranking API schemas (§8, Chunk B). The frontend's Ranking Card consumes RankingOut when
allowed, RankingPaywallOut when the caller has requested a radius > 200 km without an active
entitlement. Both responses are 200s — the paywall is a NORMAL answer the frontend renders as
a CTA, not an HTTP error. Same pattern as /shops/handle-available (a syntax-invalid handle is
a legitimate {available:false, reason:...} answer, not a 422)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class RankingWeightBreakdown(BaseModel):
    """Explainability: how the final score decomposes. All fields are in [0, 1] and sum to at
    most 1.0. The frontend uses this to render a "why this rank?" tooltip / hover."""
    sales_score: float = Field(ge=0.0, le=1.0)
    composite_score: float = Field(ge=0.0, le=1.0)


class RankingSignals(BaseModel):
    """Raw signals the caller can display — the actual revenue in cents, follower count, etc.
    ``rating`` is 0.0 for an unrated shop AND flagged by ``rating_count == 0`` so the UI can
    show "unrated" instead of a misleading 0-star average."""
    revenue_cents: int = Field(ge=0)
    revenue_window_days: int = Field(gt=0)
    rating: float = Field(ge=0.0, le=5.0)
    rating_count: int = Field(ge=0)
    follower_count: int = Field(ge=0)
    saves_total: int = Field(ge=0)


class RankingOut(BaseModel):
    """The Ranking Card's happy-path response."""
    kind: Literal["ranking"] = "ranking"
    rank: int = Field(ge=1)                     # 1-indexed
    peer_count: int = Field(ge=1)
    radius_km: float = Field(gt=0.0)
    refreshed_at: datetime                       # when THIS payload was computed
    next_refresh_at: datetime                    # 5 min after refreshed_at (cache TTL edge)
    own_score: float = Field(ge=0.0, le=1.0)
    weight_breakdown: RankingWeightBreakdown
    signals: RankingSignals


class RankingPaywallOut(BaseModel):
    """The paywall response for radius > 200 km without an active entitlement. The frontend
    renders a CTA offering the ``kinds`` on the ``cta_kinds`` list; today those two are the
    only kinds the entitlement table supports."""
    kind: Literal["paywall_required"] = "paywall_required"
    reason: Literal["radius_over_free_cap"] = "radius_over_free_cap"
    free_max_radius_km: float = Field(gt=0.0)   # 200.0 today
    requested_radius_km: float = Field(gt=0.0)
    cta_kinds: list[Literal["one_time_2h", "annual"]]


class RankingUnavailableOut(BaseModel):
    """The seller has no shop yet — the card renders a "create a shop to see your ranking"
    hint. NOT a 404: the /me/ranking endpoint always returns 200, the FE decides the layout
    from ``kind``. This keeps the frontend with ONE happy path for a signed-in seller."""
    kind: Literal["no_shop"] = "no_shop"


# Discriminated union — the FE branches on ``kind``.
RankingResponse = RankingOut | RankingPaywallOut | RankingUnavailableOut
