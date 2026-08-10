"""Chunk 0 — generic SSE + Redis Pub/Sub rail (services/event_bus.py).

The bus is genuinely async + Redis-backed, so these tests exercise it over a
LIVE Redis (Pub/Sub can't be faithfully faked here). When Redis is absent they
SKIP rather than fail — same posture as the shops-on-map rate-limit tests, which
let check_rate_limit fail-open without a Redis. Each test drives the async API
via asyncio.run (no pytest-asyncio in this env) and uses a unique channel per run
so parallel/repeat runs never cross-talk.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
import redis.asyncio as aioredis

from PE.weespas.core.config import settings
from PE.weespas.services import event_bus


def _redis_up() -> bool:
    async def _ping() -> bool:
        c = aioredis.Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            return bool(await c.ping())
        finally:
            await c.aclose()

    try:
        return asyncio.run(_ping())
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _redis_up(), reason="live Redis required for Pub/Sub bus")


def _chan() -> str:
    return f"test-bus:{uuid.uuid4()}"


def _run(coro_fn):
    """Drive one test's async body on a fresh loop, then close the module-level
    async client ON THAT SAME loop before it shuts down. asyncio.run per test means
    the client's connections are bound to this loop; closing it from a later loop
    would raise 'Event loop is closed'. So teardown happens inside the same run."""

    async def wrapper():
        try:
            await coro_fn()
        finally:
            await event_bus.aclose()

    asyncio.run(wrapper())


def test_publish_subscribe_round_trip():
    """An event published after the subscriber is listening arrives as one SSE data frame."""
    channel = _chan()

    async def run():
        async def never():
            return False

        gen = event_bus.sse_subscribe(
            channel, is_disconnected=never, heartbeat_s=0, max_connection_s=0
        )
        # Drain the opening ": connected" comment so the subscription is live.
        first = await gen.__anext__()
        assert first == ": connected\n\n"
        # Give Redis a beat to register the SUBSCRIBE before publishing.
        await asyncio.sleep(0.1)
        n = await event_bus.publish(channel, {"hello": "world", "n": 1})
        assert n == 1  # exactly our one subscriber received it
        frame = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        assert frame == 'data: {"hello":"world","n":1}\n\n'
        await gen.aclose()

    _run(run)


def test_heartbeat_on_idle():
    """With no events, an idle stream emits a ``:keep-alive`` comment each heartbeat tick."""
    channel = _chan()

    async def run():
        async def never():
            return False

        gen = event_bus.sse_subscribe(
            channel, is_disconnected=never, heartbeat_s=0.2, max_connection_s=0
        )
        assert await gen.__anext__() == ": connected\n\n"
        beat = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        assert beat == ": keep-alive\n\n"
        await gen.aclose()

    _run(run)


def test_disconnect_ends_stream():
    """When the client reports disconnected, the generator terminates (StopAsyncIteration)
    and the Redis subscription is torn down — no leaked subscriber."""
    channel = _chan()

    async def run():
        async def disconnected():
            return True  # already gone

        gen = event_bus.sse_subscribe(
            channel, is_disconnected=disconnected, heartbeat_s=0, max_connection_s=0
        )
        assert await gen.__anext__() == ": connected\n\n"
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(gen.__anext__(), timeout=2.0)

    _run(run)


def test_max_connection_cap_ends_stream():
    """The connection cap closes an otherwise-idle stream after max_connection_s."""
    channel = _chan()

    async def run():
        async def never():
            return False

        gen = event_bus.sse_subscribe(
            channel, is_disconnected=never, heartbeat_s=0, max_connection_s=0.2
        )
        assert await gen.__anext__() == ": connected\n\n"
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(gen.__anext__(), timeout=2.0)

    _run(run)


def test_multiline_data_is_single_event():
    """A payload with an embedded newline is emitted as one SSE event (two data: lines,
    one terminating blank line) — never split into two events."""

    frame = event_bus._sse_frame("line1\nline2")
    assert frame == "data: line1\ndata: line2\n\n"
