"""Router-level tests for the billing security-hardening pass.

Two controls added in routers/billing.py:
  1. POST /billing/checkout is rate-limited (STK-spam guard) — O(1) Redis,
     fail-open. The Nth+1 attempt in the window returns 429.
  2. POST /billing/mpesa/callback honours an optional Safaricom IP allow-list:
     empty (default) ⇒ processed; configured + foreign IP ⇒ ignored but still 200
     (a non-200 would make Safaricom retry-storm).

The billing service's money/network paths are monkeypatched out; the entitlement
Redis is the in-memory fake from the step-1 suite. No Postgres, no Daraja.
"""
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from PE.weespas.main import app
from PE.weespas.core.config import settings
from PE.weespas.core.database import get_db
from PE.weespas.services.auth_service import get_current_user
from PE.weespas.services import entitlement_service as ent
from PE.weespas.routers import billing as billing_router
from tests.test_entitlement_service import FakeRedis


USER = NS(id="user-1", is_active=True, phone="254700000000")


@pytest.fixture
def client(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(ent, "redis_client", fake)
    monkeypatch.setattr(ent, "_redis", lambda: fake)

    app.dependency_overrides[get_current_user] = lambda: USER
    app.dependency_overrides[get_db] = lambda: NS()  # never touched (service mocked)
    try:
        yield TestClient(app), fake, monkeypatch
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)


# ---- 1. checkout rate-limit ----------------------------------------------

def test_checkout_rate_limited_after_max(client, monkeypatch):
    c, _fake, mp = client
    # Stub the money path: every call "succeeds" so only the rate-limit can 429.
    mp.setattr(billing_router.billing_service, "create_checkout",
               lambda db, **kw: NS(id="intent-1", status="pending"))
    mp.setattr(settings, "checkout_rate_max", 3, raising=False)
    mp.setattr(settings, "checkout_rate_window_s", 300, raising=False)

    codes = [c.post("/api/v1/billing/checkout", json={"tier": "T1"}).status_code
             for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_checkout_rate_limit_fails_open_on_redis_error(client, monkeypatch):
    c, fake, mp = client
    mp.setattr(billing_router.billing_service, "create_checkout",
               lambda db, **kw: NS(id="intent-1", status="pending"))
    mp.setattr(settings, "checkout_rate_max", 1, raising=False)
    fake.fail = True  # Redis outage must NOT block a paying user
    assert c.post("/api/v1/billing/checkout", json={"tier": "T1"}).status_code == 200


# ---- 2. callback IP allow-list -------------------------------------------

def test_callback_processed_when_allowlist_empty(client, monkeypatch):
    c, _fake, mp = client
    mp.setattr(settings, "mpesa_callback_allowed_ips", "", raising=False)
    seen = {}
    mp.setattr(billing_router.billing_service, "settle_from_callback",
               lambda db, parsed: seen.setdefault("called", True) or "processed")
    r = c.post("/api/v1/billing/mpesa/callback", json={"Body": {}})
    assert r.status_code == 200 and r.json()["ResultCode"] == 0
    assert seen.get("called") is True   # default = no gate, body is processed


def test_callback_ignored_from_foreign_ip(client, monkeypatch):
    c, _fake, mp = client
    mp.setattr(settings, "mpesa_callback_allowed_ips", "196.201.214.200", raising=False)
    called = {"n": 0}
    mp.setattr(billing_router.billing_service, "settle_from_callback",
               lambda db, parsed: called.__setitem__("n", called["n"] + 1) or "processed")
    # TestClient's default client IP is 'testclient', not the allow-listed IP.
    r = c.post("/api/v1/billing/mpesa/callback", json={"Body": {}})
    assert r.status_code == 200 and r.json()["ResultCode"] == 0   # still 200 (no retry-storm)
    assert called["n"] == 0                                       # body NOT processed


def test_callback_processed_from_allowed_ip(client, monkeypatch):
    c, _fake, mp = client
    mp.setattr(settings, "mpesa_callback_allowed_ips", "9.9.9.9", raising=False)
    called = {"n": 0}
    mp.setattr(billing_router.billing_service, "settle_from_callback",
               lambda db, parsed: called.__setitem__("n", called["n"] + 1) or "processed")
    # Spoof the source via X-Forwarded-For (the _client_ip helper honours it).
    r = c.post("/api/v1/billing/mpesa/callback", json={"Body": {}},
               headers={"X-Forwarded-For": "9.9.9.9"})
    assert r.status_code == 200
    assert called["n"] == 1
