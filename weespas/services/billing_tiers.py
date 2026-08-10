"""Subscription tiers for the listing-location access model.

Single source of truth for the access ladder described in PE/commercial_model.md §5
and PE/billing_architecture.md §2.3. A *window* buys N reveals (the "locations") for
T seconds; a "reveal" sharpens one listing's exact, navigable location (see
entitlement_service). Prices are in KES.

These are deliberately overridable via env (the ladder is "adjustable, not set in
stone" — commercial_model.md §5.1), but the defaults ARE the agreed ladder:
    T1: 20 KES → 3 locations / 2 hours
    T2: 50 KES → 6 locations / 4 hours
    T3: 100 KES → 10 locations / 24 hours
plus a free HOOK tier (1 location / 30 min) used as CAC for the core product (§7).
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Tier:
    code: str
    price_kes: int
    quota: int            # number of distinct listings revealable in the window
    window_seconds: int   # how long the window stays active


def _i(env: str, default: int) -> int:
    """Env override as int, falling back to the agreed default."""
    raw = os.environ.get(env)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


# The paid ladder. HOOK is handled separately (granted free, see entitlement_service).
PAID_TIERS: dict[str, Tier] = {
    "T1": Tier("T1", _i("BILLING_T1_PRICE", 20), _i("BILLING_T1_QUOTA", 3), _i("BILLING_T1_WINDOW_S", 2 * 3600)),
    "T2": Tier("T2", _i("BILLING_T2_PRICE", 50), _i("BILLING_T2_QUOTA", 6), _i("BILLING_T2_WINDOW_S", 4 * 3600)),
    "T3": Tier("T3", _i("BILLING_T3_PRICE", 100), _i("BILLING_T3_QUOTA", 10), _i("BILLING_T3_WINDOW_S", 24 * 3600)),
}

HOOK_TIER: Tier = Tier(
    "HOOK", 0, _i("BILLING_HOOK_QUOTA", 1), _i("BILLING_HOOK_WINDOW_S", 30 * 60)
)

# Cooldown between free hook grants per user (anti-farming). Default 24h.
HOOK_COOLDOWN_SECONDS: int = _i("BILLING_HOOK_COOLDOWN_S", 24 * 3600)


def all_tiers() -> dict[str, Tier]:
    """Every tier by code, including HOOK — for lookups in grant_window."""
    return {**PAID_TIERS, HOOK_TIER.code: HOOK_TIER}


def get_tier(code: str) -> Tier | None:
    return all_tiers().get(code)
