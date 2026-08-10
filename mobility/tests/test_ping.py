"""Ping uplink: coordinate validation, own-dot identity, GEO + freshness + eligibility writes."""
import httpx

from PE.mobility.main import app
from PE.mobility.services import geo
from PE.mobility.services.event_bus import get_client

_TRANSPORT = httpx.ASGITransport(app=app)


async def _client():
    return httpx.AsyncClient(transport=_TRANSPORT, base_url="http://test")


async def test_out_of_range_coord_rejected(mint, auth):
    tok = mint("driver-1", ["mobility_dispatch"])
    async with await _client() as c:
        r = await c.post("/api/v1/dispatch/ping", json={"lat": 999, "lng": 36.81}, headers=auth(tok))
    # pydantic edge validation returns 422 before the handler.
    assert r.status_code == 422


async def test_ping_writes_position_and_freshness(mint, auth):
    tok = mint("driver-1", ["mobility_dispatch", "dispatch:eligible"])
    async with await _client() as c:
        r = await c.post("/api/v1/dispatch/ping", json={"lat": -1.2864, "lng": 36.8172}, headers=auth(tok))
    assert r.status_code == 200
    # Position stored under the token sub (not any body field).
    pos = await get_client().geopos("mobility:drivers:pos", "driver-1")
    assert pos and pos[0] is not None
    lon, lat = pos[0]
    assert abs(lon - 36.8172) < 1e-3 and abs(lat + 1.2864) < 1e-3
    # Freshness stamped, eligibility recorded (scope carried dispatch:eligible).
    assert await geo.last_seen("driver-1") is not None
    assert await get_client().sismember("mobility:drivers:eligible", "driver-1") == 1


async def test_ping_without_eligibility_scope_not_in_eligible_set(mint, auth):
    tok = mint("driver-2", ["mobility_dispatch"])  # no dispatch:eligible
    async with await _client() as c:
        await c.post("/api/v1/dispatch/ping", json={"lat": -1.28, "lng": 36.81}, headers=auth(tok))
    assert await get_client().sismember("mobility:drivers:eligible", "driver-2") == 0


async def test_eligibility_revocation_propagates_on_next_ping(mint, auth):
    """A driver eligible now, then pinging without the scope, drops from the eligible set."""
    elig = mint("driver-3", ["mobility_dispatch", "dispatch:eligible"])
    revoked = mint("driver-3", ["mobility_dispatch"])  # same sub, KYC gone
    async with await _client() as c:
        await c.post("/api/v1/dispatch/ping", json={"lat": -1.28, "lng": 36.81}, headers=auth(elig))
        assert await get_client().sismember("mobility:drivers:eligible", "driver-3") == 1
        await c.post("/api/v1/dispatch/ping", json={"lat": -1.28, "lng": 36.81}, headers=auth(revoked))
    assert await get_client().sismember("mobility:drivers:eligible", "driver-3") == 0
