"""Shop ranking router (§8, Chunk B) — the seller-console Ranking Card's data source.

ONE endpoint: ``GET /api/v1/sellers/me/ranking?radius_km=N`` — the caller's rank within their
own neighborhood. Auth: same trust class as every other seller-owner write route (``create:
trades`` via ``require_scope``). No buyer-facing endpoint exists yet; the ranking is a seller
INTROSPECTION surface (rule 5: no dead code, so only expose the shape the FE will consume).

Response shape is a **discriminated union** over ``kind``:
  * ``ranking``           — normal happy path (RankingOut).
  * ``paywall_required``  — radius > 200 km without an entitlement (RankingPaywallOut).
  * ``no_shop``           — the seller hasn't opened a shop yet (RankingUnavailableOut).
All three are 200s so the FE has ONE consuming path; error status codes are reserved for
transport failures.

Caching: a small in-process TTL dict, keyed by (seller_uuid, rounded radius_km). 5-minute
TTL matches the user's stated refresh cadence. Not shared across processes — the cost of a
cross-process cache would be gunicorn-level Redis coordination for a lookup that's already
O(log n) at the DB. If we scale up, revisit; for now, per-process is right.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from PE.commerce.core.auth import CommercePrincipal, require_scope
from PE.commerce.core.database import get_db
from PE.commerce.schemas import ranking as ranking_schemas
from PE.commerce.services import ranking_entitlement, shop_ranking

router = APIRouter(prefix="/sellers", tags=["ranking"])

# Same scope as other seller-owner writes — a caller who can't write to their own shop can't
# see its ranking either. The card is on /trade/sell, which is already a create:trades page.
_require_write = require_scope("create:trades")

# Free ranking radius cap. Anything above this needs a RankingEntitlement.
_FREE_RADIUS_KM = 200.0
# API-level cap so a caller doesn't pass e.g. 1e9 and blow up the bounding-box math.
_MAX_RADIUS_KM = 20_000.0
# Cache TTL — matches the user's "5-minute refresh" directive. Any refresh request within the
# window returns the same payload; the frontend polls at the same cadence (staleTime = 5min).
_CACHE_TTL_SECONDS = 300


@dataclass(frozen=True)
class _CacheEntry:
    payload: ranking_schemas.RankingOut
    expires_at: datetime


# Per-process TTL dict — mutation guarded by a lock so an inflight compute + a concurrent
# reader can never see a torn tuple. Cheap; the total keyset is O(sellers active in the last
# 5 minutes) which is tiny compared to the trending cache.
_cache: dict[tuple[str, int], _CacheEntry] = {}
_cache_lock = threading.Lock()


def _round_radius_for_cache(radius_km: float) -> int:
    """Round the radius to the nearest integer km for cache keying. Two seller requests for
    9.9 km vs 10.1 km share the same cache entry — the difference is smaller than the
    proximity index's bucket granularity anyway, and we don't want a slider-drag to blow the
    cache with a hundred float variants of "roughly 10 km"."""
    return max(1, int(round(radius_km)))


def _cache_get(key: tuple[str, int], now: datetime) -> ranking_schemas.RankingOut | None:
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        if entry.expires_at <= now:
            _cache.pop(key, None)
            return None
        return entry.payload


def _cache_set(key: tuple[str, int], payload: ranking_schemas.RankingOut, now: datetime) -> None:
    with _cache_lock:
        _cache[key] = _CacheEntry(payload=payload, expires_at=now + timedelta(seconds=_CACHE_TTL_SECONDS))


def _clear_cache_for_tests() -> None:
    """Test helper — a fresh process resets state, but the /_router picks the same interpreter
    across tests, so a per-test clear guards against a previous test's payload leaking into
    the next assertion. Not exposed on the API surface."""
    with _cache_lock:
        _cache.clear()


@router.get("/me/ranking", response_model=None)  # response_model=None: a Union response
def my_ranking(
    radius_km: float = Query(default=10.0, gt=0.0, le=_MAX_RADIUS_KM),
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(_require_write),
) -> ranking_schemas.RankingResponse:
    """The caller's Ranking Card data — where their shop ranks in a ``radius_km`` circle
    around it. Response is a discriminated union (see the module docstring)."""
    now = datetime.now(timezone.utc)

    # Paywall gate FIRST — an unfunded caller asking for a 300 km radius must not get any
    # ranking data at all, cached or not (a cache hit would leak the answer past the gate).
    if radius_km > _FREE_RADIUS_KM:
        if not ranking_entitlement.has_active_entitlement(db, principal.sub, now):
            return ranking_schemas.RankingPaywallOut(
                free_max_radius_km=_FREE_RADIUS_KM,
                requested_radius_km=radius_km,
                cta_kinds=["one_time_2h", "annual"],
            )

    cache_key = (principal.sub, _round_radius_for_cache(radius_km))
    cached = _cache_get(cache_key, now)
    if cached is not None:
        return cached

    result = shop_ranking.compute_shop_rank(
        db, seller_uuid=principal.sub, radius_km=radius_km, now=now,
    )
    if result is None:
        # Not cached — the "no shop" state changes as soon as the caller creates one; we don't
        # want them stuck seeing "no shop" for 5 minutes after opening one.
        return ranking_schemas.RankingUnavailableOut()

    payload = ranking_schemas.RankingOut(
        rank=result.rank,
        peer_count=result.peer_count,
        radius_km=radius_km,
        refreshed_at=now,
        next_refresh_at=now + timedelta(seconds=_CACHE_TTL_SECONDS),
        own_score=result.own_score,
        weight_breakdown=ranking_schemas.RankingWeightBreakdown(
            sales_score=result.sales_score,
            composite_score=result.composite_score,
        ),
        signals=ranking_schemas.RankingSignals(
            revenue_cents=result.signals.revenue_cents,
            revenue_window_days=shop_ranking.SALES_WINDOW_DAYS,
            rating=result.signals.rating,
            rating_count=result.signals.rating_count,
            follower_count=result.signals.follower_count,
            saves_total=result.signals.saves_total,
        ),
    )
    _cache_set(cache_key, payload, now)
    return payload
