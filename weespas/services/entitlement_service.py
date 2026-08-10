"""Reveal/window entitlement primitive (the O(1) Redis core of the access model).

Implements PE/billing_architecture.md §2. A purchased *window* buys N reveals for T
seconds. A "reveal" sharpens one specific listing's exact, navigable location; the
window's quota caps how many distinct listings can be revealed, and re-revealing a
listing already unlocked in the window is FREE and idempotent.

State is two Redis keys per user, both expiring with the window (no cleanup job):

    ent:{user}:window   → HASH { tier, quota, granted_at, txn_id }   TTL = T
    ent:{user}:unlocked → SET  of listing_id                         TTL = T

Why a SET (not a bare counter): it makes re-reveal free/idempotent AND makes the
quota the set's cardinality — one structure does both.

This module is money-agnostic: `grant_window` is called by the billing service
AFTER a settled M-Pesa payment (or by the free-hook path), but it neither knows nor
cares where the grant came from. That keeps the hot path (reveal) pure and testable.

Fail-safe: any Redis error on a reveal DENIES (returns NO_WINDOW) — we never grant
on error. Browsing/discovery is unaffected because it does not depend on this read.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from enum import Enum

from redis import Redis
from redis.exceptions import RedisError

from PE.weespas.services.cache import redis_client
from PE.weespas.services.billing_tiers import (
    Tier,
    get_tier,
    HOOK_TIER,
    HOOK_COOLDOWN_SECONDS,
)

logger = logging.getLogger(__name__)


# ---- Result types ---------------------------------------------------------

class RevealOutcome(str, Enum):
    REVEALED = "revealed"            # exact coords may be returned (slot consumed or already held)
    NO_WINDOW = "no_window"          # user has no active window → FE opens the chooser
    QUOTA_EXHAUSTED = "quota"        # window active but all N reveals used → FE offers upgrade


@dataclass(frozen=True)
class RevealResult:
    outcome: RevealOutcome
    # True only when this call newly consumed a slot (vs an idempotent re-reveal).
    consumed: bool = False
    # Reveals still available in the window (None when there is no window).
    remaining: int | None = None


def _window_key(user_id: str) -> str:
    return f"ent:{user_id}:window"


def _unlocked_key(user_id: str) -> str:
    return f"ent:{user_id}:unlocked"


def _hook_cooldown_key(user_id: str) -> str:
    return f"hook:{user_id}"


# ---- Atomic reveal (Lua) --------------------------------------------------
# The reveal decision must be atomic: two pins tapped at the same instant near the
# quota edge could otherwise both pass a separate SISMEMBER/SCARD check and let
# quota+1 reveals through. One Lua script does exists→sismember→scard→sadd in a
# single round-trip, so the quota can never be exceeded. Still O(1).
#
# KEYS[1] = window hash key, KEYS[2] = unlocked set key
# ARGV[1] = listing_id
# Returns a 2-element array { code, remaining }:
#   code:  0 = no window, 1 = already revealed (idempotent), 2 = quota exhausted,
#          3 = newly revealed (slot consumed)
#   remaining: reveals left after this call (or -1 when there is no window)
_REVEAL_LUA = """
if redis.call('EXISTS', KEYS[1]) == 0 then
  return {0, -1}
end
local quota = tonumber(redis.call('HGET', KEYS[1], 'quota')) or 0
if redis.call('SISMEMBER', KEYS[2], ARGV[1]) == 1 then
  return {1, quota - redis.call('SCARD', KEYS[2])}
end
local used = redis.call('SCARD', KEYS[2])
if used >= quota then
  return {2, 0}
end
redis.call('SADD', KEYS[2], ARGV[1])
-- Align the unlocked set's TTL to the window's: grant_window's EXPIRE on an
-- empty/deleted set is a no-op in Redis, so the set would otherwise persist past
-- the window and leak. Pin it to expire exactly when the window does.
local pttl = redis.call('PTTL', KEYS[1])
if pttl > 0 then
  redis.call('PEXPIRE', KEYS[2], pttl)
