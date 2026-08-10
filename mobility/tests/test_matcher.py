"""Ride matcher: nearest-first, eligibility + freshness + denylist filters, privacy, radius clamp."""
import time

import httpx
import pytest

from PE.mobility.main import app
from PE.mobility.services import denylist, geo, matcher

_TRANSPORT = httpx.ASGITransport(app=app)


async def _client():
    return httpx.AsyncClient(transport=_TRANSPORT, base_url="http://test")


# A pickup point + two drivers near it (Nairobi CBD).
_PICKUP = (-1.2870, 36.8172)


async def _shift(sub: str, lat: float, lng: float, *, eligible: bool = True):
    """Put a driver on shift directly via the service (bypasses the HTTP layer)."""
    await geo.record_ping(sub, lat, lng, eligible=eligible)


async def test_matches_nearest_first_and_notifies(mint):
    await _shift("d-near", -1.2868, 36.8172)
    await _shift("d-far", -1.2895, 36.8172)
    res = await matcher.match_and_dispatch("rider-1", "Ada", *_PICKUP, 3000.0)
    assert res["drivers_matched"] == 2
    assert res["drivers_notified"] == 2
    assert res["ride_id"]


async def test_ineligible_driver_not_dispatched():
    await _shift("d-elig", -1.2868, 36.8172, eligible=True)
    await _shift("d-inelig", -1.2869, 36.8172, eligible=False)
    res = await matcher.match_and_dispatch("rider-1", "Ada", *_PICKUP, 3000.0)
    assert res["drivers_matched"] == 1  # only the eligible one


async def test_denied_driver_excluded():
    await _shift("d-ok", -1.2868, 36.8172)
    await _shift("d-banned", -1.2869, 36.8172)
    await denylist.deny("d-banned")
    res = await matcher.match_and_dispatch("rider-1", "Ada", *_PICKUP, 3000.0)
    assert res["drivers_matched"] == 1


async def test_stale_driver_excluded(monkeypatch):
    """A driver last seen beyond driver_stale_seconds is off-shift and not dispatched."""
    from PE.mobility.core.config import settings
    # Record a driver, then move its seen-time far into the past.
    await _shift("d-stale", -1.2868, 36.8172)
    old = time.time() - (settings.driver_stale_seconds + 60)
    await geo.get_client().zadd("mobility:drivers:seen", {"d-stale": old})
    res = await matcher.match_and_dispatch("rider-1", "Ada", *_PICKUP, 3000.0)
    assert res["drivers_matched"] == 0


async def test_far_driver_beyond_radius_excluded():
    await _shift("d-near", -1.2868, 36.8172)
    await _shift("d-veryfar", -1.70, 37.20)  # ~50 km away
    res = await matcher.match_and_dispatch("rider-1", "Ada", *_PICKUP, 3000.0)
    assert res["drivers_matched"] == 1


async def test_dispatched_event_never_leaks_rider_sub():
    """The driver event carries the rider DISPLAY name only, never the rider's private sub."""
    await _shift("d-1", -1.2868, 36.8172)
    # Subscribe to the driver's channel and capture the published frame.
    client = matcher.get_client()
    pubsub = client.pubsub()
    await pubsub.subscribe(matcher._ride_channel("d-1"))
    await matcher.match_and_dispatch("rider-secret-sub", "Ada", *_PICKUP, 3000.0)
    # Read the published message (skip the subscribe confirmation).
    msg = None
    for _ in range(5):
        m = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
        if m and m.get("type") == "message":
            msg = m
            break
    await pubsub.aclose()
    assert msg is not None, "driver never received the ride_request"
    data = msg["data"]
    assert "rider-secret-sub" not in data
    assert "Ada" in data
    assert "ride_request" in data
