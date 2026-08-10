"""Server-side access metering: a full bundle pull reports to Weespas; a 304 does not.

This is what makes a direct-curl scraper visible to §8 company-detection. The tests stub
the outbound httpx.post so no Weespas process is needed, and assert: metered on 200, NOT on
304, inert without a sink URL, and never raising on a sink failure.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def metered_app(rsa_keypair, monkeypatch):
    """App with auth ON and a metering sink configured, plus a capture list the stubbed
    httpx.post appends to. Yields (client, captured)."""
    from fastapi.testclient import TestClient

    _, public_pem = rsa_keypair
    monkeypatch.setenv("INSAR_JWT_PUBLIC_KEY", public_pem)
    monkeypatch.setenv("WEESPAS_TELEMETRY_URL", "http://weespas.test/api/v1/insar-telemetry/event")
    monkeypatch.delenv("REDIS_URL", raising=False)

    import app.config as cfg
    importlib.reload(cfg)
    cfg.jwt_public_key.cache_clear()
    import app.auth as auth
    importlib.reload(auth)
    import app.ratelimit as rl
    importlib.reload(rl)
    import app.usage_meter as um
    importlib.reload(um)

    captured: list[dict] = []

    class _FakeHttpx:
        @staticmethod
        def post(url, json=None, headers=None, timeout=None):
            captured.append({"url": url, "json": json, "headers": headers})
            return None

    # The meter does `import httpx` locally — inject our fake into sys.modules.
    import sys
    monkeypatch.setitem(sys.modules, "httpx", _FakeHttpx)

    import app.main as main
    importlib.reload(main)

    with TestClient(main.app) as client:
        yield client, captured


def _auth(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def test_bundle_200_is_metered(metered_app, make_token):
    """A fresh bundle pull (200) fires one insar_bundle_fetch to the sink, attributed via
    the caller's own bearer, carrying the aoi_code (so it feeds breadth)."""
    client, captured = metered_app
    r = client.get("/aoi/huruma/bundle", headers=_auth(make_token()))
    assert r.status_code == 200
    assert len(captured) == 1
    assert captured[0]["json"] == {"action": "insar_bundle_fetch", "aoi_code": "huruma"}
    assert captured[0]["headers"]["Authorization"].startswith("Bearer ")


def test_bundle_304_is_not_metered(metered_app, make_token):
    """A cached revalidation (304) is NOT metered — that's the signature of normal use."""
    client, captured = metered_app
    tok = make_token()
    first = client.get("/aoi/huruma/bundle", headers=_auth(tok))
    etag = first.headers["etag"]
    captured.clear()
    r = client.get("/aoi/huruma/bundle", headers={**_auth(tok), "If-None-Match": etag})
    assert r.status_code == 304
    assert captured == []


def test_meter_inert_without_sink(auth_app, make_token, monkeypatch):
    """With no WEESPAS_TELEMETRY_URL, a bundle pull still succeeds and meters nothing
    (the read app keeps its no-live-network default)."""
    # auth_app fixture does not set WEESPAS_TELEMETRY_URL → usage_metering_enabled() False.
    r = auth_app.get("/aoi/huruma/bundle", headers=_auth(make_token()))
    assert r.status_code == 200  # no sink, no error


def test_meter_swallows_sink_failure(rsa_keypair, make_token, monkeypatch):
    """If the sink raises, the bundle response is unaffected (best-effort metering)."""
    import sys

    from fastapi.testclient import TestClient

    _, public_pem = rsa_keypair
    monkeypatch.setenv("INSAR_JWT_PUBLIC_KEY", public_pem)
    monkeypatch.setenv("WEESPAS_TELEMETRY_URL", "http://weespas.test/sink")
    monkeypatch.delenv("REDIS_URL", raising=False)

    import app.config as cfg
    importlib.reload(cfg)
    cfg.jwt_public_key.cache_clear()
    import app.auth as auth
    importlib.reload(auth)
    import app.ratelimit as rl
    importlib.reload(rl)
    import app.usage_meter as um
    importlib.reload(um)

    class _BoomHttpx:
        @staticmethod
        def post(*a, **k):
            raise RuntimeError("sink down")

    monkeypatch.setitem(sys.modules, "httpx", _BoomHttpx)
    import app.main as main
    importlib.reload(main)

    with TestClient(main.app) as client:
        r = client.get("/aoi/huruma/bundle", headers=_auth(make_token()))
        assert r.status_code == 200  # sink boom is swallowed
