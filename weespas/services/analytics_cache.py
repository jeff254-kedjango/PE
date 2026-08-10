"""Read-path SWR (stale-while-revalidate) helper for analytics endpoints.

Each cache entry is the envelope written by `services/analytics_tasks._store`:

    {"computed_at": iso8601, "ttl": seconds, "payload": <answer>}

`read_swr` returns the payload immediately when present, and — if the entry
is past its refresh ratio (defaults to half-life) — schedules a Beat-task
refresh in the background. The user always sees a hot response; the next
visitor sees the fresher number.

On cache miss, the caller falls back to live compute AND schedules a warm
so the next request is a Redis hit.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from PE.weespas.core.config import settings
from PE.weespas.services.cache import redis_client
from PE.weespas.services.celery_helpers import safe_delay

logger = logging.getLogger(__name__)


def read_swr(
    key: str,
    refresh_task: Any,
    *refresh_args: Any,
    refresh_ratio: float | None = None,
) -> Optional[Any]:
    """Return cached payload (and schedule refresh if past half-life), or None.

    `refresh_task` is a Celery task; `refresh_args` are forwarded if a
    background refresh is scheduled.
    """
    try:
        blob = redis_client.get(key)
    except Exception as exc:
        logger.debug("redis get(%s) failed: %s", key, exc)
        return None
    if not blob:
        return None

    try:
        envelope = json.loads(blob)
        payload = envelope.get("payload")
        computed_at = envelope.get("computed_at")
        ttl = float(envelope.get("ttl") or 0)
    except Exception as exc:
        logger.debug("redis envelope parse(%s) failed: %s", key, exc)
        return None

    # Schedule a background refresh if the entry is past its refresh ratio.
    if computed_at and ttl > 0:
        try:
            ratio = refresh_ratio if refresh_ratio is not None else settings.analytics_swr_refresh_ratio
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(computed_at)).total_seconds()
            if age > ttl * ratio:
                safe_delay(refresh_task, *refresh_args)
        except Exception:
            # Never let a refresh-scheduling error break the read.
            pass

    return payload


def warm_on_miss(refresh_task: Any, *refresh_args: Any) -> None:
    """Schedule a warm for the requested key. Cheap enough to fire whenever
    the read path falls through to live compute — the next hit is then free."""
    safe_delay(refresh_task, *refresh_args)
