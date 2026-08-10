"""Per-account fixed-window rate limiting for the InSAR data API.

Auth (app/auth.py) stops *unauthenticated* scraping; this stops an *authenticated* account
from pulling every AOI thousands of times — bulk exfiltration by someone who did sign in.
It keys on the verified ``sub`` from the auth dependency, so it runs AFTER auth and inherits
its identity.

Two deliberate posture choices:
  * **Inert without REDIS_URL** — a dev box with no redis is unaffected (pass-through).
  * **Fail-OPEN if redis errors** — auth (the RSA signature check) is the real security
    control and is redis-independent; a redis blip must not 503 the whole read API. So a
    redis failure degrades to "no throttling", logged, not to a denial. (Contrast app/auth.py,
    which fails CLOSED — losing auth would expose data, losing the throttle only loosens it.)

Fixed window (not sliding): key = ``insar:rl:{sub}:{floor(now/window)}``, INCR + EXPIRE,
over-limit → 429. Cheap and good enough to blunt bulk pulls; a sliding window isn't worth
the extra round-trips here.
"""
from __future__ import annotations

import logging
import time

from fastapi import Depends, HTTPException

from . import config
from .auth import require_telemetry_token

logger = logging.getLogger(__name__)

# Lazily-built module-level redis client (redis 8.x is in the serving venv). None when
# rate limiting is disabled or the client couldn't be constructed.
_redis = None
_redis_init = False


def _get_redis():
    global _redis, _redis_init
    if _redis_init:
        return _redis
    _redis_init = True
    if not config.rate_limit_enabled():
        _redis = None
        return None
    try:
        import redis  # imported lazily so a no-redis dev box never needs the dependency loaded

        _redis = redis.Redis.from_url(config.REDIS_URL, socket_timeout=0.25, socket_connect_timeout=0.25)
    except Exception as exc:  # pragma: no cover - construction is best-effort
        logger.warning("rate-limit: redis client init failed, limiting disabled: %s", exc)
        _redis = None
    return _redis


def rate_limit(sub: str | None = Depends(require_telemetry_token)) -> None:
    """FastAPI dependency: 429 if ``sub`` exceeded INSAR_RATE_LIMIT in the current window.

    No-op when auth is off (sub is None ⇒ no identity to key on) or redis is unconfigured.
    """
    if sub is None:
        return  # auth disabled, or anonymous — nothing to rate-limit by
    client = _get_redis()
    if client is None:
        return  # rate limiting disabled / unavailable → fail open

    window = config.INSAR_RATE_WINDOW_S
    bucket = int(time.time()) // window
    key = f"insar:rl:{sub}:{bucket}"
    try:
        pipe = client.pipeline()
        pipe.incr(key, 1)
        pipe.expire(key, window)
        count = pipe.execute()[0]
    except Exception as exc:  # redis down/slow → fail OPEN (auth already gates access)
        logger.warning("rate-limit: redis error, allowing request: %s", exc)
        return

    if count > config.INSAR_RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded — too many requests",
            headers={"Retry-After": str(window)},
        )
