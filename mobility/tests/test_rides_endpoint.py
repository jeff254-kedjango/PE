"""Ride endpoint: auth, the rider revocation gate, and the server-side radius clamp."""
import httpx

from PE.mobility.core.config import settings
from PE.mobility.services import denylist, geo

from PE.mobility.main import app

_TRANSPORT = httpx.ASGITransport(app=app)


async def _client():
    return httpx.AsyncClient(transport=_TRANSPORT, base_url="http://test")


async def test_rides_requires_token():
    async with await _client() as c:
        r = await c.post("/api/v1/dispatch/rides", json={"lat": -1.28, "lng": 36.81})
    assert r.status_code == 401


async def test_denied_rider_refused_403(mint, auth):
    await denylist.deny("rider-banned")
    tok = mint("rider-banned", ["mobility_dispatch"])
    async with await _client() as c:
        r = await c.post("/api/v1/dispatch/rides", json={"lat": -1.28, "lng": 36.81}, headers=auth(tok))
    assert r.status_code == 403


async def test_ride_dispatches_to_eligible_driver(mint, auth):
    await geo.record_ping("d-1", -1.2868, 36.8172, eligible=True)
    tok = mint("rider-1", ["mobility_dispatch"])
    async with await _client() as c:
        r = await c.post("/api/v1/dispatch/rides", json={"lat": -1.2870, "lng": 36.8172}, headers=auth(tok))
    assert r.status_code == 200
    body = r.json()
    assert body["drivers_matched"] == 1
    assert body["drivers_notified"] == 1


async def test_radius_clamped_to_server_max(mint, auth):
    """A client-supplied radius far beyond the cap can't widen the search past ride_max_radius_m.
    A driver just OUTSIDE the max but INSIDE the requested radius must NOT match."""
    # Place a driver ~1.05x the max radius away (still tiny in absolute terms → build from the cap).
    # 0.02 deg lat ~= 2.2 km; use the cap to derive a point safely beyond it.
    beyond = settings.ride_max_radius_m + 2000.0
    # crude north offset in degrees (~111 km per degree lat)
    dlat = beyond / 111_000.0
    await geo.record_ping("d-beyond", -1.2870 - dlat, 36.8172, eligible=True)
    tok = mint("rider-1", ["mobility_dispatch"])
    async with await _client() as c:
        r = await c.post(
            "/api/v1/dispatch/rides",
            json={"lat": -1.2870, "lng": 36.8172, "radius_m": beyond * 2},
            headers=auth(tok),
        )
    assert r.status_code == 200
    # The clamp holds: driver beyond the server max is not matched despite the huge requested radius.
    assert r.json()["drivers_matched"] == 0
