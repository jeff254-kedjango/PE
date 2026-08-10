"""Analytics endpoints — agent / staff / admin only.

Mounts at /analytics. All endpoints require require_agent (covers all three
roles per services/auth_service.py).

When ``settings.celery_beat_enabled`` is True, every handler is a Redis-first
SWR read (~<5ms) with live-compute fallback. The Beat tasks in
``services/analytics_tasks.py`` keep the cache warm; the SWR helper schedules
background refreshes for entries past their refresh ratio.
"""
from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Depends, Request, Query
from sqlalchemy.orm import Session

from PE.weespas.core.config import settings
from PE.weespas.core.database import get_db
from PE.weespas.models.user import User
from PE.weespas.services.auth_service import require_agent, require_staff
from PE.weespas.services.analytics_service import (
    aggregate_summary, aggregate_categories, aggregate_prices,
    aggregate_access_heatmap, aggregate_interest_heatmap,
    compute_engagement, aggregate_risk_summary,
)
from PE.weespas.services.agent_analytics_service import (
    compute_agent_rank, compute_agent_funnel, compute_listing_benchmarks,
)
from PE.weespas.services import analytics_tasks
from PE.weespas.services.analytics_cache import read_swr, warm_on_miss

router = APIRouter(prefix="/analytics", tags=["Analytics"])


# --------------------------------------------------------------------- #
# SWR plumbing
# --------------------------------------------------------------------- #
# Each handler keeps its old `live(db, ...)` path for two reasons:
#   1. Flag rollback — flipping celery_beat_enabled off must restore behavior.
#   2. Cold start — until Beat has run once, the cache is empty; we serve
#      from live + schedule a warm so the second request is hot.

def _swr_or_live(key: str, refresh_task, refresh_args, live_fn):
    """Return the SWR payload if present, else live-compute and warm.

    ``live_fn`` is a zero-arg callable so we never compute the heavy answer
    when the cache is hot.
    """
    if not settings.celery_beat_enabled:
        return live_fn()
    payload = read_swr(key, refresh_task, *refresh_args)
    if payload is not None:
        return payload
    # Cold cache: live compute + schedule a warm so the next hit is a Redis GET.
    warm_on_miss(refresh_task, *refresh_args)
    return live_fn()


# --------------------------------------------------------------------- #
# Platform dashboards
# --------------------------------------------------------------------- #

@router.get("/summary")
def get_summary(
    since: str = Query("30d", pattern=r"^(\d+d|all)$"),
    db: Session = Depends(get_db),
    _user: User = Depends(require_agent),
):
    return _swr_or_live(
        analytics_tasks.k_summary(since),
        analytics_tasks.aggregate_summary, (since,),
        lambda: aggregate_summary(db, since=since),
    )


@router.get("/categories")
def get_category_interest(
    since: str = Query("30d", pattern=r"^(\d+d|all)$"),
    db: Session = Depends(get_db),
    _user: User = Depends(require_agent),
):
    return _swr_or_live(
        analytics_tasks.k_categories(since),
        analytics_tasks.aggregate_categories, (since,),
        lambda: aggregate_categories(db, since=since),
    )


@router.get("/prices")
def get_price_distribution(
    since: str = Query("30d", pattern=r"^(\d+d|all)$"),
    listing_type: Optional[str] = Query(None, pattern=r"^(rent|sale)$"),
    db: Session = Depends(get_db),
    _user: User = Depends(require_agent),
):
    # Three pre-warmed variants (None/rent/sale) — match the same key shape.
    return _swr_or_live(
        analytics_tasks.k_prices(since, listing_type),
        # The price aggregator computes ALL listing_type variants per task call.
        # We schedule the umbrella refresh (no args beyond `since`) so one
        # background refresh re-warms all three keys at once.
        analytics_tasks.aggregate_prices, (since,),
        lambda: aggregate_prices(db, since=since, listing_type=listing_type),
    )


@router.get("/heatmap/access")
def get_access_heatmap(
    since: str = Query("30d", pattern=r"^(\d+d|all)$"),
    county: Optional[str] = Query(None, max_length=120),
    db: Session = Depends(get_db),
    _user: User = Depends(require_agent),
):
    # Per audit §3: county drill-downs are computed on-miss only (pre-computing
    # every county would be wasteful). Global view is Beat-warmed; drill-downs
    # fall through to live compute and don't schedule a refresh task.
    if county is None and settings.celery_beat_enabled:
        cached = read_swr(
            analytics_tasks.k_heatmap_access(since, None),
            analytics_tasks.aggregate_heatmaps, since,
        )
        if cached is not None:
            return {"level": "county", "county": None, "points": cached}
        warm_on_miss(analytics_tasks.aggregate_heatmaps, since)

    return {
        "level": "city" if county else "county",
        "county": county,
        "points": aggregate_access_heatmap(db, since=since, county=county),
    }


