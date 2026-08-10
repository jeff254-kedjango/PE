"""Asymmetric stateless auth for the commerce service.

Trust model (mirrors the InSAR read app's verifier): we verify weespas-minted RS256
tokens with the PUBLIC key only. Commerce can CHECK a token but never MINT one, so a
compromise here leaks no signing secret that could forge a money/identity token against
weespas. RS256-only verification is immune to the HS256 algorithm-confusion attack by
construction (we never HMAC-verify) — S3.

Divergence from InSAR (S1): commerce FAILS CLOSED. InSAR's data API may run public until
a key is provisioned; commerce carries trade identity, so when no key is configured every
protected endpoint returns 503 (and main.py refuses to boot in production). Auth is never
silently off.

O(1): a signature check + dict lookups, no DB, no network. The revocation denylist
(SISMEMBER) is intentionally NOT here — it belongs only on money/settlement actions, which
are out of scope this increment; the inert hook below marks where it lands (S5).
"""
from __future__ import annotations

from dataclasses import dataclass

import jwt  # PyJWT
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from PE.commerce.core.config import settings

# auto_error=False so a MISSING header reaches us as None — we choose the response
# (503 when auth is unconfigured, 401 when configured but absent).
_bearer = HTTPBearer(auto_error=False)

# Always require these claims to be present + valid. PyJWT enforces `exp` expiry when
# present; requiring it means a token minted without an exp is refused.
_DECODE_OPTIONS = {"require": ["exp", "sub"]}


@dataclass(frozen=True)
class CommercePrincipal:
    """The authenticated caller, derived entirely from signed claims — no DB load.
    ``scopes`` carries granular permissions (e.g. "read:feed", "create:trades") plus the
    audience scope string itself."""
    sub: str
    role: str
    scopes: tuple[str, ...]
    # Self-asserted display name from the token's ``name`` claim (weespas owns identity; commerce
    # only snapshots this onto comments/inquiries for display). "" when absent — NEVER an auth input.
    name: str = ""

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


def _extract_scopes(payload: dict) -> tuple[str, ...]:
    """Accept either a granular ``scopes`` array or the singular ``scope`` string (parity
    with the telemetry verifier's simple-equality model). Always includes the singular
    scope so the audience guard below can match it even on a scopes[]-only token."""
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
) -> CommercePrincipal:
    """Verify the RS256 commerce token and return the principal. Fails closed."""
    if not settings.auth_enabled:
        # No public key configured → refuse rather than serve unauthenticated (S1).
        raise HTTPException(status_code=503, detail="Auth is not configured")

    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.commerce_jwt_public_key,
            algorithms=[settings.commerce_jwt_algorithm],  # RS256 only — no HMAC path (S3)
            options=_DECODE_OPTIONS,
        )
    except jwt.PyJWTError:
        # Bad signature, expired, malformed, missing required claim — all collapse to 401.
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    scopes = _extract_scopes(payload)
    # Audience guard (S2): a correctly-signed token of the WRONG kind — a weespas access
    # token (were it RS256), an insar_telemetry token, or a future mobility token — must
    # not authenticate against commerce. Only a commerce_trade-scoped token may.
    if settings.commerce_jwt_scope not in scopes:
        raise HTTPException(status_code=401, detail="Token is not commerce-scoped")

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Invalid token")

    name = payload.get("name")
    return CommercePrincipal(
        sub=str(sub),
        role=str(payload.get("role", "user")),
        scopes=scopes,
        name=str(name) if name else "",
    )


def require_scope(scope: str):
    """Factory for a dependency that requires a specific granular permission (e.g.
    "create:trades"). Used by DEFERRED write endpoints; reads/feed only require the
    audience scope via get_current_principal."""

    def dependency(principal: CommercePrincipal = Depends(get_current_principal)) -> CommercePrincipal:
        if not principal.has_scope(scope):
            raise HTTPException(status_code=403, detail="Insufficient scope")
        return principal

    return dependency


def require_staff(
    principal: CommercePrincipal = Depends(get_current_principal),
) -> CommercePrincipal:
    """Staff-only gate: the token's ``role`` claim must be in ``settings.admin_roles`` (default
    "staff,admin"). Used by platform-moderation endpoints — approving a per-shop sponsored-cap
    override (§8.3 item 1), etc. — that are gated on WHO you are, not just a granular scope.

    Fails CLOSED: a non-staff caller → 403, and an empty admin_roles config admits NO ONE (a blank
    allow-set is an intentional lockout, not a bypass). The comparison is case-insensitive (roles
    are normalised lower-case in admin_roles_tuple)."""
    if principal.role.strip().lower() not in settings.admin_roles_tuple:
        raise HTTPException(status_code=403, detail="Staff role required")
    return principal


def require_settlement_principal(
    principal: CommercePrincipal = Depends(get_current_principal),
) -> CommercePrincipal:
    """Money-action gate (S5): on top of a valid token, an O(1) Redis denylist check stops a
    banned/fraud actor IMMEDIATELY (stateless tokens can't be revoked before TTL — §2).

    Fails CLOSED: a denied actor → 403; an unreachable denylist → 503 (refuse, never admit).
    Only money/settlement endpoints depend on this; reads/feed/saves never call it. Imported
    lazily so the denylist's redis client is only constructed on a money path."""
    from PE.commerce.services.denylist import DenylistUnavailable, is_denied

    try:
        denied = is_denied(principal.sub)
    except DenylistUnavailable:
        raise HTTPException(status_code=503, detail="Settlement temporarily unavailable")
    if denied:
        raise HTTPException(status_code=403, detail="Account is not permitted to transact")
    return principal
