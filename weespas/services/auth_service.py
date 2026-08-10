from datetime import datetime, timedelta, timezone
from typing import Optional
import logging
import random
import string

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from sqlalchemy import func as sql_func, or_

from PE.weespas.core.config import settings
from PE.weespas.core.database import get_db
from PE.weespas.models.user import User, UserRole
from PE.weespas.schemas.auth import RegisterRequest, LoginRequest, OtpVerifyRequest, UserResponse, AuthResponse
from PE.weespas.services.sms_service import send_otp as sms_send_otp
from PE.weespas.services.celery_helpers import safe_delay, redis_setnx_lock

logger = logging.getLogger(__name__)

# In-memory rate limit store: phone -> list of UTC timestamps
_otp_rate_limit: dict[str, list[datetime]] = {}
OTP_MAX_REQUESTS = 3
OTP_RATE_WINDOW = timedelta(minutes=15)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()
# Optional bearer — never raises on missing or malformed Authorization header.
optional_security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: str, role: str = "user") -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": user_id, "role": role, "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


# A telemetry-scoped token can do exactly ONE thing: append best-effort metering
# rows for its own ``sub`` via POST /insar-telemetry/event. It is handed to the
# stateless InSAR frontend on deep-link and replayed in the URL, so its blast
# radius is deliberately near-zero. The matching reject guards in
# get_current_user / get_current_user_optional ensure it can NEVER be replayed
# against a money (/reveal) or PII (/policy/me) endpoint.
INSAR_TELEMETRY_SCOPE = "insar_telemetry"


def create_insar_telemetry_token(user_id: str, role: str = "user") -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.insar_telemetry_token_ttl_min)
    payload = {"sub": user_id, "role": role, "scope": INSAR_TELEMETRY_SCOPE, "exp": expire}
    # Sign RS256 with the private key once it's provisioned, so the InSAR read app can verify
    # with the PUBLIC key alone (and never mint). Until a key is configured, fall back to
    # HS256 so the rollout can land the dual-verify path before keys exist. _decode_token
    # accepts both, so in-flight HS256 tokens stay valid through the cutover.
    if settings.insar_jwt_rs256_enabled:
        return jwt.encode(payload, settings.insar_jwt_private_key, algorithm=settings.insar_jwt_algorithm)
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


# A commerce-scoped token authenticates a signed-in user against the separate commerce
# service (the trading layer's social marketplace, :8003). Commerce verifies it with the
# PUBLIC key only — it can check but never mint. The audience scope string is distinct
# from INSAR_TELEMETRY_SCOPE so neither token type can be replayed against the other
# service, and the reject guards in get_current_user / get_current_user_optional ensure a
# commerce token can NEVER authenticate a Weespas endpoint.
COMMERCE_TRADE_SCOPE = "commerce_trade"

# Scopes that belong to OTHER services and must never authenticate a Weespas endpoint.
# Centralized so adding a future service scope (e.g. mobility) updates both reject guards
# in get_current_user / get_current_user_optional at once.
_FOREIGN_SCOPES = frozenset({INSAR_TELEMETRY_SCOPE, COMMERCE_TRADE_SCOPE})


def create_commerce_token(
    user_id: str, role: str = "user", scopes: list[str] | None = None, name: str | None = None
) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.commerce_token_ttl_min)
    payload = {
        "sub": user_id,
        "role": role,
        "scope": COMMERCE_TRADE_SCOPE,
        "scopes": scopes or ["read:feed"],
        "exp": expire,
    }
    # The caller's display name, carried as a claim so commerce can SNAPSHOT it onto comments/
    # inquiries at write time (commerce owns no identity and never queries the weespas DB). Optional
    # and self-asserted: it's a display convenience, never an authorization input.
    if name:
        payload["name"] = name
    # Reuse the existing RS256 keypair + rollout gate: signs RS256 once the private key is
    # provisioned, else HS256 dev fallback. So this minter ships INERT (HS256, commerce's
    # RS256-only verifier won't accept it) until keys land — exactly like the telemetry minter.
    if settings.insar_jwt_rs256_enabled:
        return jwt.encode(payload, settings.insar_jwt_private_key, algorithm=settings.insar_jwt_algorithm)
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def _decode_token(token: str) -> dict:
    """Decode + verify a Weespas-issued JWT, accepting BOTH HS256 (access tokens and
    legacy telemetry tokens) and RS256 (new telemetry tokens) during the RS256 rollout.

    Why branch on the token's own header ``alg`` instead of passing
    ``algorithms=["HS256","RS256"]`` with one key: that combo enables the classic JWT
    algorithm-confusion attack — an attacker signs an HS256 token using the RSA *public*
    key (which is, well, public) as the HMAC secret, and a verifier that accepts both with
    the public key on hand would treat it as valid. By reading the unverified header,
    picking exactly one (key, alg) pair, and verifying with only that, HS256 is always
    checked against the private HMAC secret and RS256 only against the RSA public key —
    the public key is never usable as an HMAC secret. Raises JWTError on any failure
    (callers translate that to 401/None as they already did)."""
    header = jwt.get_unverified_header(token)
    alg = header.get("alg")
    if alg == "RS256":
        pub = settings.insar_jwt_public_key
        if not pub:
            # An RS256 token arrived but no public key is configured to check it — refuse
            # rather than fall through to the HMAC secret (which would never verify anyway).
            raise JWTError("RS256 token received but no public key configured")
        return jwt.decode(token, pub, algorithms=["RS256"])
    if alg == "HS256":
        return jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    raise JWTError(f"unsupported token algorithm: {alg!r}")


