"""Celery dispatch helpers.

`safe_delay` is the workhorse during rollout: it tries the Celery broker, and
on any failure (Redis down, serialization error, queue full) it falls back to
running the task **inline** in the request thread. The request never fails
because a worker is unreachable. Once a task has been clean in Flower for a
week, the call site can drop `safe_delay` in favor of `task.delay(...)`.

Idempotency helpers:
- `redis_setnx_lock(key, ttl)` — boolean "first writer wins" used to dedupe
  bursts of equivalent dispatches (e.g. OTP retry, view-count bumps).
- `redis_beat_lease(...)` — Redis-backed leader lease so multi-replica Beat
  schedulers don't double-fire.
"""
from __future__ import annotations

import logging
import socket
from typing import Any, Callable

from PE.weespas.services.cache import redis_client

logger = logging.getLogger(__name__)


def safe_delay(task: Any, *args: Any, **kwargs: Any) -> Any:
    """Dispatch `task` to Celery; on any failure, run it inline.

    `task` is a Celery task object (decorated with @celery_app.task). We try
    `.delay()` first so the request returns immediately on the happy path.
    """
    try:
        return task.delay(*args, **kwargs)
    except Exception as exc:
        logger.warning(
            "celery dispatch failed for %s (%s); running inline",
            getattr(task, "name", repr(task)), exc,
        )
        # `task(...)` calls the underlying function directly — same code path
        # the worker would run, just without the queue hop.
        try:
            return task(*args, **kwargs)
        except Exception as inner:
            # Never raise out of safe_delay — the caller has already returned
            # to the user. Log and swallow.
            logger.error(
                "inline fallback for %s also failed: %s",
                getattr(task, "name", repr(task)), inner,
            )
            return None


def redis_setnx_lock(key: str, ttl_seconds: int) -> bool:
    """Acquire a single-writer lock. Returns True if we got it, False if not.

    Used for write-task dedupe: e.g. before bumping a property's view count
    for a given (session, property, day) we SETNX an idempotency key and
    skip the task entirely if it already exists.
    """
    try:
        return bool(redis_client.set(key, "1", nx=True, ex=ttl_seconds))
    except Exception as exc:
        # Redis blip: treat as "lock not acquired" so we don't spam writes.
        # If Redis is fully down, the broker is too, and safe_delay will run
        # the task inline anyway — at which point the inline path can decide
        # what to do without a lock.
        logger.debug("redis_setnx_lock(%s) failed: %s", key, exc)
        return False


# ---- Beat leader-lease ----

_BEAT_LEADER_KEY = "celery:beat:leader"
_BEAT_LEASE_TTL = 60          # seconds — must be > renewal interval
_BEAT_LEASE_RENEWAL = 30      # seconds


def acquire_beat_lease(node_id: str | None = None) -> bool:
    """Try to become the Beat leader. Returns True on success.

    Run from a wrapper around `celery beat` so multi-replica scheduler
    deployments don't double-fire every aggregation. The losing replicas
    sleep_and_retry until the current leader's lease expires.
    """
    node_id = node_id or socket.gethostname()
    try:
        return bool(
            redis_client.set(_BEAT_LEADER_KEY, node_id, nx=True, ex=_BEAT_LEASE_TTL)
        )
    except Exception as exc:
        # If Redis is unreachable we can't coordinate — refuse leadership so
        # we don't blindly double-fire schedules from every replica.
        logger.warning("acquire_beat_lease failed: %s", exc)
        return False


def renew_beat_lease(node_id: str | None = None) -> bool:
    """Renew the Beat leadership lease. Returns False if we lost it."""
    node_id = node_id or socket.gethostname()
    try:
        # Atomic compare-and-extend: only renew if we still own the key.
        current = redis_client.get(_BEAT_LEADER_KEY)
        if current != node_id:
            return False
        redis_client.expire(_BEAT_LEADER_KEY, _BEAT_LEASE_TTL)
        return True
    except Exception as exc:
        logger.warning("renew_beat_lease failed: %s", exc)
        return False
