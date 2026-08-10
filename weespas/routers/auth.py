from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from PE.weespas.core.database import get_db
import logging

from pydantic import BaseModel, Field

from PE.weespas.schemas.auth import RegisterRequest, LoginRequest, OtpVerifyRequest, ResendOtpRequest, AuthResponse, UserResponse, UserUpdateRequest
from PE.weespas.services.auth_service import (
    register_user,
    login_user,
    verify_otp,
    resend_otp,
    get_current_user,
    hash_password,
    verify_password,
)
from PE.weespas.models.user import User
from PE.weespas.models.analytics import UserSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=201,
    summary="Register a new user",
)
def register(data: RegisterRequest = Body(...), db: Session = Depends(get_db)):
    return register_user(db, data)


@router.post(
    "/login",
    summary="Login with email/password, phone/password, or request OTP",
)
def login(data: LoginRequest = Body(...), db: Session = Depends(get_db)):
    return login_user(db, data)


@router.post(
    "/verify-otp",
    response_model=AuthResponse,
    summary="Verify OTP code sent to phone",
)
def otp_verify(data: OtpVerifyRequest = Body(...), db: Session = Depends(get_db)):
    return verify_otp(db, data)


@router.post(
    "/resend-otp",
    summary="Resend OTP to phone (rate-limited: 3 per 15 min)",
)
def resend(data: ResendOtpRequest = Body(...), db: Session = Depends(get_db)):
    return resend_otp(db, data.phone)


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current authenticated user",
)
def me(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Bio lives on agents.bio, not users.*. For agents we fetch just the
    # one column with a PK-indexed scalar query — sub-ms, single round-trip,
    # only paid for users who actually have an agent_id (small fraction of
    # the user base). Non-agents skip the query entirely. Attached to the
    # ORM instance so Pydantic's from_attributes serializer picks it up
    # via the new `bio` field on UserResponse.
    bio_value: str | None = None
    if current_user.agent_id:
        from PE.weespas.models.property import Agent  # local import — avoids module-load JOIN
        bio_value = (
            db.query(Agent.bio)
            .filter(Agent.id == current_user.agent_id)
            .scalar()
        )
    current_user.bio = bio_value  # type: ignore[attr-defined]
    return current_user


@router.patch(
    "/me",
    response_model=UserResponse,
    summary="Partial update of the authenticated user's profile",
)
def update_me(
    data: UserUpdateRequest = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    payload = data.dict(exclude_unset=True)
    if not payload:
        return current_user

    for field, value in payload.items():
        setattr(current_user, field, value)

    db.commit()
    db.refresh(current_user)

    # Mirror the bio onto the response so this PATCH and GET /me return the
    # same shape. Same single-column scalar query used in `me()` above —
    # only runs for agents.
    bio_value: str | None = None
    if current_user.agent_id:
        from PE.weespas.models.property import Agent  # local import
        bio_value = (
            db.query(Agent.bio)
            .filter(Agent.id == current_user.agent_id)
            .scalar()
        )
    current_user.bio = bio_value  # type: ignore[attr-defined]

    # Note on cache invalidation: the agent_rank / agent-directory caches use
    # global per-window keys (analytics:agent_rank:{since}) with a 1h TTL, so
    # we don't bust on every name change — that would thrash the cache for
    # every user on the platform. Stale display names self-heal within an
    # hour, which is acceptable for a non-correctness-critical surface.

    logger.info(
        "user.profile.updated user_id=%s fields=%s",
        current_user.id,
        sorted(payload.keys()),
    )
    return current_user


# ────────────────────────────────────────────────────────────────────
# Phase 7 — change password
# ────────────────────────────────────────────────────────────────────
class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=6, max_length=128)


@router.post(
    "/change-password",
    summary="Change the authenticated user's password and revoke other sessions",
)
def change_password(
    request: Request,
    body: ChangePasswordRequest = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(body.old_password, current_user.hashed_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    if body.old_password == body.new_password:
        raise HTTPException(status_code=400, detail="New password must differ from the old one")

    current_user.hashed_password = hash_password(body.new_password)

    # Revoke every other session — the assumption is that a password change
    # is either intentional (user wants other devices signed out) or
    # defensive (account is compromised). Keep the current session row so
    # the user isn't kicked out of the request that just mutated state.
    current_sid = getattr(request.state, "session_id", None)
    q = db.query(UserSession).filter(UserSession.user_id == current_user.id)
    if current_sid:
        q = q.filter(UserSession.id != current_sid)
    q.delete(synchronize_session=False)

    db.commit()
    logger.info("user.password.changed user_id=%s", current_user.id)
    return {"ok": True}
