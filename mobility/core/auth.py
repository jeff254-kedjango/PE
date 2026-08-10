"""Asymmetric stateless auth for the mobility service.

Trust model (identical to commerce + the InSAR read app): we verify weespas-minted RS256
tokens with the PUBLIC key only. Mobility can CHECK a token but never MINT one, so a
compromise here leaks no signing secret that could forge a dispatch/money/identity token
against weespas. RS256-only verification is immune to the HS256 algorithm-confusion attack
by construction (we never HMAC-verify).

Fails CLOSED: when no key is configured every protected endpoint returns 503 (and main.py
refuses to boot in production). Auth is never silently off.

O(1): a signature check + dict lookups, no DB, no network. The Redis revocation denylist
(SISMEMBER) is a separate dependency, applied only to the dispatch action (chunk 4), not to
every read — mirroring commerce's ``require_settlement_principal`` split.
"""
from __future__ import annotations

from dataclasses import dataclass

import jwt  # PyJWT
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from PE.mobility.core.config import settings

# auto_error=False so a MISSING header reaches us as None — we choose the response (503 when
# auth is unconfigured, 401 when configured but absent).
_bearer = HTTPBearer(auto_error=False)

# Always require these claims to be present + valid. PyJWT enforces `exp` expiry when present;
# requiring it means a token minted without an exp is refused.
_DECODE_OPTIONS = {"require": ["exp", "sub"]}

# The granular scope a driver token must carry to be DISPATCHABLE (doc §16: the matcher pings
# `verified` drivers only). Weespas grants this only to a KYC-passed driver; the matcher checks
# for it before ever publishing a ride to that driver's channel (chunk 4). A rider token (which
# only listens + requests rides) never carries it.
DISPATCH_ELIGIBLE_SCOPE = "dispatch:eligible"


@dataclass(frozen=True)
class MobilityPrincipal:
    """The authenticated caller, derived entirely from signed claims — no DB load. ``scopes``
    carries granular permissions plus the audience scope string itself."""
    sub: str
    role: str
    scopes: tuple[str, ...]
    # Self-asserted display name from the token's ``name`` claim (weespas owns identity; mobility
    # only snapshots this for display). "" when absent — NEVER an auth input.
    name: str = ""

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


def _extract_scopes(payload: dict) -> tuple[str, ...]:
    """Accept either a granular ``scopes`` array or the singular ``scope`` string. Always includes
    the singular scope so the audience guard below can match it even on a scopes[]-only token."""
    scopes: list[str] = []
    raw_list = payload.get("scopes")
    if isinstance(raw_list, (list, tuple)):
        scopes.extend(str(s) for s in raw_list)
    single = payload.get("scope")
    if single:
        scopes.append(str(single))
    return tuple(dict.fromkeys(scopes))  # de-dup, order-preserving


def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> MobilityPrincipal:
    """Verify the RS256 mobility token and return the principal. Fails closed."""
    if not settings.auth_enabled:
        # No public key configured → refuse rather than serve unauthenticated.
        raise HTTPException(status_code=503, detail="Auth is not configured")

    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.mobility_jwt_public_key,
            algorithms=[settings.mobility_jwt_algorithm],  # RS256 only — no HMAC path
            options=_DECODE_OPTIONS,
        )
    except jwt.PyJWTError:
        # Bad signature, expired, malformed, missing required claim — all collapse to 401.
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    scopes = _extract_scopes(payload)
    # Audience guard: a correctly-signed token of the WRONG kind — a commerce_trade token, an
    # insar_telemetry token, or a weespas access token — must not authenticate against mobility.
    # Only a mobility_dispatch-scoped token may.
    if settings.mobility_jwt_scope not in scopes:
        raise HTTPException(status_code=401, detail="Token is not mobility-scoped")

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Invalid token")

    name = payload.get("name")
    return MobilityPrincipal(
        sub=str(sub),
        role=str(payload.get("role", "user")),
        scopes=scopes,
        name=str(name) if name else "",
    )


def require_scope(scope: str):
    """Factory for a dependency that requires a specific granular permission on top of a valid
    mobility token."""

    def dependency(principal: MobilityPrincipal = Depends(get_current_principal)) -> MobilityPrincipal:
        if not principal.has_scope(scope):
            raise HTTPException(status_code=403, detail="Insufficient scope")
        return principal

    return dependency
