"""Telemetry-token auth for the served InSAR data API.

The map UI is login-gated on the frontend, but the *data* endpoints (the bundle, the
building queries) are the real asset — one ``curl`` of ``/aoi/{code}/bundle`` returns every
building's risk classification. This dependency closes that hole: a request must carry the
RS256 telemetry token Weespas mints for a signed-in user.

Trust model: we verify with a PUBLIC key only (see app/config.py). This app can therefore
*check* a token but can never *mint* one — a compromise here leaks no signing secret that
could forge a money/PII token against Weespas. RS256-only verification also makes this
immune to the HS256 algorithm-confusion attack by construction (we never HMAC-verify).

Fail-closed: any problem (missing/garbled header, bad signature, expired, wrong scope)
→ 401. Inert when no public key is configured (``auth_enabled()`` is False) — the endpoints
stay public, preserving the historical dev behaviour until keys are provisioned. O(1): a
signature check and a dict lookup, no DB, no network.
"""
from __future__ import annotations

import jwt  # PyJWT
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import config

# auto_error=False so a MISSING header reaches us as None — we decide the response
# (401 when auth is on, pass-through when it's off), rather than HTTPBearer raising 403.
_bearer = HTTPBearer(auto_error=False)

# Claims we always require to be present + valid. PyJWT checks `exp` expiry automatically
# when it's in the token; requiring it means a token minted without an exp is refused.
_DECODE_OPTIONS = {"require": ["exp", "sub"]}


def require_telemetry_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str | None:
    """FastAPI dependency: verify the RS256 telemetry token and return its ``sub``.

    Returns the user id (``sub``) so a downstream dependency (rate limiting) can key on it.
    When auth is disabled (no public key) returns None and lets the request through.
    """
    if not config.auth_enabled():
        return None  # inert: data API stays public until a key is provisioned

    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    try:
        payload = jwt.decode(
            credentials.credentials,
            config.jwt_public_key(),
            algorithms=[config.INSAR_JWT_ALGORITHM],  # RS256 only — no HMAC path exists here
            options=_DECODE_OPTIONS,
        )
    except jwt.PyJWTError:
        # Bad signature, expired, malformed, missing required claim — all collapse to 401.
        # Deliberately opaque: we don't tell a caller which check failed.
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    if payload.get("scope") != config.INSAR_JWT_SCOPE:
        # A correctly-signed token of the WRONG kind (e.g. a normal Weespas access token,
        # were it ever RS256) must not read the data. Only the telemetry scope may.
        raise HTTPException(status_code=401, detail="Token is not telemetry-scoped")

    return payload.get("sub")
