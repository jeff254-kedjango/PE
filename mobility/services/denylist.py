"""Revocation denylist for the dispatch action (architecture §4, decision #4).

Stateless RS256 tokens cannot be revoked before they expire — fine for reads, NOT fine for a
banned/fraud actor. So the dispatch ACTION (a rider requesting a ride) checks an O(1) Redis set
membership (``SISMEMBER mobility:denylist {sub}``) before matching. A banned actor is stopped NOW,
not at token TTL.

**Fail CLOSED.** If Redis is unreachable on the dispatch action we DENY (the caller maps a raise to
503). A dispatch path must never let a banned actor through just because the store blinked. The
high-frequency ping uplink does NOT call this (a banned driver's pings are harmless — the matcher
excludes denied drivers from the fan-out, so a ban still takes effect without a Redis round-trip on
every 3-second ping).

Async (mobility is async end-to-end) and reuses the ONE shared async Redis pool from
services.event_bus — no second client. Mirrors the commerce denylist contract (own key namespace).
"""
from __future__ import annotations

import logging

from redis.exceptions import RedisError

from PE.mobility.services.event_bus import get_client

logger = logging.getLogger(__name__)

_DENYLIST_KEY = "mobility:denylist"


class DenylistUnavailable(RuntimeError):
    """Raised when the denylist cannot be consulted. The dispatch action maps this to 503 — fail
    closed: refuse the action rather than risk admitting a banned actor."""


async def is_denied(sub: str) -> bool:
    """True iff ``sub`` is on the dispatch denylist. Raises ``DenylistUnavailable`` if Redis can't
    be reached — the caller MUST treat that as 'refuse the action' (fail closed)."""
    try:
        return bool(await get_client().sismember(_DENYLIST_KEY, sub))
    except RedisError as exc:
        logger.error("mobility denylist check failed (failing closed): %s", exc)
        raise DenylistUnavailable(str(exc)) from exc


async def deny(sub: str) -> None:
    """Add ``sub`` to the denylist (ops/abuse action). Exposed for tests + a future admin tool."""
    await get_client().sadd(_DENYLIST_KEY, sub)


async def undeny(sub: str) -> None:
    """Remove ``sub`` from the denylist."""
    await get_client().srem(_DENYLIST_KEY, sub)


def denylist_key() -> str:
    """The Redis key, exposed for the matcher's batched pipeline membership check."""
    return _DENYLIST_KEY
