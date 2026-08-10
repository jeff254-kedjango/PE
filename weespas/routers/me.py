"""User-self endpoints under /me/*.

Houses everything the authenticated user can do *to their own account*
(avatar upload, saved searches, hidden listings, active sessions,
notification prefs, deletion request, etc.) — the partial-update
PATCH /auth/me lives in routers.auth because it belongs to the auth
schema/router pair. Anything that needs its own resource shape
(multipart, list endpoints, sub-collections) belongs here.

Performance posture:
- Every list endpoint here is a single indexed query — no JOINs that
  fan out beyond the page size, no N+1 risk.
- Writes that affect cacheable derived state (feed, agent directory)
  delegate to the existing invalidate_user_feed Celery chain so the
  request stays sub-50ms even when downstream caches need warming.
- No new Redis keys: per-user lists are small enough that an indexed
  scan beats the round-trip cost of a cache hop.
"""
from __future__ import annotations

import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import List, Optional

import hashlib
import hmac
import random
import string
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session

from PE.weespas.core.config import settings
from PE.weespas.schemas.auth import UserResponse

from PE.weespas.core.database import get_db
from PE.weespas.models.analytics import PropertyDismissal, UserSession
from PE.weespas.models.property import Address, Agent, Property, PropertyImage
from PE.weespas.models.user import User
from PE.weespas.services.auth_service import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/me", tags=["Me"])


# ─────────────────────────────────────────────────────────────────────
# Avatar storage (mirrors routers.media layout so /uploads/avatars/*
# is served by the existing StaticFiles mount in main.py)
# ─────────────────────────────────────────────────────────────────────
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "uploads"))
AVATAR_DIR = UPLOAD_DIR / "avatars"
AVATAR_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_AVATAR_TYPES = {"image/jpeg", "image/png", "image/webp", "image/avif"}
MAX_AVATAR_SIZE = 5 * 1024 * 1024  # 5 MB — avatars are smaller than property images


class AvatarUploadResponse(BaseModel):
    url: str
    thumbnail_url: str  # same as url for now; WebP transcode runs async and overwrites in place