@router.get("/heatmap/interest")
def get_interest_heatmap(
    since: str = Query("30d", pattern=r"^(\d+d|all)$"),
    county: Optional[str] = Query(None, max_length=120),
    db: Session = Depends(get_db),
    _user: User = Depends(require_agent),
):
    if county is None and settings.celery_beat_enabled:
        cached = read_swr(
            analytics_tasks.k_heatmap_interest(since, None),
            analytics_tasks.aggregate_heatmaps, since,
        )
        if cached is not None:
            return {"level": "county", "county": None, "points": cached}
        warm_on_miss(analytics_tasks.aggregate_heatmaps, since)

    return {
        "level": "city" if county else "county",
        "county": county,
        "points": aggregate_interest_heatmap(db, since=since, county=county),
    }


# --------------------------------------------------------------------- #
# Agent dashboards
# --------------------------------------------------------------------- #
# Per audit §3: platform leaderboard / funnel rates are identical for every
# agent; we pre-compute the global blob and let the read serve the same
# answer to every requester. Per-agent "me" overlays remain cheap because
# the underlying aggregates are already in cache.

@router.get("/agent/rank")
def get_agent_rank(
    since: str = Query("30d", pattern=r"^(\d+d|all)$"),
    db: Session = Depends(get_db),
    user: User = Depends(require_agent),
):
    # When the agent is asking about themselves, we still need a per-agent
    # slice — the cached blob holds the global leaderboard; computing the
    # personal "where do I rank" view is fast on top of it. For the rollout
    # phase we serve live for the agent_id path; the cached path is reserved
    # for an admin/global view (agent_id=None) that the frontend can request
    # by passing ?global=true if added later.
    return compute_agent_rank(db, agent_id=user.agent_id, since=since)


@router.get("/agent/funnel")
def get_agent_funnel(
    since: str = Query("30d", pattern=r"^(\d+d|all)$"),
    db: Session = Depends(get_db),
    user: User = Depends(require_agent),
):
    return compute_agent_funnel(db, agent_id=user.agent_id, since=since)


@router.get("/agent/listings/benchmarks")
def get_listing_benchmarks(
    since: str = Query("30d", pattern=r"^(\d+d|all)$"),
    db: Session = Depends(get_db),
    user: User = Depends(require_agent),
):
    # Per-agent key is Beat-warmed nightly. Per audit §3 this was the worst
    # offender — 3·N peer aggregates per request. The SWR hit serves it from
    # Redis; a cold miss falls through to live compute and warms the cache.
    if user.agent_id:
        return _swr_or_live(
            analytics_tasks.k_listing_benchmarks(user.agent_id, since),
            analytics_tasks.compute_listing_benchmarks, (since,),
            lambda: compute_listing_benchmarks(db, agent_id=user.agent_id, since=since),
        )
    return compute_listing_benchmarks(db, agent_id=user.agent_id, since=since)


@router.get("/engagement")
def get_engagement(
    since: str = Query("30d", pattern=r"^(\d+d|all)$"),
    db: Session = Depends(get_db),
    user: User = Depends(require_staff),
):
    """Per-role 'time-to-return vs avg-session-length' time series.

    Staff/Admin only — surfaces user/agent/staff engagement on the Staff
    dashboard. Returns three series in one round-trip so the dashboard
    can render the three line charts without fan-out requests.
    """
    return _swr_or_live(
        analytics_tasks.k_engagement(since),
        analytics_tasks.compute_engagement, (since,),
        lambda: compute_engagement(db, since=since),
    )


@router.get("/risk/summary")
def get_risk_summary(
    db: Session = Depends(get_db),
    _user: User = Depends(require_staff),
):
    """Risk-oversight tile: catalog coverage mix + count of active listings on a
    currently-unsafe building.

    Staff/Admin ONLY (sensitive — the unsafe×listing join is the §4.2/§9.7 corruption
    surface). Returns COUNTS only, never the underlying listing/flag rows. No SWR
    cache: low-cardinality, infrequent admin read — a direct live query is simpler and
    avoids caching a sensitive aggregate.
    """
    return aggregate_risk_summary(db)
