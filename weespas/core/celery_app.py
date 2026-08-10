"""Celery application — production-hardened.

Queues are split by workload class so a slow analytics job can never delay
an OTP. The beat_schedule is populated in two passes (Phase 3 + Phase 4 in
Celery_Audit.md). Until then, the schedule is empty — Beat starts cleanly
but does nothing.
"""
from __future__ import annotations

import os

from celery import Celery
from celery.schedules import crontab
from datetime import timedelta

from PE.weespas.core.config import settings

celery_app = Celery(
    "weespas_tasks",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    # Fully-qualified module paths. These MUST match how the rest of the codebase
    # imports these modules (`from PE.weespas.services...`). They were previously
    # listed as bare "services.*", which made Python load each file TWICE under two
    # distinct module objects — one per name — so module-level state was duplicated
    # (e.g. property_tasks.POPULAR_CITIES existed as two separate lists) and any task
    # without an explicit `name=` registered under whichever identity imported it.
    include=[
        # Phase 0 — existing
        "PE.weespas.services.image_processing",
        "PE.weespas.services.personalization_tasks",
        # Phase 1
        "PE.weespas.services.auth_tasks",
        "PE.weespas.services.analytics_tasks",
        # Phase 2
        "PE.weespas.services.session_tasks",
        # Phase 4
        "PE.weespas.services.property_tasks",
        # Billing — M-Pesa reconciliation sweep
        "PE.weespas.services.billing_tasks",
        # §8 — metering write-offload + company-detection scoring beat job
        "PE.weespas.services.metering_service",
        "PE.weespas.services.policy_tasks",
        # InSAR footprint-verification of a listing on upload (→ status + inbox)
        "PE.weespas.services.insar_verify_tasks",
    ],
)

celery_app.conf.update(
    # Serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Africa/Nairobi",
    enable_utc=True,
    # Reliability
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Time limits — a hung task (a wedged feed pull, a stuck media encode) must
    # never pin a worker forever. Weespas tasks are short (OTP, analytics, feeds,
    # media), so the soft limit raises SoftTimeLimitExceeded (catchable → cleanup)
    # well before the hard limit SIGKILLs the worker as a backstop. Env-overridable.
    task_soft_time_limit=int(os.environ.get("CELERY_TASK_SOFT_TIME_LIMIT", "120")),
    task_time_limit=int(os.environ.get("CELERY_TASK_TIME_LIMIT", "180")),
    # Throughput — workers prefetch 4 tasks per concurrency slot. Tuned for
    # short tasks (OTP, log writes, cache invalidations) where overhead per
    # fetch matters more than fairness across queues.
    worker_prefetch_multiplier=4,
    # Queue routing — one queue per workload class so analytics never blocks
    # an OTP. Workers in scripts/run_workers.sh subscribe to a single queue.
    task_default_queue="default",
    task_routes={
        "auth.*":         {"queue": "auth"},
        "analytics.*":    {"queue": "analytics"},
        "feeds.*":        {"queue": "feeds"},
        "media.*":        {"queue": "media"},
        "session.*":      {"queue": "default"},
        # Legacy task names from the original 2 modules — keep on default
        # so existing image/video uploads continue to work unchanged.
        "process_property_image":         {"queue": "media"},
        "process_property_images_batch":  {"queue": "media"},
        "process_property_video":         {"queue": "media"},
    },
    # Beat schedule — populated by Phase 3 (analytics) + Phase 4 (feeds).
    # Until each feature flag flips on, these tasks are no-ops at the
    # router layer; Beat firing them is harmless.
    beat_schedule={
        # ---- Phase 3: analytics aggregators ----
        "summary-hourly":             {"task": "analytics.aggregate_summary",     "schedule": crontab(minute=7),  "args": ["30d"]},
        "summary-hourly-all":         {"task": "analytics.aggregate_summary",     "schedule": crontab(minute=8),  "args": ["all"]},
        "categories-hourly":          {"task": "analytics.aggregate_categories",  "schedule": crontab(minute=12), "args": ["30d"]},
        "prices-hourly":              {"task": "analytics.aggregate_prices",      "schedule": crontab(minute=17), "args": ["30d"]},
        "heatmaps-hourly":            {"task": "analytics.aggregate_heatmaps",    "schedule": crontab(minute=22), "args": ["30d"]},
        "engagement-daily":           {"task": "analytics.compute_engagement",    "schedule": crontab(hour=2, minute=15), "args": ["30d"]},
        "agent-rank-hourly":          {"task": "analytics.compute_agent_rank",    "schedule": crontab(minute=27), "args": ["30d"]},
        "agent-funnel-hourly":        {"task": "analytics.compute_agent_funnel",  "schedule": crontab(minute=32), "args": ["30d"]},
        "listing-benchmarks-nightly": {"task": "analytics.compute_listing_benchmarks", "schedule": crontab(hour=3, minute=0), "args": ["30d"]},
        "agent-prop-counts":          {"task": "analytics.refresh_agent_prop_counts",  "schedule": timedelta(minutes=5)},
        # Staff-eligibility precompute — backs the "Become Staff" gate on
        # ProfilePage. 15-min cadence: eligibility moves slowly (a 91st-day
        # crossover is only meaningful once per agent), so paying more
        # frequent SQL scans here would be pure waste. See
        # `services/analytics_tasks.py:refresh_staff_eligibility` for
        # full rationale.
        "staff-eligibility":          {"task": "analytics.refresh_staff_eligibility", "schedule": timedelta(minutes=15)},
        # ---- Phase 4: feed warmers ----
        "featured-warm":              {"task": "feeds.warm_featured",            "schedule": timedelta(minutes=10)},
        "popular-anon-feeds":         {"task": "feeds.warm_popular_anon_feeds",  "schedule": timedelta(minutes=2)},
        "trending-counts":            {"task": "feeds.warm_trending_counts",     "schedule": timedelta(minutes=5)},
        # Housekeeping: demote featured promotions whose expiry has passed (the read
        # path already hides them; this just keeps the stored flag honest). Off-:00.
        "featured-expire":            {"task": "feeds.expire_featured",          "schedule": crontab(minute="9,39")},
        # ---- Billing: reconcile STK payments whose callback was lost ----
        # No-op when billing isn't configured; cheap (bounded batch, only pending
        # intents in a 90s–1h age window). 2-min cadence keeps a paid-but-uncallback'd
        # user waiting at most ~2 min for their window.
        "billing-reconcile":          {"task": "billing.reconcile_pending",       "schedule": timedelta(minutes=2)},
        # ---- §8: company-detection — recompute usage profiles ----
        # Slow cadence: commercial-scale use is a rolling pattern, not per-request.
        # Bounded — only recomputes users with a commercial event in the window.
        "usage-profiles":             {"task": "policy.recompute_usage_profiles", "schedule": timedelta(minutes=30)},
    },
    # Result expiry — we mostly use ignore_result=True; keep what remains short.
    result_expires=3600,
)