def _build_auth_response(user: User) -> dict:
    role_value = user.role.value if isinstance(user.role, UserRole) else user.role
    roles_value = list(user.roles) if user.roles else ([role_value] if role_value else [])
    return {
        "token": create_access_token(user.id, role_value),
        "user": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "phone": user.phone,
            "avatar": user.avatar,
            "role": role_value,
            "roles": roles_value,
            "agent_id": user.agent_id,
            "is_public_profile": user.is_public_profile,
            # Phase 6 — notification prefs (surfaced so clients can render
            # the toggles without an extra round-trip after login).
            "notify_inquiries_sms": getattr(user, "notify_inquiries_sms", True),
            "notify_inquiries_email": getattr(user, "notify_inquiries_email", False),
            "notify_digest_email": getattr(user, "notify_digest_email", False),
            "notify_push": getattr(user, "notify_push", False),
            # Phase 8 — search defaults
            "default_radius_km": getattr(user, "default_radius_km", 10),
            "preferred_listing_type": getattr(user, "preferred_listing_type", None),
            "language": getattr(user, "language", "en"),
            "created_at": user.created_at,
        },
    }


def register_user(db: Session, data: RegisterRequest) -> dict:
    # Active-only conflict checks: a soft-deleted row (is_active=False) that
    # still owns this email or phone must NOT block re-registration, because
    # login also ignores inactive rows (`is_active == True` everywhere in
    # this module). Without this, the user is wedged: register says
    # "already registered", login says "no account found".
    #
    # Two unioned existence checks instead of two separate queries — one
    # round-trip, indexed on `email` and `phone` so it's ~sub-ms.
    conflict = (
        db.query(User.email, User.phone)
        .filter(
            User.is_active == True,  # noqa: E712
            or_(User.email == data.email, User.phone == data.phone),
        )
        .first()
    )
    if conflict is not None:
        # Disambiguate which field collided so the client renders the right
        # message. Email match wins the report when both match (rare).
        if conflict[0] == data.email:
            raise HTTPException(status_code=400, detail="Email already registered")
        raise HTTPException(status_code=400, detail="Phone number already registered")

    # Inactive rows still own the unique (email, phone) at the DB level.
    # Resurrect the existing row instead of INSERTing: keeps the user_id
    # stable (so the soft-deletion audit trail in DeletionRequest still
    # points at a real user) and avoids an IntegrityError on the unique
    # index. Falls through to a fresh INSERT when no inactive ghost exists.
    ghost = (
        db.query(User)
        .filter(
            User.is_active == False,  # noqa: E712
            or_(User.email == data.email, User.phone == data.phone),
        )
        .first()
    )
    if ghost is not None:
        ghost.name = data.name
        ghost.email = data.email
        ghost.phone = data.phone
        ghost.hashed_password = hash_password(data.password)
        ghost.is_active = True
        # Wipe any pending contact-change OTP state left over from the prior
        # life of the row so it can't be confirmed across the resurrection.
        ghost.pending_phone = None
        ghost.pending_email = None
        ghost.pending_contact_otp_hash = None
        ghost.pending_contact_expires_at = None
        ghost.pending_contact_kind = None
        db.commit()
        db.refresh(ghost)
        logger.info("auth.register.resurrected user_id=%s", ghost.id)
        return _build_auth_response(ghost)

    user = User(
        name=data.name,
        email=data.email,
        phone=data.phone,
        hashed_password=hash_password(data.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _build_auth_response(user)


def _check_otp_rate_limit(phone: str) -> None:
    """Raise 429 if phone has exceeded OTP_MAX_REQUESTS in the rate window."""
    now = datetime.now(timezone.utc)
    cutoff = now - OTP_RATE_WINDOW
    timestamps = [t for t in _otp_rate_limit.get(phone, []) if t > cutoff]
    if len(timestamps) >= OTP_MAX_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail="Too many OTP requests. Try again in 15 minutes.",
        )
    timestamps.append(now)
    _otp_rate_limit[phone] = timestamps


def _generate_and_send_otp(db: Session, user: User) -> dict:
    """Generate a 6-digit OTP, persist it, and send via SMS."""
    _check_otp_rate_limit(user.phone)

    otp = "".join(random.choices(string.digits, k=6))
    user.otp_code = otp
    user.otp_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    db.commit()

    if settings.debug:
        logger.info("DEBUG OTP for %s: %s", user.phone, otp)

    # Phase 1.1: offload the Africa's Talking REST call. The OTP row is
    # already committed, so the response can return immediately and we
    # never block on AT's infrastructure. A 30s SETNX dedupes bursts from
    # the resend button so we don't fire two SMS for the same code.
    if settings.celery_send_otp_enabled and redis_setnx_lock(
        f"otp:sent:{user.phone}", 30
    ):
        from PE.weespas.services import auth_tasks  # local import — avoids circular at module load
        safe_delay(auth_tasks.send_otp, user.phone, otp)
    elif settings.celery_send_otp_enabled:
        # Lock taken → another worker is already sending. Skip the dispatch
        # silently; the OTP row is still valid and the user got their code.
        pass
    else:
        sms_send_otp(user.phone, otp)

    result: dict = {"message": "OTP sent to your phone", "otp_sent": True}
    if settings.debug:
        result["otp"] = otp  # Only exposed in dev — never in production
    return result


def login_user(db: Session, data: LoginRequest) -> dict:
    user: Optional[User] = None

    if data.email and data.password:
        user = db.query(User).filter(User.email == data.email.lower(), User.is_active == True).first()
        if not user or not verify_password(data.password, user.hashed_password):
            raise HTTPException(status_code=401, detail="Invalid email or password")
    elif data.phone and data.password:
        user = db.query(User).filter(User.phone == data.phone, User.is_active == True).first()
        if not user or not verify_password(data.password, user.hashed_password):
            raise HTTPException(status_code=401, detail="Invalid phone or password")
    elif data.phone:
        user = db.query(User).filter(User.phone == data.phone, User.is_active == True).first()
        if not user:
            raise HTTPException(status_code=404, detail="No account found with this phone number")
        return _generate_and_send_otp(db, user)
    else:
        raise HTTPException(status_code=400, detail="Provide email+password, phone+password, or phone for OTP")

    return _build_auth_response(user)


def resend_otp(db: Session, phone: str) -> dict:
    """Generate a fresh OTP and send it, invalidating any previous code."""
    user = db.query(User).filter(User.phone == phone, User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=404, detail="No account found with this phone number")
    return _generate_and_send_otp(db, user)


def verify_otp(db: Session, data: OtpVerifyRequest) -> dict:
    user = db.query(User).filter(User.phone == data.phone, User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=404, detail="No account found with this phone number")

    if not user.otp_code or user.otp_code != data.otp:
        raise HTTPException(status_code=401, detail="Invalid OTP code")

    if user.otp_expires_at and user.otp_expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="OTP has expired")

    # Clear OTP after successful verification
    user.otp_code = None
    user.otp_expires_at = None
    db.commit()
    db.refresh(user)
    return _build_auth_response(user)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    token = credentials.credentials
    try:
        payload = _decode_token(token)
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        # A cross-service-scoped token (telemetry or commerce) must never authenticate a
        # normal request. The telemetry token may only reach POST /insar-telemetry/event;
        # the commerce token belongs to the :8003 service only. Reject both everywhere
        # else so a token leaked in a deep-link URL can't spend money or read PII.
        if payload.get("scope") in _FOREIGN_SCOPES:
            raise HTTPException(status_code=401, detail="Invalid token scope")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    # Throttled presence touch: write last_seen_at at most once per 60s per user.
    # When the offload flag is on, dispatch to Celery instead of committing
    # inline — removes one DB write from every authed request. The Redis
    # SETNX is the source of truth for the throttle; the prev-timestamp
    # check stays as a belt-and-suspenders for when Redis is missing.
    try:
        if settings.celery_last_seen_enabled:
            if redis_setnx_lock(f"touch:{user.id}", 60):
                from PE.weespas.services import auth_tasks  # local import
                safe_delay(auth_tasks.touch_last_seen, user.id)
        else:
            now = datetime.now(timezone.utc)
            prev = user.last_seen_at
            if prev is None or (now - prev) > timedelta(seconds=60):
                user.last_seen_at = now
                db.commit()
    except Exception as e:
        logger.warning("last_seen_at touch failed for %s: %s", user.id, e)
        try:
            db.rollback()
        except Exception:
            pass

    return user


