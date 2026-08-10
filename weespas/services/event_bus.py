"""Generic SSE + Redis Pub/Sub realtime rail (work_flow.md §5).

"POST to act, SSE to hear." A publisher writes a JSON event to a per-recipient
channel; a subscriber holds one long-lived SSE connection and receives only the
events on its OWN channel. Per-recipient channels (never per-AOI/per-topic
broadcast) keep the fan-out horizontally scalable and leak nothing about who
else is connected.

This module is deliberately domain-agnostic — it knows about channels, JSON
events, heartbeats and disconnects, nothing about shops or footprints. §8.1b
pair-radiate is the first consumer (``contact-events:<user_id>``); mobility
reuses it verbatim.

Redis Pub/Sub is instance-global (not DB-index-scoped), so this async client
shares the same Redis instance as the sync cache client (services/cache.py) and
the bus keeps working across services once mobility joins on its own DB index.
"""
from __future__ import annotations

import json
import logging
import time
from typing import AsyncIterator, Awaitable, Callable, Optional

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from PE.weespas.core.config import settings

logger = logging.getLogger(__name__)

# Lazy module-level async client — ONE connection pool reused across requests,
# mirroring services/cache.py on the sync side. Created on first use so merely
# importing this module never opens a socket (test collection and every non-SSE
# request path stay free of Redis).
_client: Optional[aioredis.Redis] = None


def get_client() -> aioredis.Redis:
    """The shared async Redis client (lazy singleton)."""
    global _client
    if _client is None:
        _client = aioredis.Redis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def aclose() -> None:
    """Close the async connection pool on app shutdown. Idempotent — safe to call
    when the client was never created (no SSE traffic this process)."""
    global _client
    if _client is not None:
        client, _client = _client, None
        await client.aclose()


async def publish(channel: str, event: dict) -> int:
    """Publish one JSON event to ``channel``.

    Returns the number of subscribers that received it — 0 is entirely normal
    (the recipient simply may not be watching the map right now). Raises
    ``RedisError``; hot-path callers MUST catch and degrade (a publish failure
    must never fail the user action that triggered it)."""
    payload = json.dumps(event, separators=(",", ":"))
    return await get_client().publish(channel, payload)


def publish_sync(channel: str, event: dict) -> int:
    """Synchronous counterpart to :func:`publish`, for publishers running in a plain
    ``def`` request handler (FastAPI runs those in a threadpool, so blocking Redis I/O
    is fine and an ``async`` endpoint — which would block the loop on its DB/HTTP work —
    is the wrong shape). Reuses the shared SYNC cache pool; Pub/Sub is instance-global so
    a sync publish here is received by the async ``sse_subscribe`` on the same Redis.

    Same JSON encoding and return contract as :func:`publish`. Raises ``RedisError`` — the
    hot-path caller MUST catch and degrade (a publish failure must never fail the user
    action that triggered it)."""
    from PE.weespas.services.cache import redis_client  # shared sync pool; local import avoids cycle

    payload = json.dumps(event, separators=(",", ":"))
    return redis_client.publish(channel, payload)


def _sse_frame(data: str) -> str:
    """Format one SSE ``data:`` event. Our payloads are single-line compact JSON,
    but a stray newline in ``data`` would otherwise inject a second frame — so we
    prefix every line defensively and terminate the event with a blank line."""
    return "".join(f"data: {line}\n" for line in data.split("\n")) + "\n"


async def sse_subscribe(
    channel: str,
    *,
    is_disconnected: Callable[[], Awaitable[bool]],
    heartbeat_s: float,
    max_connection_s: float,
) -> AsyncIterator[str]:
    """Yield SSE-formatted strings for every event published to ``channel``, plus a
    ``:keep-alive`` comment every ``heartbeat_s`` idle seconds.

    Terminates cleanly when the client disconnects (``is_disconnected`` — pass
    ``Request.is_disconnected``) or when ``max_connection_s`` elapses (bounds
    server-side subscriber state; the client transparently reconnects). The Redis
    subscription is torn down on ANY exit — normal return, cancellation, or error
    — so a dropped connection never leaks a subscriber slot.

    ``heartbeat_s <= 0`` disables heartbeats; ``max_connection_s <= 0`` disables
    the connection cap (both used by tests)."""
    client = get_client()
    pubsub = client.pubsub()
    await pubsub.subscribe(channel)
    deadline = time.monotonic() + max_connection_s if max_connection_s > 0 else None
    # Base poll cadence bounds how quickly an idle stream wakes to check for a
    # disconnect / the deadline. Default to 20s when heartbeats are off so we don't
    # busy-loop, but never poll PAST the connection deadline (else the cap wouldn't
    # bite until a full poll later).
    base_poll = heartbeat_s if heartbeat_s > 0 else 20.0
    try:
        # An opening comment flushes response headers immediately so the client's
        # stream reader unblocks and the connection is confirmed established.
        yield ": connected\n\n"
        while True:
            if await is_disconnected():
                return
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                return
            poll = base_poll
            if deadline is not None:
                poll = min(poll, max(0.01, deadline - now))
            try:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=poll)
            except RedisError:
                # A Redis blip ends this stream; the client reconnects and re-subscribes.
                logger.warning("event_bus: pubsub read error on %s", channel, exc_info=True)
                return
            if msg is None:
                if heartbeat_s > 0:
                    yield ": keep-alive\n\n"
                continue
            data = msg.get("data")
            if isinstance(data, str):
                yield _sse_frame(data)
    finally:
        # aclose() unsubscribes and returns the connection to the pool. Guard it:
        # teardown must never raise out of the generator's finally.
        try:
            await pubsub.aclose()
        except RedisError:
            logger.debug("event_bus: pubsub close error on %s", channel, exc_info=True)
