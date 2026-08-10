"""Reveal endpoint tests (PE/billing_architecture.md §6, step 3).

Exercises the HTTP contract via TestClient with dependency overrides:
  * no window            → 402 {reason: no_window, tiers}
  * window + slot        → 200 exact coords + directions_url, slot consumed
  * re-reveal            → 200, not re-charged
  * quota exhausted      → 402 {reason: quota}
  * missing listing      → 404
  * GET /entitlement/me  → status snapshot

The entitlement service's Redis is replaced with the in-memory fake from the
step-1 suite; the DB and auth are dependency-overridden so no Postgres/JWT needed.
"""
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from PE.weespas.main import app
from PE.weespas.core.database import get_db
from PE.weespas.services.auth_service import get_current_user
from PE.weespas.services import entitlement_service as ent
from tests.test_entitlement_service import FakeRedis


USER = NS(id="user-1", is_active=True)

# One fake listing with an exact address; the reveal endpoint queries Address by
# property_id, so the fake DB only needs to answer that one query shape.
EXACT = NS(property_id="L1", latitude=-1.2921, longitude=36.8219,
           street_address="12 Ngong Rd Apt 4")


class _FakeQuery:
    def __init__(self, addr): self._addr = addr
    def join(self, *a, **k): return self
    def filter(self, *a, **k): return self
    def first(self): return self._addr


class _FakeDB:
    def __init__(self, addr): self._addr = addr
    def query(self, *a, **k): return _FakeQuery(self._addr)


@pytest.fixture
def client(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(ent, "redis_client", fake)
    monkeypatch.setattr(ent, "_redis", lambda: fake)

    app.dependency_overrides[get_current_user] = lambda: USER
    app.dependency_overrides[get_db] = lambda: _FakeDB(EXACT)
    try:
        yield TestClient(app), fake
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)


def test_first_reveal_is_free_via_hook(client):
    """A brand-new user (no window, fresh cooldown) gets ONE free reveal — the hook
    tier grants silently and the reveal succeeds with exact coords. This is the
    "accessible as much as possible" front door (commercial_model.md, step 6)."""
    c, _ = client
    r = c.post("/api/v1/reveal/L1")
    assert r.status_code == 200
    body = r.json()
    assert body["latitude"] == EXACT.latitude          # EXACT — the free look is real
    assert body["newly_charged"] is True
    # The granted window is the free HOOK tier (quota 1).
    st = c.get("/api/v1/reveal/entitlement/me").json()
    assert st["active"] is True and st["tier"] == "HOOK"


def test_402_after_free_hook_spent(client):
    """Once the single free reveal is used AND its short window is gone, a SECOND
    distinct listing hits the paywall (the hook is on cooldown, so no second free
    look). We simulate the window expiring via the fake clock."""
    c, fake = client
    assert c.post("/api/v1/reveal/L1").status_code == 200    # free hook consumed
    # Expire the 30-min hook window so there's no active window for the next reveal,
    # but the 24h anti-farm cooldown key is still live → no second free grant.
    fake.advance(31 * 60)
    r = c.post("/api/v1/reveal/L2")
    assert r.status_code == 402
    body = r.json()
    assert body["reason"] == "no_window"
    assert any(t["code"] == "T1" and t["price_kes"] == 20 for t in body["tiers"])


def test_reveal_without_window_returns_402_when_hook_unavailable(client):
    """If the user is already on hook cooldown (e.g. used the free look earlier), a
    reveal with no active window goes straight to the chooser."""
    c, fake = client
    # Pre-set the cooldown key so try_grant_hook declines.
    from PE.weespas.services.entitlement_service import _hook_cooldown_key
    fake.set(_hook_cooldown_key(USER.id), "1", nx=True, ex=86400)
    r = c.post("/api/v1/reveal/L1")
    assert r.status_code == 402
    body = r.json()
    assert body["reason"] == "no_window"
    assert any(t["code"] == "T1" and t["price_kes"] == 20 for t in body["tiers"])


def test_reveal_with_window_returns_exact_and_consumes(client):
    c, _ = client
    ent.grant_window(USER.id, "T1", txn_id="rcpt-1")   # 3 / 2h
    r = c.post("/api/v1/reveal/L1")
    assert r.status_code == 200
    body = r.json()
    assert body["latitude"] == EXACT.latitude          # EXACT, not fuzzed
    assert body["longitude"] == EXACT.longitude
    assert body["street_address"] == EXACT.street_address
    assert "google.com/maps/dir" in body["directions_url"]
    assert body["newly_charged"] is True
    assert body["remaining"] == 2


def test_re_reveal_is_not_recharged(client):
    c, _ = client
    ent.grant_window(USER.id, "T1", txn_id="rcpt-1")
    c.post("/api/v1/reveal/L1")
    r2 = c.post("/api/v1/reveal/L1")
    assert r2.status_code == 200
    assert r2.json()["newly_charged"] is False
    assert r2.json()["remaining"] == 2                 # still 2 — only L1 consumed once


def test_quota_exhausted_returns_402_quota(client):
    c, _ = client
    ent.grant_window(USER.id, "T1", txn_id="rcpt-1")   # quota 3
    # Use 3 distinct listings; the fake DB returns the same address but the
    # entitlement set keys on the path id, so these are distinct reveals.
    for lid in ("L1", "L2", "L3"):
        assert c.post(f"/api/v1/reveal/{lid}").status_code == 200
    r = c.post("/api/v1/reveal/L4")
    assert r.status_code == 402
    assert r.json()["reason"] == "quota"


def test_reveal_missing_listing_returns_404(client):
    c, _ = client
    # Override DB to a no-address result for this test.
    app.dependency_overrides[get_db] = lambda: _FakeDB(None)
    ent.grant_window(USER.id, "T1", txn_id="rcpt-1")
    r = c.post("/api/v1/reveal/Lx")
    assert r.status_code == 404


def test_entitlement_me_snapshot(client):
    c, _ = client
    assert c.get("/api/v1/reveal/entitlement/me").json() == {"active": False}
    ent.grant_window(USER.id, "T2", txn_id="rcpt-2")   # 6 / 4h
    c.post("/api/v1/reveal/L1")
    st = c.get("/api/v1/reveal/entitlement/me").json()
    assert st["active"] is True and st["tier"] == "T2"
    assert st["remaining"] == 5 and st["used"] == 1
