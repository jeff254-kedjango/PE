"""Read-through cache for the trending slate (§8) — Redis, keyed per locality bucket, TTL = dwell.

The slate is a pure function of (bucket, time window), so every viewer in a locality wants the
SAME payload. Caching it per bucket turns N viewers into ONE compute per dwell window (O(1) per
extra viewer). Stored as the already-serialised JSON of the TrendingSlate response.

**Fail OPEN** (the deliberate opposite of the money denylist, which fails closed): the rail is a
best-effort discovery surface, NOT a security boundary. If Redis is unreachable, ``get`` returns
None (cache miss) and the router recomputes directly from the DB, and ``set`` silently drops — a
blinking cache must degrade to "slightly more DB load", never to an error in the buyer's face.

Lazy module-level client (mirrors services.denylist) with short timeouts so a hung Redis fails
fast into the recompute path rather than stalling the request.
"""
from __future__ import annotations

import logging

import redis

from PE.commerce.core.config import settings

logger = logging.getLogger(__name__)

_KEY_PREFIX = "commerce:trending:"
_client: "redis.Redis | None" = None


def _get_client() -> "redis.Redis":
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )
    return _client


def _key(bucket: str, kind: str | None) -> str:
    # The slate is identical for a (bucket) — kind is reserved for a future per-kind rail; included
    # in the key now so adding it later can't collide with already-cached entries.
    return f"{_KEY_PREFIX}{kind or 'all'}:{bucket}"


def get(bucket: str, kind: str | None = None) -> str | None:
    """The cached slate JSON for a bucket, or None on miss / Redis unavailable (fail open)."""
    try:
        return _get_client().get(_key(bucket, kind))
    except redis.RedisError as exc:
        logger.warning("trending cache get failed (fail open, recomputing): %s", exc)
        return None


def set(bucket: str, payload: str, ttl_seconds: int, kind: str | None = None) -> None:
    """Cache the slate JSON for ``ttl_seconds`` (= the slate's dwell, so the entry expires exactly
    as the window flips). Silently no-ops if Redis is unavailable (fail open)."""
    try:
        # ttl must be ≥ 1 for SETEX; the dwell floor (min_dwell ≥ 1) already guarantees this, but
        # guard anyway so a misconfigured 0 can't raise.
        _get_client().setex(_key(bucket, kind), max(1, ttl_seconds), payload)
    except redis.RedisError as exc:
        logger.warning("trending cache set failed (fail open): %s", exc)