end
return {3, quota - (used + 1)}
"""


def _redis() -> Redis:
    return redis_client


def reveal(user_id: str, listing_id: str) -> RevealResult:
    """Attempt to reveal one listing's exact location for `user_id`.

    O(1). Atomic across concurrent taps. Re-revealing an already-unlocked listing
    is free (consumed=False). Fail-safe: any Redis error → NO_WINDOW (deny).
    """
    try:
        code, remaining = _redis().eval(
            _REVEAL_LUA, 2, _window_key(user_id), _unlocked_key(user_id), str(listing_id)
        )
    except RedisError:
        logger.warning("entitlement reveal: Redis error → denying (user=%s)", user_id,
                       exc_info=True)
        return RevealResult(RevealOutcome.NO_WINDOW)

    code = int(code)
    remaining = int(remaining)
    if code == 0:
        return RevealResult(RevealOutcome.NO_WINDOW)
    if code == 1:
        return RevealResult(RevealOutcome.REVEALED, consumed=False, remaining=remaining)
    if code == 2:
        return RevealResult(RevealOutcome.QUOTA_EXHAUSTED, consumed=False, remaining=0)
    return RevealResult(RevealOutcome.REVEALED, consumed=True, remaining=remaining)


def is_revealed(user_id: str, listing_id: str) -> bool:
    """True iff this user has already revealed this listing in the active window.

    Used by the property serializer to decide exact-vs-fuzzed coords (O(1)).
    Fail-safe: Redis error → False (serve fuzzed; never leak exact on error).
    """
    if not user_id:
        return False
    try:
        return bool(_redis().sismember(_unlocked_key(user_id), str(listing_id)))
    except RedisError:
        logger.warning("entitlement is_revealed: Redis error → fuzzed (user=%s)", user_id,
                       exc_info=True)
        return False


def revealed_set(user_id: str) -> set[str]:
    """All listing_ids revealed in the active window — lets a list endpoint decide
    exact-vs-fuzzed for a whole page with one round-trip instead of one per row.
    Fail-safe: Redis error → empty set (everything fuzzed)."""
    if not user_id:
        return set()
    try:
        return set(_redis().smembers(_unlocked_key(user_id)))
    except RedisError:
        logger.warning("entitlement revealed_set: Redis error → empty (user=%s)", user_id,
                       exc_info=True)
        return set()


def grant_window(user_id: str, tier_code: str, *, txn_id: str) -> Tier:
    """Grant (or replace) a user's window for `tier_code`.

    OPTION A — REPLACE (commercial_model.md §12 decision): a new purchase wipes and
    replaces any existing window (fresh quota + fresh clock); leftover reveals from a
    prior window are discarded. To switch to additive top-ups later, this is the only
    function to change — callers are unaffected.

    Raises ValueError for an unknown tier. Returns the granted Tier.
    """
    tier = get_tier(tier_code)
    if tier is None:
        raise ValueError(f"unknown tier: {tier_code!r}")

    wkey, ukey = _window_key(user_id), _unlocked_key(user_id)
    pipe = _redis().pipeline()
    pipe.delete(ukey)  # fresh window starts with an empty unlocked set (replace semantics)
    pipe.hset(wkey, mapping={
        "tier": tier.code,
        "quota": tier.quota,
        "granted_at": int(time.time()),
        "txn_id": str(txn_id),
    })
    pipe.expire(wkey, tier.window_seconds)
    pipe.expire(ukey, tier.window_seconds)
    pipe.execute()
    return tier


def try_grant_hook(user_id: str) -> Tier | None:
    """Grant the free HOOK window once per cooldown, only if the user has no active
    window. Returns the Tier if granted, else None.

    The hook:{user} cooldown key (set NX with a long TTL) prevents farming the free
    reveal in a loop. The first paid window supersedes the hook via grant_window.
    """
    wkey = _window_key(user_id)
    try:
        if _redis().exists(wkey):
            return None  # already has a window; nothing to grant
        # Claim the cooldown slot atomically; only the winner grants.
        if not _redis().set(_hook_cooldown_key(user_id), "1", nx=True, ex=HOOK_COOLDOWN_SECONDS):
            return None  # within cooldown of a previous free hook
    except RedisError:
        logger.warning("entitlement try_grant_hook: Redis error (user=%s)", user_id,
                       exc_info=True)
        return None
    return grant_window(user_id, HOOK_TIER.code, txn_id="free-hook")


def check_rate_limit(action: str, identity: str, *, max_hits: int, window_seconds: int) -> bool:
    """Fixed-window rate-limit, O(1) in Redis. Returns True if the call is ALLOWED,
    False if the window's budget is exhausted.

    One counter per (action, identity) window: INCR, and on the first hit set the
    TTL so the whole window expires together (no cleanup job). This is the same
    Redis-primitive discipline as the receipt dedupe in billing_service.

    FAIL-OPEN: any Redis error returns True (allow). Auth is the real control; a
    Redis outage must never block a legitimate (e.g. paying) user. Contrast the
    reveal path, which fails closed because it gates a paid secret.
    """
    if max_hits <= 0:
        return True
    key = f"rl:{action}:{identity}"
    try:
        r = _redis()
        hits = r.incr(key)
        if hits == 1:
            # First hit in a fresh window — start the clock.
            r.expire(key, window_seconds)
        return hits <= max_hits
    except RedisError:
        logger.warning("check_rate_limit: Redis error → allow (action=%s id=%s)",
                       action, identity, exc_info=True)
        return True


def entitlement_status(user_id: str) -> dict:
    """Current window snapshot for the UI (a small status chip): tier, quota, used,
    remaining, seconds-to-expiry. Returns {active: False} when there is no window.
    Fail-safe: Redis error → {active: False}."""
    wkey, ukey = _window_key(user_id), _unlocked_key(user_id)
    try:
        data = _redis().hgetall(wkey)
        if not data:
            return {"active": False}
        quota = int(data.get("quota", 0))
        used = int(_redis().scard(ukey))
        ttl = int(_redis().ttl(wkey))
        return {
            "active": True,
            "tier": data.get("tier"),
            "quota": quota,
            "used": used,
            "remaining": max(quota - used, 0),
            "expires_in_seconds": max(ttl, 0),
        }
    except RedisError:
        logger.warning("entitlement_status: Redis error (user=%s)", user_id, exc_info=True)
        return {"active": False}
