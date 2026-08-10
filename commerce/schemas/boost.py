"""§8.3 Boost schemas — request a reach tier on a listing/shop, and the grant + quota responses.

The tier and target are constrained Literals so an unknown value is a 422 at the API boundary
before the service runs. ``duration_seconds`` is optional (defaults to the configured tier
window); when present it is edge-bounded generously and re-checked against the real config bounds
in the service (the service is the authority).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

# Mirror models.boost.BOOST_TIERS / BOOST_TARGETS — single legal set, enforced at the edge.
BoostTier = Literal["mtaa", "hustle", "sovereign"]
BoostTarget = Literal["listing", "shop"]


class BoostRequest(BaseModel):
    """Open a Boost on one of the caller's targets. ``target_type`` + ``target_id`` name the
    listing or shop; ``tier`` is the reach tier; ``duration_seconds`` is optional (the tier's
    default window is used when omitted)."""
    target_type: BoostTarget
    target_id: str = Field(min_length=1, max_length=64)
    tier: BoostTier
    duration_seconds: int | None = Field(default=None, ge=1, le=2_592_000)  # ≤30d edge cap


class BoostGrantOut(BaseModel):
    id: str
    seller_id: str
    target_type: BoostTarget
    target_id: str
    tier: BoostTier
    scope_kind: str
    radius_m: float | None = None
    started_at: datetime
    expires_at: datetime
    business_date: date
    source: str


class TierAllowanceOut(BaseModel):
    tier: BoostTier
    daily_cap: int
    remaining: int


class BoostAllowancesOut(BaseModel):
    """The caller's remaining free chances per tier for the current business day."""
    business_date: date
    tiers: list[TierAllowanceOut]


def to_boost_grant_out(grant) -> BoostGrantOut:
    return BoostGrantOut(
        id=str(grant.id),
        seller_id=str(grant.seller_id),
        target_type=grant.target_type,
        target_id=str(grant.target_id),
        tier=grant.tier,
        scope_kind=grant.scope_kind,
        radius_m=grant.radius_m,
        started_at=grant.started_at,
        expires_at=grant.expires_at,
        business_date=grant.business_date,
        source=grant.source,
    )


# --- §8.3 tier METADATA (GET /boosts/tiers) — the single server-authoritative description of each
# reach tier, so the FE chooser stops hard-coding reach copy / caps / prices (drift risk). Every
# field is read from config at request time; price_kes is the DISPLAY-ONLY nominal price (never
# charged — see settings.boost_tier_price_kes). --------------------------------------------------
class BoostTierMetaOut(BaseModel):
    tier: BoostTier
    scope_kind: str                       # 'mtaa' | 'hustle' | 'sovereign' scope label
    radius_m: float | None = None         # None ⇒ nationwide (sovereign)
    daily_free_cap: int                   # free grants/day for this tier
    duration_default_seconds: int         # the tier's default window
    price_kes: int                        # NOMINAL, display-only (0 = free today)


class BoostTiersOut(BaseModel):
    """Server-authoritative tier catalogue for the FE Boost chooser."""
    tiers: list[BoostTierMetaOut]


# --- §8.3 per-shop sponsored-cap OVERRIDE (item 1) — apply / decide / list schemas. -------------
CapOverrideStatus = Literal["pending", "approved", "rejected"]


class CapOverrideRequest(BaseModel):
    """A seller's application for a per-shop absolute sponsored cap. The upper bound is re-checked
    against settings.boost_cap_override_max in the service (the service is the authority); this edge
    bound is a generous guard so an absurd value is a 422 before the DB is touched."""
    requested_cap: int = Field(ge=1, le=1000)


class CapOverrideDecision(BaseModel):
    """A staff decision on a pending override. When ``approve`` is true, ``approved_cap`` is the
    granted absolute cap (defaults to the requested cap when omitted); on reject it is ignored."""
    approve: bool
    approved_cap: int | None = Field(default=None, ge=1, le=1000)


class CapOverrideOut(BaseModel):
    id: str
    shop_id: str
    requested_cap: int
    status: CapOverrideStatus
    approved_cap: int | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None


class CapOverrideStatusOut(BaseModel):
    """The seller-facing status of their OWN shop's sponsored-cap override (non-destructive read).
    ``override`` is null when the seller has never applied. ``max_cap`` / ``default_cap`` are the
    server-authoritative ceiling (``boost_cap_override_max``) and global fallback
    (``feed_sponsored_max_per_shop``) — the FE bounds its input and shows context from THESE rather
    than hard-coding them, so the UI and backend config can never drift (the Chunk-6 lesson)."""
    override: CapOverrideOut | None = None
    max_cap: int
    default_cap: int


class PendingCapListOut(BaseModel):
    overrides: list[CapOverrideOut]
    # Server-authoritative ceiling for an approvable cap, so the staff decide input bounds itself
    # off this (anti-drift) without a second call.
    max_cap: int


def to_cap_override_out(row) -> CapOverrideOut:
    return CapOverrideOut(
        id=str(row.id),
        shop_id=str(row.shop_id),
        requested_cap=row.requested_cap,
        status=row.status,
        approved_cap=row.approved_cap,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
    )
