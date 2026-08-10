"""Entitlement primitive tests (PE/billing_architecture.md §2, step 1).

No real Redis: a small in-memory fake implements exactly the ops the service uses,
including a faithful Python port of the reveal Lua script (so the atomic
exists→sismember→scard→sadd contract is exercised). The money path is NOT involved —
windows are granted directly via grant_window, which is what the billing service will
call after a settled payment.
"""
import time

import pytest

from PE.weespas.services import entitlement_service as ent
from PE.weespas.services.entitlement_service import RevealOutcome
from PE.weespas.services.billing_tiers import PAID_TIERS, HOOK_TIER
from redis.exceptions import RedisError


# ---- Minimal in-memory fake Redis ----------------------------------------

class FakeRedis:
    """Just enough Redis for the entitlement service. TTLs are tracked but, for
    test determinism, only enforced when a test calls `advance()`."""

    def __init__(self):
        self.h: dict[str, dict] = {}      # hashes
        self.s: dict[str, set] = {}       # sets
        self.kv: dict[str, str] = {}      # plain strings (hook cooldown)
        self.expiry: dict[str, float] = {}
        self.now = 1_000_000.0
        self.fail = False                 # flip to simulate Redis outage

    # -- helpers --
    def _maybe_fail(self):
        if self.fail:
            raise RedisError("simulated outage")

    def _alive(self, key) -> bool:
        exp = self.expiry.get(key)
        if exp is not None and exp <= self.now:
            self._evict(key)
            return False
        return key in self.h or key in self.s or key in self.kv

    def _evict(self, key):
        self.h.pop(key, None); self.s.pop(key, None)
        self.kv.pop(key, None); self.expiry.pop(key, None)

    def advance(self, seconds):
        self.now += seconds

    # -- ops used by the service --
    def exists(self, key):
        self._maybe_fail()
        return 1 if self._alive(key) else 0

    def hset(self, key, mapping=None):
        self._maybe_fail()
        self.h.setdefault(key, {}).update({k: str(v) for k, v in (mapping or {}).items()})
        return 1

    def hget(self, key, field):
        self._maybe_fail()
        return self.h.get(key, {}).get(field) if self._alive(key) else None

    def hgetall(self, key):
        self._maybe_fail()
        return dict(self.h.get(key, {})) if self._alive(key) else {}

    def sadd(self, key, *members):
        self._maybe_fail()
        s = self.s.setdefault(key, set())
        before = len(s)
        s.update(str(m) for m in members)
        return len(s) - before

    def sismember(self, key, member):
        self._maybe_fail()
        return 1 if (self._alive(key) and str(member) in self.s.get(key, set())) else 0

    def smembers(self, key):
        self._maybe_fail()
        return set(self.s.get(key, set())) if self._alive(key) else set()

    def scard(self, key):
        self._maybe_fail()
        return len(self.s.get(key, set())) if self._alive(key) else 0

    def delete(self, key):
        self._maybe_fail()
        self._evict(key)
        return 1

    def expire(self, key, seconds):
        self._maybe_fail()
        if self._alive(key):
            self.expiry[key] = self.now + int(seconds)
            return 1
        return 0

    def ttl(self, key):
        self._maybe_fail()
        exp = self.expiry.get(key)
        if exp is None:
            return -1
        return max(int(exp - self.now), 0)

    def pttl(self, key):
        exp = self.expiry.get(key)
        if exp is None:
            return -1
        return max(int((exp - self.now) * 1000), 0)

    def pexpire(self, key, millis):
        if self._alive(key):
            self.expiry[key] = self.now + (int(millis) / 1000.0)
            return 1
        return 0

    def incr(self, key):
        self._maybe_fail()
        # Honour TTL: an expired counter resets to a fresh window.
        cur = int(self.kv.get(key, "0")) if self._alive(key) else 0
        cur += 1
        self.kv[key] = str(cur)
        return cur

    def set(self, key, value, nx=False, ex=None):
        self._maybe_fail()
        if nx and self._alive(key):
            return None
        self.kv[key] = str(value)
        if ex is not None:
            self.expiry[key] = self.now + int(ex)
        return True

    def pipeline(self):
        return _FakePipe(self)

    def eval(self, script, numkeys, *args):
        """Faithful port of the reveal Lua: exists→sismember→scard→sadd, atomic."""
        self._maybe_fail()
        wkey, ukey = args[0], args[1]
        listing = str(args[2])
        if not self._alive(wkey):
            return [0, -1]
        quota = int(self.h.get(wkey, {}).get("quota", 0))
        if str(listing) in self.s.get(ukey, set()):
            return [1, quota - len(self.s.get(ukey, set()))]
        used = len(self.s.get(ukey, set()))
        if used >= quota:
            return [2, 0]
        self.s.setdefault(ukey, set()).add(listing)
        # mirror the Lua: pin the unlocked set's TTL to the window's remaining TTL
        pttl = self.pttl(wkey)
        if pttl > 0:
            self.pexpire(ukey, pttl)
        return [3, quota - (used + 1)]


