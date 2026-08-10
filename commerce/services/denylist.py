"""Revocation denylist for money/settlement actions (architecture §2, S5).

Stateless RS256 tokens cannot be revoked before they expire — fine for reads/identity, NOT
fine for a banned or fraud actor mid-transaction. So every MONEY action checks an O(1) Redis
set membership (``SISMEMBER commerce:denylist {sub}``) before proceeding. A banned actor is
stopped NOW, not at token TTL.

**Fail CLOSED.** Unlike the best-effort feed, if Redis is unreachable on a money action we
DENY (the caller treats a None/raise as denied → 503/403). A payments path must never let an
actor through just because the denylist store blinked. Reads never call this.

Lazy, module-level client so a missing Redis at import never breaks app startup (only money
actions, which are rare relative to reads, touch it).
"""
from __future__ import annotations

import logging

import redis

from PE.commerce.core.config import settings

logger = logging.getLogger(__name__)

_DENYLIST_KEY = "commerce:denylist"
_client: redis.Redis | None = None


def _get_client() -> redis.Redis:
    """Lazily build a Redis client (decode_responses so members compare as str). Short timeouts
    so a hung Redis fails fast into the fail-closed path rather than stalling a request."""
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )
    return _client


class DenylistUnavailable(RuntimeError):
    """Raised when the denylist cannot be consulted. Money endpoints translate this to 503 —
    fail closed: we refuse the action rather than risk admitting a banned actor."""


def is_denied(sub: str) -> bool:
    """True iff ``sub`` is on the money-action denylist. Raises ``DenylistUnavailable`` if Redis
    can't be reached — the caller MUST treat that as 'refuse the action' (fail closed)."""
    try:
        return bool(_get_client().sismember(_DENYLIST_KEY, sub))
    except redis.RedisError as exc:
        logger.error("denylist check failed (failing closed): %s", exc)
        raise DenylistUnavailable(str(exc)) from exc


def deny(sub: str) -> None:
    """Add ``sub`` to the denylist (ops/abuse action). Exposed for tests + a future admin tool;
    no public endpoint this increment (population belongs to the abuse pipeline)."""
    _get_client().sadd(_DENYLIST_KEY, sub)


def undeny(sub: str) -> None:
    """Remove ``sub`` from the denylist."""
    _get_client().srem(_DENYLIST_KEY, sub)
