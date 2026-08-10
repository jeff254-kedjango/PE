"""Auth: the RS256 audience guard + fail-closed behaviour (mobility core.auth)."""
import httpx
import pytest

from PE.mobility.main import app

_TRANSPORT = httpx.ASGITransport(app=app)


async def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=_TRANSPORT, base_url="http://test")


async def test_health_is_public():
    async with await _client() as c:
        r = await c.get("/health")
    assert r.status_code == 200
    assert r.json()["auth_enabled"] is True  # dev keypair configured in conftest


async def test_ping_requires_token():
    async with await _client() as c:
        r = await c.post("/api/v1/dispatch/ping", json={"lat": -1.28, "lng": 36.81})
    assert r.status_code == 401


async def test_wrong_audience_token_rejected(mint, auth):
    """A correctly-signed commerce_trade token must NOT authenticate against mobility."""
    tok = mint("driver-x", ["commerce_trade"], scope="commerce_trade")
    async with await _client() as c:
        r = await c.post("/api/v1/dispatch/ping", json={"lat": -1.28, "lng": 36.81}, headers=auth(tok))
    assert r.status_code == 401
    assert "mobility-scoped" in r.json()["detail"]


async def test_valid_mobility_token_accepted(mint, auth):
    tok = mint("driver-1", ["mobility_dispatch", "dispatch:eligible"])
    async with await _client() as c:
        r = await c.post("/api/v1/dispatch/ping", json={"lat": -1.28, "lng": 36.81}, headers=auth(tok))
    assert r.status_code == 200
    assert r.json()["ok"] is True


async def test_tampered_token_rejected(mint, auth):
    tok = mint("driver-1", ["mobility_dispatch"]) + "x"  # break the signature
    async with await _client() as c:
        r = await c.post("/api/v1/dispatch/ping", json={"lat": -1.28, "lng": 36.81}, headers=auth(tok))
    assert r.status_code == 401
