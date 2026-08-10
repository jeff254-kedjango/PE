"""Per-account rate limiting: an authed account can't bulk-pull the whole dataset.

The auth tests prove anonymous scraping is blocked; this proves a signed-in account is
throttled too. Uses a tiny in-memory fake redis injected into the rate-limit module, so no
real redis is needed. Also asserts the two posture choices: inert without REDIS_URL, and
fail-OPEN if redis errors.
"""
from __future__ import annotations

import pytest


class _FakeRedis:
    """Minimal redis stand-in: INCR + EXPIRE over a dict, via a pipeline() shim."""

    def __init__(self, raise_on_exec: bool = False):
        self.store: dict[str, int] = {}
        self.raise_on_exec = raise_on_exec

    def pipeline(self):
        return _FakePipe(self)


class _FakePipe:
    def __init__(self, parent: _FakeRedis):
        self.parent = parent
        self._ops: list = []

    def incr(self, key: str, amount: int = 1):
        self._ops.append(("incr", key, amount))
        return self

    def expire(self, key: str, ttl: int):
        self._ops.append(("expire", key, ttl))
        return self

    def execute(self):
        if self.parent.raise_on_exec:
            raise RuntimeError("redis down")
        results = []
        for op in self._ops:
            if op[0] == "incr":
                self.parent.store[op[1]] = self.parent.store.get(op[1], 0) + op[2]
                results.append(self.parent.store[op[1]])
            else:
                results.append(True)
        return results


def _auth(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def _arm_ratelimit(monkeypatch, fake, limit=3, window=60):
    """Point the (already-imported) ratelimit module at a fake redis + low limit."""
    import app.ratelimit as rl
    import app.config as cfg

    monkeypatch.setattr(cfg, "INSAR_RATE_LIMIT", limit, raising=False)
    monkeypatch.setattr(cfg, "INSAR_RATE_WINDOW_S", window, raising=False)
    # Bypass _get_redis()'s construction path — inject the fake directly.
    monkeypatch.setattr(rl, "_redis", fake, raising=False)
    monkeypatch.setattr(rl, "_redis_init", True, raising=False)


def test_over_limit_returns_429_per_sub(auth_app, make_token, monkeypatch):
    """The (limit+1)-th request for one sub → 429; a different sub is unaffected."""
    _arm_ratelimit(monkeypatch, _FakeRedis(), limit=3, window=60)
    tok_a = make_token(sub="user-A")
    for _ in range(3):
        assert auth_app.get("/aois", headers=_auth(tok_a)).status_code == 200
    assert auth_app.get("/aois", headers=_auth(tok_a)).status_code == 429
    # Different account → its own bucket, still allowed.
    tok_b = make_token(sub="user-B")
    assert auth_app.get("/aois", headers=_auth(tok_b)).status_code == 200


def test_inert_without_redis(auth_app, make_token, monkeypatch):
    """No redis configured → no throttling (many requests all 200)."""
    import app.ratelimit as rl
    monkeypatch.setattr(rl, "_redis", None, raising=False)
    monkeypatch.setattr(rl, "_redis_init", True, raising=False)
    tok = make_token(sub="user-C")
    for _ in range(10):
        assert auth_app.get("/aois", headers=_auth(tok)).status_code == 200


def test_fail_open_when_redis_errors(auth_app, make_token, monkeypatch):
    """Redis raising → request still served (fail-open; auth already gates access)."""
    _arm_ratelimit(monkeypatch, _FakeRedis(raise_on_exec=True), limit=1, window=60)
    tok = make_token(sub="user-D")
    for _ in range(5):
        assert auth_app.get("/aois", headers=_auth(tok)).status_code == 200