def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(optional_security),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Best-effort authentication for endpoints that personalize when signed in
    but stay reachable to anonymous callers. Never raises on bad or missing
    tokens — returns ``None``. Cheap path: no last_seen_at write, no extra
    queries beyond the user lookup.
    """
    if credentials is None:
        return None
    try:
        payload = _decode_token(credentials.credentials)
    except JWTError:
        return None
    # Same reject as get_current_user: a cross-service-scoped token (telemetry or
    # commerce) is not an identity here (it would otherwise quietly personalize
    # money/feed endpoints).
    if payload.get("scope") in _FOREIGN_SCOPES:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    return db.query(User).filter(User.id == user_id, User.is_active == True).first()


def require_insar_telemetry_token(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    """Dependency for the InSAR telemetry endpoint ONLY. Verifies a token minted by
    ``create_insar_telemetry_token`` and returns its ``sub`` (the user id) — with
    NO database load, so the metering hot path stays O(1) and resilient even if the
    user row is mid-mutation. The ``sub`` is signed, so it is unforgeable; rejecting
    any token that lacks the telemetry scope keeps normal access tokens out of here.
    """
    try:
        payload = _decode_token(credentials.credentials)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if payload.get("scope") != INSAR_TELEMETRY_SCOPE:
        raise HTTPException(status_code=401, detail="Token is not telemetry-scoped")
    # Post-cutover: optionally pin telemetry tokens to RS256. Access tokens (also HS256)
    # are unaffected — this guard is scoped to the telemetry path only, AFTER the scope
    # check above. Until the flag is set, legacy HS256 telemetry tokens still pass.
    if settings.insar_telemetry_require_rs256:
        alg = jwt.get_unverified_header(credentials.credentials).get("alg")
        if alg != "RS256":
            raise HTTPException(status_code=401, detail="Telemetry token must be RS256")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user_id


# ===================== PERMISSION DEPENDENCIES =====================

def require_role(*allowed_roles):
    """Factory that returns a FastAPI dependency requiring any of the given roles.

    Accepts either UserRole members or their string values. Checks the user's
    `roles` list (multi-role) and falls back to `user.role` for users not yet
    backfilled.
    """
    allowed = frozenset(
        r.value if isinstance(r, UserRole) else r for r in allowed_roles
    )

    def dependency(current_user: User = Depends(get_current_user)) -> User:
        user_roles = set(current_user.roles or [])
        if not user_roles and current_user.role:
            user_roles = {current_user.role.value}
        if user_roles.isdisjoint(allowed):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions"
            )
        return current_user
    return dependency


require_agent = require_role(UserRole.AGENT, UserRole.STAFF, UserRole.ADMIN)
require_staff = require_role(UserRole.STAFF, UserRole.ADMIN)
require_admin = require_role(UserRole.ADMIN)
# P4a: who may record a structural flag (the second-sensor input). A professional
# (engineer) or an authority records the judgement; staff/admin may on their behalf.
require_certifier = require_role(
    UserRole.PROFESSIONAL, UserRole.AUTHORITY, UserRole.STAFF, UserRole.ADMIN
)


def verify_property_ownership(user: User, property_obj) -> None:
    """Raise 403 if the user is not the owning agent. Admins bypass."""
    if user.has_role(UserRole.ADMIN):
        return
    if not user.agent_id or property_obj.agent_id != user.agent_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only modify your own properties"
        )
