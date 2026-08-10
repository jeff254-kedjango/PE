"""Self-service Become Agent / Become Staff applications.

Endpoint summary:

    /me side
    --------
    GET    /me/role-eligibility               — agent_eligible + staff_eligible + stats
    POST   /me/role-applications              — submit an application
    GET    /me/role-applications              — my own applications history

    /admin side
    -----------
    GET    /admin/role-applications           — paginated queue (filterable by status/role)
    PATCH  /admin/role-applications/{id}      — approve or reject (additive role grant)
    GET    /admin/role-applications/badge     — counts for NavBar badge

Hot path: `GET /me/role-eligibility` is hit on every ProfilePage mount.
We deliberately avoid touching the DB here — agent-eligibility is a 1-bit
check on the JWT-decoded user, and staff-eligibility is one Redis HGET
on the precomputed `analytics:staff_eligibility` HASH written by the
15-min Celery beat task `analytics.refresh_staff_eligibility`.

Admin queue uses the existing `ix_role_apps_status_role` composite index
so the paginated scan is a single index seek + LIMIT, no sort, no JOIN.
Name enrichment is batched (one round-trip for all applicants + reviewers
on the page) — same idiom as `services/deletion_request_service.py`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from PE.weespas.core.database import get_db
from PE.weespas.models.role_application import (
    RoleApplication,
    ROLE_AGENT,
    ROLE_STAFF,
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
)
from PE.weespas.models.user import User, UserRole, UserRoleRow
from PE.weespas.services.analytics_tasks import K_STAFF_ELIGIBILITY, parse_staff_eligibility
from PE.weespas.services.auth_service import get_current_user, require_admin
from PE.weespas.services.cache import redis_client
from PE.weespas.services.role_service import grant_role_additive


logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────────────────────────────────────────────────
# Redis keys for the admin badge counters. We maintain two counters
# (one per role) and INCR/DECR them transactionally with the row
# write. The badge endpoint just reads both with a single MGET.
# ─────────────────────────────────────────────────────────────────────
def _badge_key(role: str) -> str:
    return f"analytics:role_apps:pending:{role}"


def _bump_badge(role: str, delta: int) -> None:
    """Adjust the pending-count badge for the given role. Failure is
    swallowed — counters self-heal on the next pending recount path."""
    if delta == 0:
        return
    try:
        redis_client.incrby(_badge_key(role), delta)
    except Exception as exc:  # noqa: BLE001
        logger.warning("role_apps badge bump failed role=%s delta=%d err=%s", role, delta, exc)


# ─────────────────────────────────────────────────────────────────────
# Pydantic schemas — kept local to this router. The application is
# semantically distinct from the auth shapes, and inlining the schemas
# avoids a third file for ten lines of definitions.
# ─────────────────────────────────────────────────────────────────────
class StaffStats(BaseModel):
    listings: int
    views: int
    days: int
    min_listings: int
    min_views: int
    min_days: int


class EligibilityResponse(BaseModel):
    agent_eligible: bool
    staff_eligible: bool
    # Present only when the user is already an agent — drives the
    # progress hint in the ineligible-state modal.
    staff_stats: Optional[StaffStats] = None
    # True if the user has a pending application of either type, so the
    # frontend can show "Application pending review" instead of a CTA.
    pending_agent: bool = False
    pending_staff: bool = False


class ApplicationCreate(BaseModel):
    role_requested: str = Field(..., description="'agent' or 'staff'")
    message: str = Field(..., min_length=10, max_length=1000)


class ApplicationResponse(BaseModel):
    id: str
    applicant_id: str
    role_requested: str
    message: str
    status: str
    review_note: Optional[str] = None
    created_at: datetime
    reviewed_at: Optional[datetime] = None
    # Enriched fields (admin list view). Marked optional so the same
    # schema serves both /me and /admin endpoints.
    applicant_name: Optional[str] = None
    reviewed_by_name: Optional[str] = None

    class Config:
        from_attributes = True


class ApplicationReview(BaseModel):
    status: str = Field(..., description="'approved' or 'rejected'")
    review_note: Optional[str] = Field(None, max_length=1000)


class PaginatedApplications(BaseModel):
    total: int
    skip: int
    limit: int
    items: List[ApplicationResponse]


class BadgeResponse(BaseModel):
    agent_pending: int
    staff_pending: int


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _serialize_applications(
    db: Session,
    apps: Iterable[RoleApplication],
) -> List[Dict[str, Any]]:
    """Batched name enrichment — mirrors serialize_deletion_requests."""
    rows = list(apps)
    if not rows:
        return []

    user_ids: set[str] = set()
    for r in rows:
        user_ids.add(r.applicant_id)
        if r.reviewed_by_id:
            user_ids.add(r.reviewed_by_id)

    name_by_id: Dict[str, str] = {}
    if user_ids:
        results = db.query(User.id, User.name).filter(User.id.in_(user_ids)).all()
        name_by_id = {uid: name for uid, name in results}

    out: List[Dict[str, Any]] = []
    for r in rows:
        payload = ApplicationResponse.model_validate(r).model_dump()
        payload["applicant_name"] = name_by_id.get(r.applicant_id)
        payload["reviewed_by_name"] = (
            name_by_id.get(r.reviewed_by_id) if r.reviewed_by_id else None
        )
        out.append(payload)
    return out


def _has_pending(db: Session, user_id: str, role: str) -> bool:
    """Single PK-indexed lookup against ix_role_apps_applicant_status."""
    return (
        db.query(RoleApplication.id)
        .filter(
            RoleApplication.applicant_id == user_id,
            RoleApplication.status == STATUS_PENDING,
            RoleApplication.role_requested == role,
        )
        .first()
        is not None
    )


def _hget_staff_eligibility(agent_id: str) -> Optional[dict]:
    """One Redis HGET on the precomputed HASH. Returns parsed dict or None.

    Never raises — Redis being unreachable degrades to "no eligibility
    data" which the caller treats as ineligible. That's a safer default
    than letting an ops incident open the staff floodgates.
    """
    try:
        raw = redis_client.hget(K_STAFF_ELIGIBILITY, agent_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("staff_eligibility HGET failed agent=%s err=%s", agent_id, exc)
        return None
    return parse_staff_eligibility(raw)


# ─────────────────────────────────────────────────────────────────────
# /me/role-eligibility — the hot path
# ─────────────────────────────────────────────────────────────────────
@router.get(
    "/me/role-eligibility",
    response_model=EligibilityResponse,
    tags=["Me"],
    summary="Eligibility check for Become-Agent / Become-Staff CTAs",
)
def get_role_eligibility(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """O(1) Redis lookup for staff metrics + 1-bit check for agent."""
    has_agent_role = current_user.has_role(UserRole.AGENT) or bool(current_user.agent_id)
    has_staff_role = current_user.has_role(UserRole.STAFF)

    staff_stats: Optional[StaffStats] = None
    staff_eligible_metrics = False

    if current_user.agent_id:
        parsed = _hget_staff_eligibility(current_user.agent_id)
        if parsed:
            staff_eligible_metrics = parsed["eligible"]
            staff_stats = StaffStats(
                listings=parsed["listings"],
                views=parsed["views"],
                days=parsed["days"],
                min_listings=parsed["min_listings"],
                min_views=parsed["min_views"],
                min_days=parsed["min_days"],
            )
        else:
            # No row in the precompute yet — show zeros so the UI can
            # still render the progress hint. Eligibility stays False.
            staff_stats = StaffStats(
                listings=0,
                views=0,
                days=0,
                min_listings=10,
                min_views=500,
                min_days=90,
            )

    # Pending-application flags — one indexed query each, fast enough on
    # the hot path. Could be batched into one query, but the row count
    # for a single user is bounded to 2 so the two-statement form is
    # clearer than COUNT GROUP BY.
    pending_agent = _has_pending(db, current_user.id, ROLE_AGENT)
    pending_staff = _has_pending(db, current_user.id, ROLE_STAFF)

    return EligibilityResponse(
        agent_eligible=not has_agent_role and not pending_agent,
        staff_eligible=(
            has_agent_role
            and not has_staff_role
            and staff_eligible_metrics
            and not pending_staff
        ),
        staff_stats=staff_stats,
        pending_agent=pending_agent,
        pending_staff=pending_staff,
    )


# ─────────────────────────────────────────────────────────────────────
# /me/role-applications — submit + list-my-own
# ─────────────────────────────────────────────────────────────────────
@router.post(
    "/me/role-applications",
    response_model=ApplicationResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Me"],
    summary="Submit a Become-Agent or Become-Staff application",
)
def create_application(
    body: ApplicationCreate = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    role = body.role_requested
    if role not in (ROLE_AGENT, ROLE_STAFF):
        raise HTTPException(status_code=400, detail="role_requested must be 'agent' or 'staff'")

    # ── Already-have-this-role guard ──
    if role == ROLE_AGENT and (current_user.has_role(UserRole.AGENT) or current_user.agent_id):
        raise HTTPException(status_code=400, detail="You are already an agent.")
    if role == ROLE_STAFF and current_user.has_role(UserRole.STAFF):
        raise HTTPException(status_code=400, detail="You are already on the staff team.")

    # ── Staff eligibility re-check (never trust the client gate) ──
    if role == ROLE_STAFF:
        if not current_user.agent_id:
            raise HTTPException(
                status_code=403,
                detail="Only agents can apply to become staff.",
            )
        parsed = _hget_staff_eligibility(current_user.agent_id)
        if not parsed or not parsed["eligible"]:
            raise HTTPException(
                status_code=403,
                detail=(
                    "You do not yet meet the staff-eligibility threshold "
                    "(≥3 months as an agent, ≥10 active listings, ≥500 views)."
                ),
            )

    # ── Duplicate-pending guard (idx_role_apps_applicant_status) ──
    if _has_pending(db, current_user.id, role):
        raise HTTPException(
            status_code=400,
            detail="You already have a pending application for this role.",
        )

    app_row = RoleApplication(
        applicant_id=current_user.id,
        role_requested=role,
        message=body.message.strip(),
        status=STATUS_PENDING,
    )
    db.add(app_row)
    db.commit()
    db.refresh(app_row)

    _bump_badge(role, +1)
    logger.info(
        "role_app.submitted user_id=%s role=%s app_id=%s",
        current_user.id, role, app_row.id,
    )
    payload = ApplicationResponse.model_validate(app_row).model_dump()
    payload["applicant_name"] = current_user.name
    return payload


@router.get(
    "/me/role-applications",
    response_model=List[ApplicationResponse],
    tags=["Me"],
    summary="List my own role applications (most recent first)",
)
def list_my_applications(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(RoleApplication)
        .filter(RoleApplication.applicant_id == current_user.id)
        .order_by(RoleApplication.created_at.desc())
        .limit(20)
        .all()
    )
    # Single user — no batched enrichment needed.
    out: List[Dict[str, Any]] = []
    for r in rows:
        payload = ApplicationResponse.model_validate(r).model_dump()
        payload["applicant_name"] = current_user.name
        out.append(payload)
    return out


# ─────────────────────────────────────────────────────────────────────
# /admin/role-applications — paginated queue
# ─────────────────────────────────────────────────────────────────────
@router.get(
    "/admin/role-applications",
    response_model=PaginatedApplications,
    tags=["Admin"],
    summary="List role applications (admin queue)",
)
def admin_list_applications(
    status_filter: Optional[str] = Query(None, alias="status"),
    role: Optional[str] = Query(None, description="'agent' or 'staff'"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    q = db.query(RoleApplication)
    if status_filter:
        q = q.filter(RoleApplication.status == status_filter)
    if role:
        q = q.filter(RoleApplication.role_requested == role)

    total = q.count()
    rows = (
        q.order_by(RoleApplication.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    items = _serialize_applications(db, rows)
    return PaginatedApplications(total=total, skip=skip, limit=limit, items=items)


@router.patch(
    "/admin/role-applications/{application_id}",
    response_model=ApplicationResponse,
    tags=["Admin"],
    summary="Approve or reject a role application",
)
def admin_review_application(
    application_id: str = Path(...),
    body: ApplicationReview = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if body.status not in (STATUS_APPROVED, STATUS_REJECTED):
        raise HTTPException(status_code=400, detail="status must be 'approved' or 'rejected'")

    app_row = (
        db.query(RoleApplication)
        .filter(RoleApplication.id == application_id)
        .first()
    )
    if not app_row:
        raise HTTPException(status_code=404, detail="Application not found")
    if app_row.status != STATUS_PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Application already {app_row.status}.",
        )

    # ── Approval grants the role atomically with the status update ──
    if body.status == STATUS_APPROVED:
        applicant = db.query(User).filter(User.id == app_row.applicant_id).first()
        if not applicant:
            raise HTTPException(status_code=410, detail="Applicant no longer exists.")
        # grant_role_additive() does NOT commit — same txn as the status flip.
        grant_role_additive(db, applicant, app_row.role_requested)

    app_row.status = body.status
    app_row.review_note = (body.review_note or "").strip() or None
    app_row.reviewed_by_id = admin.id
    app_row.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(app_row)

    _bump_badge(app_row.role_requested, -1)
    logger.info(
        "role_app.reviewed id=%s status=%s by=%s",
        app_row.id, body.status, admin.id,
    )

    # Enriched response for the admin UI.
    enriched = _serialize_applications(db, [app_row])
    return enriched[0]


@router.get(
    "/admin/role-applications/badge",
    response_model=BadgeResponse,
    tags=["Admin"],
    summary="Pending-application counts for the NavBar badge",
)
def admin_badge(_admin: User = Depends(require_admin)):
    """Two Redis GETs. Counters are maintained inline with submit/review
    so the badge is eventually consistent with sub-second precision.

    Falls back to a DB count on Redis miss/error so the badge never
    shows a stale-zero on a fresh deploy before the first INCR has
    happened.
    """
    try:
        with redis_client.pipeline() as pipe:
            pipe.get(_badge_key(ROLE_AGENT))
            pipe.get(_badge_key(ROLE_STAFF))
            agent_raw, staff_raw = pipe.execute()
        agent_pending = int(agent_raw or 0)
        staff_pending = int(staff_raw or 0)
        # Negative counts can happen if a row is deleted out-of-band.
        # Clamp to zero so the UI never shows a nonsense badge.
        return BadgeResponse(
            agent_pending=max(agent_pending, 0),
            staff_pending=max(staff_pending, 0),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("role_apps badge read failed: %s — falling back to DB", exc)
        # Cold-start / Redis-down fallback. One indexed COUNT GROUP BY
        # scan on ix_role_apps_status_role. Kept inside the except block
        # so the happy path stays zero-DB.
        from sqlalchemy import func
        from PE.weespas.core.database import SessionLocal
        db2 = SessionLocal()
        try:
            counts = (
                db2.query(RoleApplication.role_requested, func.count(RoleApplication.id))
                .filter(RoleApplication.status == STATUS_PENDING)
                .group_by(RoleApplication.role_requested)
                .all()
            )
            by_role = {role: cnt for role, cnt in counts}
            return BadgeResponse(
                agent_pending=int(by_role.get(ROLE_AGENT, 0)),
                staff_pending=int(by_role.get(ROLE_STAFF, 0)),
            )
        finally:
            db2.close()