class _FakePipe:
    def __init__(self, r): self.r = r; self.ops = []
    def delete(self, k): self.ops.append(("delete", (k,))); return self
    def hset(self, k, mapping=None): self.ops.append(("hset", (k,), {"mapping": mapping})); return self
    def expire(self, k, s): self.ops.append(("expire", (k, s))); return self
    def execute(self):
        out = []
        for op in self.ops:
            name, a = op[0], op[1]
            kw = op[2] if len(op) > 2 else {}
            out.append(getattr(self.r, name)(*a, **kw))
        self.ops = []
        return out


@pytest.fixture
def fake(monkeypatch):
    fr = FakeRedis()
    # The service caches the client via _redis(); patch the module-level reference.
    monkeypatch.setattr(ent, "redis_client", fr)
    # _redis() returns the module global; ensure it points at the fake.
    monkeypatch.setattr(ent, "_redis", lambda: fr)
    return fr


U = "user-1"


# ---- reveal: no window ----------------------------------------------------

def test_reveal_without_window_is_no_window(fake):
    r = ent.reveal(U, "L1")
    assert r.outcome is RevealOutcome.NO_WINDOW


# ---- grant + basic reveal -------------------------------------------------

def test_grant_then_reveal_consumes_a_slot(fake):
    ent.grant_window(U, "T1", txn_id="rcpt-1")     # 3 reveals / 2h
    r = ent.reveal(U, "L1")
    assert r.outcome is RevealOutcome.REVEALED
    assert r.consumed is True
    assert r.remaining == 2


def test_rereveal_same_listing_is_free_idempotent(fake):
    ent.grant_window(U, "T1", txn_id="rcpt-1")
    first = ent.reveal(U, "L1")
    second = ent.reveal(U, "L1")
    assert first.consumed is True
    assert second.outcome is RevealOutcome.REVEALED
    assert second.consumed is False          # no second slot used
    assert second.remaining == 2             # still 2 left (only L1 consumed once)


def test_quota_exhaustion(fake):
    ent.grant_window(U, "T1", txn_id="rcpt-1")   # quota 3
    for lid in ("L1", "L2", "L3"):
        assert ent.reveal(U, lid).outcome is RevealOutcome.REVEALED
    r = ent.reveal(U, "L4")
    assert r.outcome is RevealOutcome.QUOTA_EXHAUSTED
    assert r.remaining == 0
    # ...but a 4th DISTINCT listing is blocked while already-revealed ones still work
    assert ent.reveal(U, "L2").outcome is RevealOutcome.REVEALED  # idempotent, free


# ---- is_revealed / revealed_set (serializer gate) -------------------------

def test_is_revealed_reflects_state(fake):
    ent.grant_window(U, "T2", txn_id="rcpt-2")
    assert ent.is_revealed(U, "L1") is False
    ent.reveal(U, "L1")
    assert ent.is_revealed(U, "L1") is True
    assert ent.is_revealed(U, "L9") is False


def test_revealed_set_and_anon(fake):
    ent.grant_window(U, "T2", txn_id="rcpt-2")
    ent.reveal(U, "L1"); ent.reveal(U, "L2")
    assert ent.revealed_set(U) == {"L1", "L2"}
    # Anonymous (no user_id) never reveals anything.
    assert ent.revealed_set("") == set()
    assert ent.is_revealed("", "L1") is False


# ---- option A: replace semantics ------------------------------------------

def test_new_purchase_replaces_window_and_resets_unlocked(fake):
    ent.grant_window(U, "T1", txn_id="rcpt-1")   # 3 reveals
    ent.reveal(U, "L1"); ent.reveal(U, "L2")     # 2 used, L1/L2 unlocked
    assert ent.revealed_set(U) == {"L1", "L2"}

    ent.grant_window(U, "T2", txn_id="rcpt-2")   # REPLACE: 6 reveals, fresh slate
    # Old unlocks are gone (replace, not stack).
    assert ent.revealed_set(U) == set()
    st = ent.entitlement_status(U)
    assert st["tier"] == "T2" and st["quota"] == 6 and st["used"] == 0
    # L1 must be re-revealed (and now costs a slot again under the new window).
    r = ent.reveal(U, "L1")
    assert r.consumed is True and r.remaining == 5


