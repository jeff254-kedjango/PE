"""§8.1b — GET /insar/contact/stream: the pair-radiate SSE downlink (Chunk 3).

The bus mechanics (real Pub/Sub round-trip, heartbeat, disconnect cleanup) are proven against
LIVE Redis in test_event_bus.py, and the real browser↔uvicorn↔Redis round-trip is covered by the
Chunk 6 Playwright e2e. This suite asserts the ENDPOINT's job — the WIRING between the bus and the
HTTP response — deterministically, by stubbing ``event_bus.sse_subscribe``:

  * telemetry-gated — a token-less caller is refused (never opens a stream);
  * the stream subscribes to the channel derived from the VERIFIED ``sub`` (a caller can only ever
    hear their OWN events — there is no caller-supplied channel), with the configured heartbeat /
    connection-cap;
  * the response is ``text/event-stream`` with nginx buffering disabled + no-cache;
  * frames the bus yields pass through verbatim as the response body.

Stubbing (not real Redis) is deliberate here: Starlette's TestClient buffers a streaming response
in full before delivering it, so it cannot observe a live incremental round-trip anyway — that is
exactly what the e2e exists for. Here we assert the contract the endpoint owns.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from PE.weespas.main import app
from PE.weespas.core.config import settings
from PE.weespas.services import contact_service, event_bus
from PE.weespas.services.auth_service import require_insar_telemetry_token


@pytest.fixture
def sub():
    """A fresh per-test user id with the telemetry gate overridden to return it."""
    uid = f"sse-user-{uuid.uuid4()}"
    app.dependency_overrides[require_insar_telemetry_token] = lambda: uid
    yield uid
    app.dependency_overrides.pop(require_insar_telemetry_token, None)


@pytest.fixture
def captured_bus(monkeypatch):
    """Replace the real Pub/Sub bus with a fast fake that records how the endpoint called it and
    yields two canned frames. Keeps the test deterministic and Redis-free while still exercising
    the endpoint's real subscribe-call + StreamingResponse wiring."""
    seen = {}

    async def _fake(channel, *, is_disconnected, heartbeat_s, max_connection_s):
        seen["channel"] = channel
        seen["heartbeat_s"] = heartbeat_s
        seen["max_connection_s"] = max_connection_s
        yield ": connected\n\n"
        yield 'data: {"kind":"contact","shop_building_id":4242,"aoi":"huruma"}\n\n'

    monkeypatch.setattr(event_bus, "sse_subscribe", _fake)
    return seen


# ── 1. auth gate ─────────────────────────────────────────────────────────────────

def test_requires_telemetry_token():
    app.dependency_overrides.pop(require_insar_telemetry_token, None)
    client = TestClient(app)
    resp = client.get("/api/v1/insar/contact/stream")
    assert resp.status_code in (401, 403)


# ── 2. subscribes to the caller's OWN channel, with configured params ────────────

def test_subscribes_to_own_channel(sub, captured_bus):
    client = TestClient(app)
    with client.stream("GET", "/api/v1/insar/contact/stream") as r:
        assert r.status_code == 200
        list(r.iter_lines())  # drain so the generator (and our capture) runs to completion
    # The channel is derived from the verified sub — never caller-supplied — so cross-user
    # subscription is impossible by construction.
    assert captured_bus["channel"] == contact_service.channel_for(sub)
    assert captured_bus["channel"] == f"contact-events:{sub}"
    assert captured_bus["heartbeat_s"] == settings.contact_sse_heartbeat_s
    assert captured_bus["max_connection_s"] == settings.contact_sse_max_connection_s


# ── 3. response shape: event-stream + buffering-off headers ──────────────────────

def test_stream_headers(sub, captured_bus):
    client = TestClient(app)
    with client.stream("GET", "/api/v1/insar/contact/stream") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        assert r.headers.get("cache-control") == "no-cache"
        assert r.headers.get("x-accel-buffering") == "no"   # nginx must not buffer the stream
        list(r.iter_lines())


# ── 4. bus frames pass through verbatim ──────────────────────────────────────────

def test_frames_pass_through(sub, captured_bus):
    client = TestClient(app)
    with client.stream("GET", "/api/v1/insar/contact/stream") as r:
        body = "".join(r.iter_text())
    assert ": connected" in body
    assert 'data: {"kind":"contact","shop_building_id":4242,"aoi":"huruma"}' in body