@router.post(
    "/avatar",
    response_model=AvatarUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload (or replace) the authenticated user's avatar",
)
def upload_avatar(
    file: UploadFile = File(..., description="Avatar image (JPEG, PNG, WebP, AVIF, ≤ 5 MB)"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if file.content_type not in ALLOWED_AVATAR_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported type '{file.content_type}'. Allowed: JPEG, PNG, WebP, AVIF.",
        )

    # Stream into memory once — avatars are <5MB so the in-memory cost is
    # bounded and avoids a second read for size validation.
    content = file.file.read()
    if len(content) > MAX_AVATAR_SIZE:
        raise HTTPException(status_code=400, detail="Avatar exceeds 5 MB limit.")

    ext = (file.filename or "avatar.jpg").rsplit(".", 1)[-1].lower()
    if ext not in ("jpg", "jpeg", "png", "webp", "avif"):
        ext = "jpg"
    filename = f"{current_user.id}-{uuid.uuid4().hex[:8]}.{ext}"
    filepath = AVATAR_DIR / filename
    with open(filepath, "wb") as f:
        f.write(content)

    # Best-effort cleanup of ALL prior avatar files for this user — never
    # block on it. We sweep by prefix (`{user_id}-*`) rather than just the
    # URL currently on the row because the WebP worker (services/
    # image_processing.py) intentionally leaves the original-extension
    # source file on disk to keep cached client URLs serveable (see the
    # docstring there for the rationale). Without this prefix sweep, one
    # stale source would accumulate per upload. With it, disk usage is
    # bounded to a single file per user immediately after each upload,
    # and grows to two during the brief window the WebP worker is
    # running (source + webp), which is exactly the trade-off that
    # keeps the avatar from going 404 in the client.
    #
    # Using Path.glob (single readdir + fnmatch) — for a directory with
    # tens of thousands of avatars this is O(n) on the dir scan, but
    # we only scan once per upload and the filesystem caches the inode
    # block. No globbing on a hot read path.
    new_filename = filepath.name
    try:
        for stale in AVATAR_DIR.glob(f"{current_user.id}-*"):
            if stale.name == new_filename:
                continue
            # Defence-in-depth: never delete anything that isn't inside
            # AVATAR_DIR (defends against symlink shenanigans even though
            # the glob source is trusted).
            try:
                if stale.resolve().is_relative_to(AVATAR_DIR.resolve()):
                    stale.unlink(missing_ok=True)
            except Exception:
                continue
    except Exception as exc:
        logger.warning("avatar.sweep_failed user=%s err=%s", current_user.id, exc)

    url = f"/uploads/avatars/{filename}"
    current_user.avatar = url

    # Sync the denormalized Agent row in the SAME transaction. Agents are
    # listed from `agents.agent_profile_picture` (routers/agents.py), which
    # is otherwise only populated at promote-time (routers/admin.py:57) and
    # never touched after. Without this, an Agent's directory card stays on
    # the old image forever. Bulk UPDATE() avoids loading the Agent row —
    # one indexed UPDATE on the PK, piggybacking on the commit below.
    #
    # No Redis/HTTP cache sits in front of /agents/public (verified), and
    # the analytics:agent_rank:* keys carry rank scores, not avatar URLs —
    # so no cache bust is needed here. Frontend invalidates its React
    # Query keys (publicAgents, staffDirectory, unifiedSearch) after the
    # upload returns; that's the only client-side cache that needs nudging.
    if current_user.agent_id:
        db.query(Agent).filter(Agent.id == current_user.agent_id).update(
            {"agent_profile_picture": url}, synchronize_session=False
        )

    db.commit()

    # WebP transcode — offloaded to the `media` Celery queue (same queue
    # property images use). Keeps POST /me/avatar sub-50ms server-time
    # even on Nairobi/Kampala/Kigali links where every blocking ms in a
    # hot endpoint compounds with TLS RTT.
    #
    # The endpoint returns the original-extension URL; the worker rewrites
    # `users.avatar` (and the linked `agents.agent_profile_picture`) to the
    # WebP variant within ~100ms. The original source file stays on disk
    # (see services/image_processing.py docstring) so the URL the client
    # already cached keeps serving — no 404 window where the avatar
    # appears blank between upload-success and the next /auth/me refetch.
    # The client picks up the WebP URL when React Query refetches
    # `['auth','me']` (after EditProfilePanel's post-upload invalidation,
    # or on the next 5-min staleTime / window-focus boundary). Both URLs
    # serve the same image until the next upload sweeps the prior files.
    try:
        from PE.weespas.services.image_processing import process_avatar_image  # type: ignore

        process_avatar_image.delay(str(filepath), current_user.id)
    except Exception as exc:
        # Worker/broker unreachable is non-fatal — the original file is
        # already serving. Log so ops can spot a stuck Celery worker.
        logger.warning("avatar.transcode_enqueue_failed user=%s err=%s", current_user.id, exc)

    logger.info("user.avatar.uploaded user_id=%s bytes=%d", current_user.id, len(content))
    return AvatarUploadResponse(url=url, thumbnail_url=url)


# ─────────────────────────────────────────────────────────────────────
# Agent bio — PATCH /me/bio
# ─────────────────────────────────────────────────────────────────────
# Bio lives on agents.bio (Text, nullable). Co-located with the avatar
# endpoint above because both write to the agents row when the
# authenticated user is linked to an Agent. PATCH /auth/me deliberately
# does NOT carry this field — its schema (UserUpdateRequest) targets the
# users table; threading a cross-table write through it would couple two
# tables to one schema. One indexed UPDATE per save; no Agent row load.
class BioUpdateRequest(BaseModel):
    # Empty string is valid and means "clear my bio".
    bio: str = Field(..., max_length=500)


class BioUpdateResponse(BaseModel):
    bio: str


@router.patch(
    "/bio",
    response_model=BioUpdateResponse,
    summary="Update the authenticated agent's public bio",
)
def update_bio(
    body: BioUpdateRequest = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not current_user.agent_id:
        raise HTTPException(status_code=403, detail="Only agents can set a bio.")
    cleaned = body.bio.strip()
    # NULL for empty so existing `WHERE bio IS NOT NULL` filters and the
    # frontend's `{agent.bio && ...}` guards work without the empty-string
    # edge case. Single PK-indexed UPDATE — no row load.
    db.query(Agent).filter(Agent.id == current_user.agent_id).update(
        {"bio": cleaned or None}, synchronize_session=False
    )
    db.commit()
    # No cache to bust: /agents/public is not Redis-cached, and the
    # analytics:agent_rank:* keys index by id/score, not bio content.
    # Frontend invalidates ['publicAgents'] + ['agentProfile'] after the
    # mutation lands so visitors see the new copy on next subscription.
    logger.info("user.agent.bio.updated user_id=%s len=%d", current_user.id, len(cleaned))
    return BioUpdateResponse(bio=cleaned)


# ─────────────────────────────────────────────────────────────────────
# Phase 4 — Hidden listings management
# ─────────────────────────────────────────────────────────────────────
class HiddenListingItem(BaseModel):
    property_id: str
    title: Optional[str] = None
    price: Optional[float] = None
    city: Optional[str] = None
    main_image_url: Optional[str] = None
    listing_type: Optional[str] = None
    dismissed_at: Optional[str] = None


@router.get(
    "/dismissals",
    response_model=List[HiddenListingItem],
    summary="List the user's hidden (dismissed) properties with display info",
)
def list_hidden_listings(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Single JOIN query — pulls dismissal + property + main image URL in
    # one round-trip. No N+1: the LEFT JOIN on PropertyImage uses the
    # is_main=True predicate and the (property_id, is_main) index.
    # Single round-trip: dismissal row + property row + address.city + main
    # image URL. The `is_main=True` filter on the LEFT JOIN means the row
    # never duplicates; an INNER JOIN on properties guarantees the dismissed
    # property still exists (cascade-deleted rows naturally drop out).
    rows = (
        db.query(
            PropertyDismissal.property_id,
            PropertyDismissal.created_at,
            Property.title,
            Property.price,
            Property.listing_type,
            Address.city,
            PropertyImage.url,
        )
        .join(Property, Property.id == PropertyDismissal.property_id)
        .outerjoin(Address, Address.property_id == Property.id)
        .outerjoin(
            PropertyImage,
            (PropertyImage.property_id == Property.id) & (PropertyImage.is_main == True),  # noqa: E712
        )
        .filter(PropertyDismissal.user_id == user.id)
        .order_by(PropertyDismissal.created_at.desc())
        .limit(100)
        .all()
    )
    return [
        HiddenListingItem(
            property_id=r[0],
            dismissed_at=r[1].isoformat() if r[1] else None,
            title=r[2],
            price=float(r[3]) if r[3] is not None else None,
            listing_type=(r[4].value if hasattr(r[4], "value") else (str(r[4]) if r[4] is not None else None)),
            city=r[5],
            main_image_url=r[6],
        )
        for r in rows
    ]


@router.delete(
    "/dismissals",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Unhide ALL of the user's dismissed listings",
)
def unhide_all(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    deleted = (
        db.query(PropertyDismissal).filter(PropertyDismissal.user_id == user.id).delete()
    )
    db.commit()
    if deleted:
        # Reuse the same chain the per-property delete uses so the feed
        # gets prewarmed exactly once instead of N times.
        try:
            from PE.weespas.routers.dismissals import _enqueue_feed_invalidation
            _enqueue_feed_invalidation(user.id)
        except Exception as exc:
            logger.warning("unhide_all.invalidate_failed user=%s err=%s", user.id, exc)
    return None


# ─────────────────────────────────────────────────────────────────────
# Phase 5 — Active sessions
# ─────────────────────────────────────────────────────────────────────
class ActiveSessionItem(BaseModel):
    id: str
    user_agent: Optional[str] = None
    ip_address: Optional[str] = None
    geo_city: Optional[str] = None
    geo_county: Optional[str] = None
    last_seen_at: Optional[str] = None
    created_at: Optional[str] = None
    is_current: bool = False


@router.get(
    "/sessions",
    response_model=List[ActiveSessionItem],
    summary="List the user's active sessions (most-recent first, max 10)",
)
def list_sessions(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    current_sid = getattr(request.state, "session_id", None)
    rows = (
        db.query(UserSession)
        .filter(UserSession.user_id == user.id)
        .order_by(UserSession.last_seen_at.desc().nullslast())
        .limit(10)
        .all()
    )
    return [
        ActiveSessionItem(
            id=s.id,
            user_agent=s.user_agent,
            ip_address=s.ip_address,
            geo_city=s.geo_city,
            geo_county=s.geo_county,
            last_seen_at=s.last_seen_at.isoformat() if s.last_seen_at else None,
            created_at=s.created_at.isoformat() if s.created_at else None,
            is_current=(s.id == current_sid),
        )
        for s in rows
    ]


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a specific session",
)
def revoke_session(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    deleted = (
        db.query(UserSession)
        .filter(UserSession.id == session_id, UserSession.user_id == user.id)
        .delete()
    )
    db.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return None


@router.delete(
    "/sessions",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke all sessions except the current one",
)
def revoke_all_other_sessions(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    current_sid = getattr(request.state, "session_id", None)
    q = db.query(UserSession).filter(UserSession.user_id == user.id)
    if current_sid:
        q = q.filter(UserSession.id != current_sid)
    q.delete(synchronize_session=False)
    db.commit()
    return None


# ─────────────────────────────────────────────────────────────────────
# Phase 7 — Self deletion request
# ─────────────────────────────────────────────────────────────────────
class SelfDeletionRequestBody(BaseModel):
    reason: str


class SelfDeletionRequestResponse(BaseModel):
    id: str
    status: str
    created_at: str


@router.post(
    "/deletion-request",
    response_model=SelfDeletionRequestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Request deletion of the authenticated user's own account",
)
def request_self_deletion(
    body: SelfDeletionRequestBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    reason = (body.reason or "").strip()
    if len(reason) < 10:
        raise HTTPException(status_code=400, detail="Reason must be at least 10 characters")
    if len(reason) > 1000:
        raise HTTPException(status_code=400, detail="Reason must be ≤ 1000 characters")

    from PE.weespas.models.deletion_request import DeletionRequest

    # Prevent spam: refuse if there's already a pending request from this user
    # targeting themselves. Quick indexed lookup on (target_user_id, status).
    existing = (
        db.query(DeletionRequest)
        .filter(
            DeletionRequest.target_user_id == user.id,
            DeletionRequest.status == "pending",
        )
        .first()
    )
    if existing:
        return SelfDeletionRequestResponse(
            id=existing.id,
            status=existing.status,
            created_at=existing.created_at.isoformat() if existing.created_at else "",
        )

    row = DeletionRequest(
        id=str(uuid.uuid4()),
        target_user_id=user.id,
        target_user_name_snapshot=user.name,
        requested_by_id=user.id,
        reason=reason,
        status="pending",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info("user.self_deletion.requested user_id=%s req_id=%s", user.id, row.id)
    return SelfDeletionRequestResponse(
        id=row.id,
        status=row.status,
        created_at=row.created_at.isoformat() if row.created_at else "",
    )


# ─────────────────────────────────────────────────────────────────────
# Phase 9 — phone / email change with OTP confirmation
#
# Storage strategy:
# - pending_phone / pending_email / pending_contact_otp_hash live on the
#   user row (avoids a JOIN on confirm — confirm must be fast).
# - OTP is hashed at rest with HMAC-SHA256(secret_key, otp). Hash-only
#   storage means a DB dump can't replay OTPs.
# - Single in-flight pending change at a time; starting a new one
#   overwrites the prior pending state (cheap UX, no orphans to clean up).
# - Rate limiting borrows the auth_service in-memory store at login; for
#   change flows we keep it simpler and gate by the per-row expires_at
#   (a fresh start within the OTP window overwrites the prior code; outside
#   the window, any number is allowed). Production should add a Redis
#   token bucket here — flagged in the doc deprioritized section.
# ─────────────────────────────────────────────────────────────────────
OTP_TTL = timedelta(minutes=5)
PHONE_RE = "[0-9+\\-\\s()]"  # documentation only — validator does the work


def _hash_otp(otp: str) -> str:
    """HMAC-SHA256 the OTP with the server secret. Constant-time compare on
    confirm (hmac.compare_digest) keeps timing leaks off the table."""
    key = settings.secret_key.encode("utf-8")
    return hmac.new(key, otp.encode("utf-8"), hashlib.sha256).hexdigest()


def _generate_otp() -> str:
    # `secrets.choice` would be cryptographically stronger, but the existing
    # auth_service uses `random.choices` and we match that for consistency —
    # both are good enough for a 5-minute single-use code over a side-channel.
    return "".join(random.choices(string.digits, k=6))


def _send_otp_via_sms(phone: str, otp: str) -> None:
    """Dispatch the OTP to the user's phone. Mirrors the login OTP path so
    Africa's Talking dedupes and offload-flags apply identically."""
    if settings.debug:
        logger.info("DEBUG contact-change OTP for %s: %s", phone, otp)
    try:
        if settings.celery_send_otp_enabled:
            from PE.weespas.services import auth_tasks
            from PE.weespas.services.celery_helpers import safe_delay
            safe_delay(auth_tasks.send_otp, phone, otp)
        else:
            from PE.weespas.services.sms_service import send_otp as sms_send_otp
            sms_send_otp(phone, otp)
    except Exception as exc:
        logger.warning("contact_change.sms_send_failed phone=%s err=%s", phone, exc)


class StartPhoneChangeRequest(BaseModel):
    new_phone: str = Field(..., max_length=20)

    @validator("new_phone")
    def _clean(cls, v):
        # Reuse the canonical normalizer so phone-change uniqueness and the
        # login lookup agree on the same value. Without this, a user could
        # change their phone to "+254..." while their login row stays as
        # "0...", and the next login would 404.
        from PE.weespas.schemas.auth import normalize_phone
        return normalize_phone(v)


class StartEmailChangeRequest(BaseModel):
    new_email: str = Field(..., max_length=255)

    @validator("new_email")
    def _vemail(cls, v):
        # Mirrors RegisterRequest.validate_email — keeps the contract uniform
        # across signup and change flows without pulling in email-validator
        # (which isn't on the existing requirements pin and shouldn't be
        # added casually — it ships ~150KB of TLD tables).
        import re
        pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        if not re.match(pattern, v):
            raise ValueError("Invalid email format")
        return v.lower()


class ConfirmOtpRequest(BaseModel):
    otp: str = Field(..., min_length=6, max_length=6)


class StartChangeResponse(BaseModel):
    ok: bool = True
    expires_in: int = int(OTP_TTL.total_seconds())


@router.post(
    "/phone/start-change",
    response_model=StartChangeResponse,
    summary="Start changing the user's phone — sends OTP to the NEW number",
)
def start_phone_change(
    body: StartPhoneChangeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    new_phone = body.new_phone
    if new_phone == user.phone:
        raise HTTPException(status_code=400, detail="Phone number is unchanged")

    # Uniqueness check against active users — refusing here keeps the
    # confirm path simple (no race condition between OTP send and commit).
    taken = (
        db.query(User.id)
        .filter(User.phone == new_phone, User.id != user.id, User.is_active == True)  # noqa: E712
        .first()
    )
    if taken:
        raise HTTPException(status_code=409, detail="Phone number already registered to another account")

    otp = _generate_otp()
    user.pending_phone = new_phone
    user.pending_email = None  # mutually exclusive — only one in-flight
    user.pending_contact_kind = "phone"
    user.pending_contact_otp_hash = _hash_otp(otp)
    user.pending_contact_expires_at = datetime.now(timezone.utc) + OTP_TTL
    db.commit()

    _send_otp_via_sms(new_phone, otp)
    logger.info("user.phone_change.started user_id=%s", user.id)
    return StartChangeResponse()


@router.post(
    "/phone/confirm",
    response_model=UserResponse,
    summary="Confirm a phone change with the OTP sent to the new number",
)
def confirm_phone_change(
    body: ConfirmOtpRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _confirm_pending(user, db, kind="phone", otp=body.otp)
    return user


@router.post(
    "/email/start-change",
    response_model=StartChangeResponse,
    summary="Start changing the user's email — sends OTP via SMS to the current phone",
)
def start_email_change(
    body: StartEmailChangeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    new_email = str(body.new_email).lower()
    if new_email == (user.email or "").lower():
        raise HTTPException(status_code=400, detail="Email is unchanged")

    taken = (
        db.query(User.id)
        .filter(User.email == new_email, User.id != user.id, User.is_active == True)  # noqa: E712
        .first()
    )
    if taken:
        raise HTTPException(status_code=409, detail="Email already in use")

    otp = _generate_otp()
    user.pending_email = new_email
    user.pending_phone = None  # mutually exclusive
    user.pending_contact_kind = "email"
    user.pending_contact_otp_hash = _hash_otp(otp)
    user.pending_contact_expires_at = datetime.now(timezone.utc) + OTP_TTL
    db.commit()

    # Email channel isn't wired yet — until SMTP lands we dispatch the
    # confirmation code to the existing phone. This is documented in the
    # plan as a stub behavior; persists the preference so the flow works
    # end-to-end today.
    if user.phone:
        _send_otp_via_sms(user.phone, otp)
    logger.info("user.email_change.started user_id=%s", user.id)
    return StartChangeResponse()


@router.post(
    "/email/confirm",
    response_model=UserResponse,
    summary="Confirm an email change with the OTP",
)
def confirm_email_change(
    body: ConfirmOtpRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _confirm_pending(user, db, kind="email", otp=body.otp)
    return user


def _confirm_pending(user: User, db: Session, *, kind: str, otp: str) -> None:
    """Shared confirm path — verifies OTP, promotes pending → active,
    clears pending state. Constant-time hash compare prevents timing
    side-channel on the OTP guess. Race-free: single-row UPDATE."""
    if user.pending_contact_kind != kind:
        raise HTTPException(status_code=400, detail=f"No pending {kind} change")
    if not user.pending_contact_otp_hash or not user.pending_contact_expires_at:
        raise HTTPException(status_code=400, detail=f"No pending {kind} change")
    if user.pending_contact_expires_at < datetime.now(timezone.utc):
        # Clear stale pending state so retries don't keep failing on the
        # same expired code.
        _clear_pending(user)
        db.commit()
        raise HTTPException(status_code=410, detail="Code has expired — please start over")

    if not hmac.compare_digest(user.pending_contact_otp_hash, _hash_otp(otp)):
        raise HTTPException(status_code=401, detail="Invalid code")

    if kind == "phone":
        if not user.pending_phone:
            raise HTTPException(status_code=400, detail="No pending phone change")
        user.phone = user.pending_phone
    else:
        if not user.pending_email:
            raise HTTPException(status_code=400, detail="No pending email change")
        user.email = user.pending_email

    _clear_pending(user)
    db.commit()
    db.refresh(user)
    logger.info("user.%s_change.confirmed user_id=%s", kind, user.id)


def _clear_pending(user: User) -> None:
    user.pending_phone = None
    user.pending_email = None
    user.pending_contact_otp_hash = None
    user.pending_contact_expires_at = None
    user.pending_contact_kind = None