# ---- window expiry --------------------------------------------------------

def test_window_expiry_clears_everything(fake):
    ent.grant_window(U, "T1", txn_id="rcpt-1")   # 2h window
    ent.reveal(U, "L1")
    fake.advance(PAID_TIERS["T1"].window_seconds + 1)
    assert ent.reveal(U, "L2").outcome is RevealOutcome.NO_WINDOW
    assert ent.is_revealed(U, "L1") is False
    assert ent.entitlement_status(U) == {"active": False}


# ---- status snapshot ------------------------------------------------------

def test_entitlement_status(fake):
    assert ent.entitlement_status(U) == {"active": False}
    ent.grant_window(U, "T3", txn_id="rcpt-3")   # 10 / 24h
    ent.reveal(U, "L1")
    st = ent.entitlement_status(U)
    assert st["active"] is True
    assert st["tier"] == "T3"
    assert st["quota"] == 10 and st["used"] == 1 and st["remaining"] == 9
    assert 0 < st["expires_in_seconds"] <= PAID_TIERS["T3"].window_seconds


# ---- unknown tier ---------------------------------------------------------

def test_grant_unknown_tier_raises(fake):
    with pytest.raises(ValueError):
        ent.grant_window(U, "T9", txn_id="x")


# ---- free hook ------------------------------------------------------------

def test_hook_granted_once_then_cooldown(fake):
    t = ent.try_grant_hook(U)
    assert t is not None and t.code == "HOOK"
    assert ent.entitlement_status(U)["quota"] == HOOK_TIER.quota
    # Use the hook reveal, let the window expire → still in cooldown → no re-grant.
    ent.reveal(U, "L1")
    fake.advance(HOOK_TIER.window_seconds + 1)
    assert ent.try_grant_hook(U) is None          # cooldown still active


def test_hook_not_granted_when_window_active(fake):
    ent.grant_window(U, "T1", txn_id="rcpt-1")
    assert ent.try_grant_hook(U) is None           # already has a paid window


# ---- fail-safe: Redis outage denies, never leaks --------------------------

def test_reveal_fails_safe_to_no_window_on_redis_error(fake):
    ent.grant_window(U, "T1", txn_id="rcpt-1")
    fake.fail = True
    assert ent.reveal(U, "L1").outcome is RevealOutcome.NO_WINDOW


def test_is_revealed_fails_safe_to_false_on_redis_error(fake):
    ent.grant_window(U, "T1", txn_id="rcpt-1")
    ent.reveal(U, "L1")
    fake.fail = True
    assert ent.is_revealed(U, "L1") is False        # never leak exact coords on error
    assert ent.entitlement_status(U) == {"active": False}


# ---- check_rate_limit (checkout STK-spam guard, billing_architecture §10) --

def test_rate_limit_allows_up_to_max_then_blocks(fake):
    # max_hits=3 → first three calls allowed, fourth blocked.
    assert [ent.check_rate_limit("checkout", U, max_hits=3, window_seconds=300)
            for _ in range(4)] == [True, True, True, False]


def test_rate_limit_is_per_identity(fake):
    assert ent.check_rate_limit("checkout", "userA", max_hits=1, window_seconds=300) is True
    assert ent.check_rate_limit("checkout", "userA", max_hits=1, window_seconds=300) is False
    # A different user has an independent budget.
    assert ent.check_rate_limit("checkout", "userB", max_hits=1, window_seconds=300) is True


def test_rate_limit_window_resets_after_expiry(fake):
    assert ent.check_rate_limit("checkout", U, max_hits=1, window_seconds=300) is True
    assert ent.check_rate_limit("checkout", U, max_hits=1, window_seconds=300) is False
    fake.advance(301)   # window elapsed → counter evicted, fresh budget
    assert ent.check_rate_limit("checkout", U, max_hits=1, window_seconds=300) is True


def test_rate_limit_fails_open_on_redis_error(fake):
    # Auth is the real control; a Redis outage must NEVER block a paying user.
    fake.fail = True
    assert ent.check_rate_limit("checkout", U, max_hits=1, window_seconds=300) is True


def test_rate_limit_zero_max_is_disabled(fake):
    # max_hits<=0 is treated as "no limit configured" → always allow.
    assert ent.check_rate_limit("checkout", U, max_hits=0, window_seconds=300) is True
