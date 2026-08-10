"""Runtime configuration for the served InSAR read app.

The read app had no config surface beyond the DuckDB path — it was a fully public,
stateless map API. Hardening it (auth + rate-limit + CORS lock) needs a few settings, so
this module centralises them. Plain ``os.getenv`` on purpose: the serving venv has neither
``pydantic-settings`` nor a ``.env`` loader, and adding one just for five strings would be
overkill. Everything here is optional with a safe default, so an unconfigured dev box
behaves exactly as the old public app (auth OFF, no rate limit, localhost CORS).

Read once at import. To change config, restart the process (same posture as the DuckDB
bundle, which is also built once at startup).
"""
from __future__ import annotations

import os
from functools import lru_cache


# --- Deployment environment -------------------------------------------------------------
# "development" (default) or "production". In production the app refuses to start unless
# auth is actually configured (see app/main.py boot guard) — so a forgotten public-key env
# can never SILENTLY re-expose the whole risk dataset. Dev stays permissive (auth optional).
INSAR_ENV = os.getenv("INSAR_ENV", "development").strip().lower()


def is_production() -> bool:
    return INSAR_ENV.startswith("prod")


# --- Auth (RS256 telemetry-token verification) ------------------------------------------
# The bridge token Weespas mints is RS256-signed; we verify it with the PUBLIC key only.
# Provide EITHER the PEM inline (INSAR_JWT_PUBLIC_KEY) or a file path
# (INSAR_JWT_PUBLIC_KEY_PATH). If NEITHER is set, auth is DISABLED and the data endpoints
# stay public — this preserves the historical dev behaviour and lets the dependency deploy
# inert before keys are provisioned (rollout step 3, gated). In prod the key MUST be set.
INSAR_JWT_ALGORITHM = os.getenv("INSAR_JWT_ALGORITHM", "RS256")
# Must match Weespas's auth_service.INSAR_TELEMETRY_SCOPE — a token without this scope
# (e.g. a normal access token) is refused, so a leaked access token can't read the data.
INSAR_JWT_SCOPE = os.getenv("INSAR_JWT_SCOPE", "insar_telemetry")


@lru_cache(maxsize=1)
def jwt_public_key() -> str:
    """The RSA public PEM used to verify telemetry tokens, or '' when auth is disabled.
    Inline env wins over the file path. Read+cached once."""
    inline = os.getenv("INSAR_JWT_PUBLIC_KEY", "").strip()
    if inline:
        # Allow literal "\n" in a single-line env var (common in CI/secret stores).
        return inline.replace("\\n", "\n")
    path = os.getenv("INSAR_JWT_PUBLIC_KEY_PATH", "").strip()
    if path and os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    return ""


def auth_enabled() -> bool:
    """True when a public key is configured — only then are the data endpoints gated.
    No key ⇒ inert (dev/public mode)."""
    return bool(jwt_public_key())


# --- CORS -------------------------------------------------------------------------------
# Comma-separated allowed origins. Default covers the local InSAR + Weespas Vite servers.
# In prod, set INSAR_ALLOWED_ORIGINS to the real InSAR frontend origin(s).
def allowed_origins() -> list[str]:
    raw = os.getenv(
        "INSAR_ALLOWED_ORIGINS",
        "http://localhost:5174,http://localhost:5173,http://127.0.0.1:5174",
    )
    return [o.strip() for o in raw.split(",") if o.strip()]


# --- Rate limiting (optional) -----------------------------------------------------------
# Fixed-window per authenticated `sub`. Inert unless REDIS_URL is set, so a dev box without
# redis is unaffected. See app/ratelimit.py.
REDIS_URL = os.getenv("REDIS_URL", "").strip()
INSAR_RATE_LIMIT = int(os.getenv("INSAR_RATE_LIMIT", "120"))      # requests per window per sub
INSAR_RATE_WINDOW_S = int(os.getenv("INSAR_RATE_WINDOW_S", "60"))  # window length, seconds


def rate_limit_enabled() -> bool:
    return bool(REDIS_URL) and INSAR_RATE_LIMIT > 0
