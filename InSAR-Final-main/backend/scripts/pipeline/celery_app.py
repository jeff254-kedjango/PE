"""Celery application for the InSAR build-time pipeline.

Broker + result backend are Redis, configurable via env so the same code runs
on a laptop (on-demand worker) or a small always-on host later:

    REDIS_URL                 default redis://localhost:6379/2
    INSAR_CELERY_BROKER_URL   overrides broker only
    INSAR_CELERY_RESULT_BACKEND overrides backend only

DB index 2 keeps this isolated from any Weespas Redis (which uses 0/1), so the
two products never share keyspace even on one Redis instance.

Run a worker (from backend/):
    celery -A scripts.pipeline.celery_app worker --loglevel=info --concurrency=1

Why concurrency=1 by default: the heavy stages (clip/reproject/MintPy) are each
already multi-core or disk-bound and assume sole use of the box; running two
AOIs' heavy chains at once on a laptop thrashes. Scale out only on a real host.
"""
from __future__ import annotations

import os

from celery import Celery

_DEFAULT_REDIS = os.environ.get("REDIS_URL", "redis://localhost:6379/2")
_BROKER = os.environ.get("INSAR_CELERY_BROKER_URL", _DEFAULT_REDIS)
_BACKEND = os.environ.get("INSAR_CELERY_RESULT_BACKEND", _DEFAULT_REDIS)

app = Celery("insar_pipeline", broker=_BROKER, backend=_BACKEND)

app.conf.update(
    # Pipeline tasks are long (minutes–hours) and side-effecting on disk.
    # ack late + reject-on-worker-lost so a killed worker re-queues the task
    # instead of silently dropping it; the underlying scripts are idempotent,
    # so re-running is safe (a no-op on already-computed files).
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Time limits — backstop against a wedged stage (a hung MintPy/GDAL call)
    # pinning the single worker indefinitely. Defaults are deliberately LARGE
    # (pipeline runs are minutes–hours) so a legitimate rebuild is never killed;
    # tune via env on hosts with known AOI sizes. Soft raises SoftTimeLimitExceeded
    # (catchable cleanup); hard SIGKILLs as a last resort. acks_late + idempotent
    # scripts mean a killed task re-queues and re-runs safely.
    task_soft_time_limit=int(os.environ.get("INSAR_CELERY_SOFT_TIME_LIMIT", "3000")),
    task_time_limit=int(os.environ.get("INSAR_CELERY_TIME_LIMIT", "3600")),
    # One heavy task at a time per worker (see module docstring).
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=8,        # recycle to release MintPy/GDAL RSS
    # Keep results around long enough to inspect a finished/failed run.
    result_expires=60 * 60 * 24 * 7,     # 7 days
    task_track_started=True,
    # Serialize plain JSON — task args are AOI codes / track strings, never
    # Python objects. Refuse pickle for safety.
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    task_default_queue="insar",
)

# Beat schedule (opt-in via INSAR_BEAT_ENABLED; empty otherwise). Running a
# default worker therefore never auto-fires pipeline jobs — see schedule.py.
from scripts.pipeline.schedule import build_beat_schedule  # noqa: E402

app.conf.beat_schedule = build_beat_schedule()

# Import task definitions so they register on the app when a worker starts.
from scripts.pipeline import tasks  # noqa: E402,F401
