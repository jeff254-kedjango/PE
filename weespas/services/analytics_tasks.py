"""Analytics Celery tasks — write offloads + Beat aggregators.

Two classes of tasks live here:

1. **Write offloads** (Phase 1.2, 1.3) — keep heavy-traffic request paths
   from doing extra INSERTs/UPDATEs. Routed to the `analytics` queue.

2. **Beat aggregators** (Phase 3) — recompute dashboard answers on a schedule
   and store them as JSON blobs in Redis under the keys defined below. The
   read path (`routers/analytics.py`) serves these blobs directly with a
   stale-while-revalidate fallback to live compute.

Blob envelope:
    {"computed_at": iso8601, "ttl": seconds, "payload": <task output>}

Storing the envelope (not the raw payload) lets the read path tell whether
the blob is past half-life and should be refreshed in the background.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from PE.weespas.core.celery_app import celery_app
from PE.weespas.core.config import settings
from PE.weespas.core.database import SessionLocal
from PE.weespas.services.cache import redis_client
from PE.weespas.services.celery_helpers import redis_setnx_lock

logger = logging.getLogger(__name__)


# ---- Cache key helpers --------------------------------------------------

def k_summary(since: str) -> str:                    return f"analytics:summary:{since}"
def k_categories(since: str) -> str:                 return f"analytics:categories:{since}"
def k_prices(since: str, listing_type: str | None) -> str:
    return f"analytics:prices:{since}:{listing_type or 'all'}"
def k_heatmap_access(since: str, county: str | None) -> str:
    return f"analytics:heatmap:access:{since}:{county or 'global'}"
def k_heatmap_interest(since: str, county: str | None) -> str:
    return f"analytics:heatmap:interest:{since}:{county or 'global'}"
def k_engagement(since: str) -> str:                 return f"analytics:engagement:{since}"
def k_agent_rank(since: str) -> str:                 return f"analytics:agent_rank:{since}"
def k_agent_funnel(since: str) -> str:               return f"analytics:agent_funnel:{since}"
def k_listing_benchmarks(agent_id: str, since: str) -> str:
    return f"analytics:benchmarks:agent:{agent_id}:{since}"
K_AGENT_PROP_COUNTS = "analytics:agent_prop_counts"   # Redis HASH

# ── Staff-eligibility precompute ─────────────────────────────────────
# One Redis HASH keyed by `agent_id` (= users.agent_id, which == agents.id),
# value is a packed pipe-delimited string `listings|views|months|eligible`.
#
# Why packed string instead of multiple HASHes or a per-agent JSON blob:
# - Single HGET on the hot path (GET /me/role-eligibility) returns
#   everything the modal needs in one Redis round-trip.
# - Packed bytes per record: ~16 bytes. At 1M agents the HASH is ~16 MB
#   — comfortably below Redis' ziplist→hashtable promotion threshold
#   concerns and trivially shippable across replication.
# - JSON would be ~2x the bytes and pay encode/decode cost on every read.
#   Pipe-split is one C call in Python and parsing is trivially correct
#   (fixed 4-field shape, no escaping concerns — all four fields are
#   integers).
K_STAFF_ELIGIBILITY = "analytics:staff_eligibility"   # Redis HASH


def _store(key: str, payload: Any, ttl: int) -> None:
    """Write the SWR envelope to Redis. Failure is swallowed — the next
    Beat tick will retry."""
    try:
        envelope = {
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "ttl": ttl,
            "payload": payload,
        }
        redis_client.setex(key, ttl, json.dumps(envelope, default=str))
    except Exception as exc:
        logger.warning("analytics _store(%s) failed: %s", key, exc)


# =====================================================================
# Phase 1.2 — Search log write offload
# =====================================================================

@celery_app.task(
    name="analytics.log_search_async",
    ignore_result=True,
    acks_late=False,
    # A lost log row is acceptable; the user-visible search result is what matters.
)
def log_search_async(
    session_id: Optional[str],
    user_id: Optional[str] = None,
    query_text: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    radius_km: Optional[float] = None,
    category_id: Optional[str] = None,
    listing_type: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    result_count: int = 0,
) -> None:
    """Write a SearchLog row off the request thread.

    The worker opens its own SessionLocal so FastAPI's request DB session
    never escapes the route handler.
    """
    from PE.weespas.services.analytics_service import log_search  # local import — heavy module
    db = SessionLocal()
    try:
        log_search(
            db,
            session_id=session_id,
            user_id=user_id,
            query_text=query_text,
            latitude=latitude,
            longitude=longitude,
            radius_km=radius_km,
            category_id=category_id,
            listing_type=listing_type,
            min_price=min_price,
            max_price=max_price,
            result_count=result_count,
        )
    finally:
        db.close()


# =====================================================================
# Phase 1.3 — Property-view bump
# =====================================================================

@celery_app.task(
    name="analytics.record_property_view",
    ignore_result=True,
    acks_late=False,
)
def record_property_view(
    property_id: str,
    user_id: Optional[str],
    session_id: Optional[str],
    ts_iso: str,
) -> None:
    """Increment view_count + insert a PropertyViewEvent off the read path.

    Idempotent on (property_id, session_id, day): a per-day Redis SETNX
    short-circuits duplicates from a user opening the same detail page
    multiple times — view counts no longer inflate under hot-reload or
    React StrictMode double-mounts.
    """
    # Bucket per UTC-day; first visit of the day counts, refresh same day doesn't.
    day = ts_iso[:10] if len(ts_iso) >= 10 else "unk"
    dedupe_key = f"view:{property_id}:{session_id or 'anon'}:{day}"
    if session_id and not redis_setnx_lock(dedupe_key, 86_400):
        return

    from PE.weespas.models.property import Property
    from PE.weespas.models.analytics import PropertyViewEvent

    db = SessionLocal()
    try:
        prop = db.query(Property).filter(Property.id == property_id).first()
        if prop is None:
            return
        prop.view_count = (prop.view_count or 0) + 1
        try:
            ts = datetime.fromisoformat(ts_iso)
        except ValueError:
            ts = datetime.now(timezone.utc)
        db.add(PropertyViewEvent(
            property_id=property_id,
            user_id=user_id,
            session_id=session_id,
            viewed_at=ts,
        ))
        db.commit()
    except Exception as exc:
        logger.warning("record_property_view(%s) failed: %s", property_id, exc)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()


# =====================================================================
# Phase 3 — Dashboard pre-aggregation
# =====================================================================

@celery_app.task(name="analytics.aggregate_summary")
def aggregate_summary(since: str = "30d") -> dict:
    from PE.weespas.services.analytics_service import aggregate_summary as _agg
    db = SessionLocal()
    try:
        payload = _agg(db, since)
        _store(k_summary(since), payload, ttl=3600)
        return payload
    finally:
        db.close()


@celery_app.task(name="analytics.aggregate_categories")
def aggregate_categories(since: str = "30d") -> list[dict]:
    from PE.weespas.services.analytics_service import aggregate_categories as _agg
    db = SessionLocal()
    try:
        payload = _agg(db, since)
        _store(k_categories(since), payload, ttl=3600)
        return payload
    finally:
        db.close()


@celery_app.task(name="analytics.aggregate_prices")
def aggregate_prices(since: str = "30d") -> dict:
    """Recompute price histogram for every listing_type variant the API exposes.

    The frontend can request `listing_type=rent`, `=sale`, or omit it; we
    pre-compute all three so the read path is a pure Redis GET. Each variant
    is its own key under `analytics:prices:{since}:{type}`.
    """
    from PE.weespas.services.analytics_service import aggregate_prices as _agg
    db = SessionLocal()
    try:
        results = {}
        for lt in (None, "rent", "sale"):
            payload = _agg(db, since=since, listing_type=lt)
            _store(k_prices(since, lt), payload, ttl=3600)
            results[lt or "all"] = "ok"
        return results
    finally:
        db.close()


@celery_app.task(name="analytics.aggregate_heatmaps")
def aggregate_heatmaps(since: str = "30d") -> dict:
    """Global heatmaps for both access (where users come from) and interest
    (where they're looking). Drill-down county variants are computed on-miss
    only — pre-computing every county would be wasteful."""
    from PE.weespas.services.analytics_service import (
        aggregate_access_heatmap as _access,
        aggregate_interest_heatmap as _interest,
    )
    db = SessionLocal()
    try:
        access = _access(db, since=since, county=None)
        interest = _interest(db, since=since, county=None)
        _store(k_heatmap_access(since, None), access, ttl=3600)
        _store(k_heatmap_interest(since, None), interest, ttl=3600)
        return {"access": "ok", "interest": "ok"}
    finally:
        db.close()


@celery_app.task(name="analytics.compute_engagement")
def compute_engagement(since: str = "30d") -> dict:
    from PE.weespas.services.analytics_service import compute_engagement as _agg
    db = SessionLocal()
    try:
        payload = _agg(db, since)
        # Engagement is daily-cadence + heavy SQL — TTL matches the cadence.
        _store(k_engagement(since), payload, ttl=86_400)
        return payload
    finally:
        db.close()


@celery_app.task(name="analytics.compute_agent_rank")
def compute_agent_rank(since: str = "30d") -> Any:
    """Platform-wide leaderboard. Per-agent "me" lookups slice this in the read path."""
    from PE.weespas.services.agent_analytics_service import compute_agent_rank as _agg
    db = SessionLocal()
    try:
        # agent_id=None → full leaderboard
        payload = _agg(db, agent_id=None, since=since)
        _store(k_agent_rank(since), payload, ttl=3600)
        return payload
    finally:
        db.close()


@celery_app.task(name="analytics.compute_agent_funnel")
def compute_agent_funnel(since: str = "30d") -> Any:
    from PE.weespas.services.agent_analytics_service import compute_agent_funnel as _agg
    db = SessionLocal()
    try:
        payload = _agg(db, agent_id=None, since=since)
        _store(k_agent_funnel(since), payload, ttl=3600)
        return payload
    finally:
        db.close()


@celery_app.task(name="analytics.compute_listing_benchmarks")
def compute_listing_benchmarks(since: str = "30d") -> dict:
    """Per-agent listing benchmarks. Iterates active agents — nightly cadence."""
    from sqlalchemy import select
    from PE.weespas.models.user import User, UserRole
    from PE.weespas.services.agent_analytics_service import compute_listing_benchmarks as _agg

    db = SessionLocal()
    try:
        # Active agents only — benchmarks for inactive accounts are wasted compute.
        rows = (
            db.query(User.agent_id)
              .filter(User.agent_id.isnot(None), User.is_active.is_(True))
              .distinct()
              .all()
        )
        count = 0
        for (agent_id,) in rows:
            try:
                payload = _agg(db, agent_id=agent_id, since=since)
                _store(k_listing_benchmarks(agent_id, since), payload, ttl=86_400)
                count += 1
            except Exception as exc:
                logger.warning("benchmarks(%s) failed: %s", agent_id, exc)
        return {"agents_processed": count}
    finally:
        db.close()


@celery_app.task(name="analytics.refresh_agent_prop_counts")
def refresh_agent_prop_counts() -> int:
    """Recompute (agent_id → active property count) into a Redis HASH.

    Replaces the per-request GROUP BY on /agents/* endpoints. Read path
    becomes an O(1) HGET.
    """
    from sqlalchemy import func
    from PE.weespas.models.property import Property
    db = SessionLocal()
    try:
        rows = (
            db.query(Property.agent_id, func.count(Property.id))
              .filter(Property.is_active.is_(True))
              .group_by(Property.agent_id)
              .all()
        )
        # Atomic-ish: write into a temp key then RENAME so readers never see a
        # half-built hash. (Redis RENAME is atomic.)
        tmp = f"{K_AGENT_PROP_COUNTS}:tmp"
        try:
            pipe = redis_client.pipeline()
            pipe.delete(tmp)
            if rows:
                mapping = {str(agent_id): str(cnt) for agent_id, cnt in rows if agent_id}
                if mapping:
                    pipe.hset(tmp, mapping=mapping)
            pipe.rename(tmp, K_AGENT_PROP_COUNTS)
            pipe.execute()
        except Exception as exc:
            logger.warning("refresh_agent_prop_counts redis write failed: %s", exc)
        return len(rows)
    finally:
        db.close()


# Staff-eligibility metrics — gating the "Become Staff" application flow.
#
# Three thresholds combined: (1) the user must have been linked to an
# agent profile (i.e. `users.agent_id IS NOT NULL`) for ≥ 90 days,
# (2) ≥ 10 active listings, (3) ≥ 500 cumulative views across those
# listings. A single GROUP BY scan computes all three for every agent
# in one round-trip — at 1M agents this is ~200 ms off the request path,
# fired every 15 minutes by Beat. Read path becomes one HGET.
#
# We key by `users.agent_id` (== agents.id) and not by `users.id` so the
# read path (`routers/role_applications.py`) can resolve eligibility
# directly from the JWT-decoded `current_user.agent_id` without a User
# row touch.
_STAFF_ELIGIBILITY_MIN_DAYS = 90
_STAFF_ELIGIBILITY_MIN_LISTINGS = 10
_STAFF_ELIGIBILITY_MIN_VIEWS = 500


@celery_app.task(name="analytics.refresh_staff_eligibility")
def refresh_staff_eligibility() -> int:
    """Recompute (agent_id → packed eligibility metrics) into a Redis HASH.

    Output value format per HSET field: `"<listings>|<views>|<days>|<eligible>"`
    where `<eligible>` is `"1"` or `"0"`. Parse with `split("|", 3)`.

    Atomic write via temp-key + RENAME so concurrent readers never see
    a half-built hash — same idiom as `refresh_agent_prop_counts`.
    """
    from sqlalchemy import text  # local import — keeps module import cheap
    db = SessionLocal()
    try:
        # Single CTE-free GROUP BY scan. Why raw SQL instead of an ORM
        # query: the FILTER-aggregate gives us listings AND views in one
        # pass; ORM's `func.count(...).filter(...)` works but the raw SQL
        # is one line shorter and reads identically to a DBA. The query
        # planner already uses `properties.agent_id` index (declared in
        # models/property.py:147) — verified with EXPLAIN ANALYZE.
        rows = db.execute(text(
            """
            SELECT
              u.agent_id,
              EXTRACT(EPOCH FROM (NOW() - u.created_at))::bigint / 86400 AS days_old,
              COUNT(p.id) FILTER (WHERE p.is_active)                       AS listings,
              COALESCE(SUM(p.view_count) FILTER (WHERE p.is_active), 0)    AS views
            FROM users u
            LEFT JOIN properties p ON p.agent_id = u.agent_id
            WHERE u.agent_id IS NOT NULL
            GROUP BY u.agent_id, u.created_at
            """
        )).all()

        tmp = f"{K_STAFF_ELIGIBILITY}:tmp"
        mapping: dict[str, str] = {}
        for agent_id, days, listings, views in rows:
            if not agent_id:
                continue
            days_i = int(days or 0)
            listings_i = int(listings or 0)
            views_i = int(views or 0)
            eligible = (
                days_i >= _STAFF_ELIGIBILITY_MIN_DAYS
                and listings_i >= _STAFF_ELIGIBILITY_MIN_LISTINGS
                and views_i >= _STAFF_ELIGIBILITY_MIN_VIEWS
            )
            mapping[str(agent_id)] = f"{listings_i}|{views_i}|{days_i}|{1 if eligible else 0}"

        try:
            pipe = redis_client.pipeline()
            pipe.delete(tmp)
            if mapping:
                pipe.hset(tmp, mapping=mapping)
                pipe.rename(tmp, K_STAFF_ELIGIBILITY)
            else:
                # No agents yet — wipe the live HASH so stale data doesn't
                # outlive the population.
                pipe.delete(K_STAFF_ELIGIBILITY)
            pipe.execute()
        except Exception as exc:
            logger.warning("refresh_staff_eligibility redis write failed: %s", exc)
        return len(mapping)
    finally:
        db.close()


def parse_staff_eligibility(packed: str | bytes | None) -> dict | None:
    """Decode the packed eligibility string written by refresh_staff_eligibility.

    Lives here (next to the writer) so the format stays in lock-step with
    the producer. Returns None on malformed input — callers fall back to
    a fresh DB compute or treat the agent as ineligible. Never raises.
    """
    if packed is None:
        return None
    if isinstance(packed, bytes):
        packed = packed.decode("utf-8", errors="ignore")
    parts = packed.split("|", 3)
    if len(parts) != 4:
        return None
    try:
        listings, views, days, eligible = (int(p) for p in parts)
    except ValueError:
        return None
    return {
        "listings": listings,
        "views": views,
        "days": days,
        "eligible": bool(eligible),
        "min_listings": _STAFF_ELIGIBILITY_MIN_LISTINGS,
        "min_views": _STAFF_ELIGIBILITY_MIN_VIEWS,
        "min_days": _STAFF_ELIGIBILITY_MIN_DAYS,
    }
