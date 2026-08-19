from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Path, Body, status
from sqlalchemy import or_, and_
from sqlalchemy.orm import Session

from PE.weespas.core.database import get_db
from PE.weespas.services.auth_service import require_admin, get_current_user
from PE.weespas.models.user import User, UserRole, UserRoleRow
from PE.weespas.models.property import Agent, Property
from PE.weespas.models.deletion_request import DeletionRequest
from PE.weespas.schemas.auth import (
    UserAdminResponse, RoleAssignRequest, RolesAssignRequest, PaginatedUserResponse,
    DeletionRequestResponse, DeletionReviewRequest,
)
from PE.weespas.schemas.property import (
    FeatureRequest, PropertyListResponse, PaginatedPropertyResponse,
)
from PE.weespas.services.property_service import PropertyService, _list_load_options
from PE.weespas.services.deletion_request_service import serialize_deletion_requests


def _ensure_agent_profile(db: Session, user: User) -> None:
    """Ensure a user with the `agent` role has a linked Agent profile.

    If the user has no `agent_id`, find an existing Agent row matching their
    email or create a fresh one and link it. No-op if already linked.
    """
    if user.agent_id:
        return
    # Try to adopt an existing unlinked agent profile by email or phone match
    existing = (
        db.query(Agent)
        .filter(
            Agent.is_active == True,
            or_(Agent.email == user.email, Agent.agent_phone_number == user.phone),
        )
        .first()
    )
    if existing:
        already_linked = (
            db.query(User)
            .filter(User.agent_id == existing.id, User.id != user.id)
            .first()
        )
        if not already_linked:
            user.agent_id = existing.id
            return
        raise HTTPException(
            status_code=409,
            detail=(
                f"An agent profile matching this user's email/phone is already "
                f"linked to another account. Resolve the conflict before granting agent role."
            ),
        )
    # Otherwise create a new Agent row from the user's profile
    new_agent = Agent(
        agent_name=user.name,
        agent_phone_number=user.phone,
        email=user.email,
        agent_profile_picture=user.avatar,
        is_active=True,
    )
    db.add(new_agent)
    db.flush()  # populate new_agent.id without committing
    user.agent_id = new_agent.id

router = APIRouter()


# ===================== LIST ALL USERS =====================

@router.get(
    "/admin/users",
    summary="List all users",
    tags=["Admin"],
)
def list_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    role: str = Query(None, description="Filter by role"),
    q: str = Query(None, description="Search by name, email, or phone"),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List all users. Admin only."""
    query = db.query(User)
    if role:
        query = query.filter(User.role == role)
    if q:
        pattern = f"%{q}%"
        query = query.filter(
            or_(User.name.ilike(pattern), User.email.ilike(pattern), User.phone.ilike(pattern))
        )
    total = query.count()
    users = query.order_by(User.created_at.desc()).offset(skip).limit(limit).all()
    items = [UserAdminResponse.model_validate(u).model_dump() for u in users]
    return PaginatedUserResponse(total=total, skip=skip, limit=limit, items=items)


# ===================== GET USER DETAILS =====================

@router.get(
    "/admin/users/{user_id}",
    response_model=UserAdminResponse,
    summary="Get full user details",
    tags=["Admin"],
)
def get_user(
    user_id: str = Path(...),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Get full details of any user (except password). Admin only."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


# ===================== ASSIGN ROLE =====================

def _replace_user_roles(db: Session, user: User, roles: list[str], actor: User) -> User:
    """Replace the user's roles atomically. Roles are additive; agent_id is preserved.

    Used by both the multi-role endpoint and the legacy single-role endpoint.
    """
    if user.id == actor.id and "admin" not in roles and actor.has_role(UserRole.ADMIN):
        raise HTTPException(status_code=400, detail="Cannot remove admin from yourself")

    db.query(UserRoleRow).filter(UserRoleRow.user_id == user.id).delete(synchronize_session=False)
    for role in roles:
        db.add(UserRoleRow(user_id=user.id, role=role))

    # Keep `users.role` in sync as the "primary" role for back-compat.
    # Prefer the highest-privilege role we can find, else the first role.
    priority = ["admin", "staff", "agent", "user"]
    primary = next((r for r in priority if r in roles), roles[0])
    user.role = UserRole(primary)

    # If the user now has an agent role, make sure they have a linked Agent
    # profile. This makes the standard role-assignment endpoints safe even
    # when admins forget to call /admin/promote-agent first.
    if "agent" in roles:
        _ensure_agent_profile(db, user)

    db.commit()
    db.refresh(user)
    return user


@router.patch(
    "/admin/users/{user_id}/roles",
    summary="Assign roles to a user (multi-role)",
    tags=["Admin"],
)
def assign_roles(
    user_id: str = Path(...),
    body: RolesAssignRequest = Body(...),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Replace the user's role list. Admin only.
    Roles: user, agent, staff, admin (any subset, at least one).
    Roles are additive: changing roles never clears `agent_id`, so an agent
    promoted to staff retains access to their listings.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user = _replace_user_roles(db, user, body.roles, current_user)
    return {
        "message": f"User '{user.name}' roles updated to {user.roles}",
        "user_id": user.id,
        "role": user.role.value,
        "roles": user.roles,
    }


@router.patch(
    "/admin/users/{user_id}/role",
    summary="Assign a single role to a user (legacy alias)",
    tags=["Admin"],
)
def assign_role(
    user_id: str = Path(...),
    body: RoleAssignRequest = Body(...),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Legacy single-role alias. Routes through the additive multi-role logic
    (one role in the list); `agent_id` is preserved across role changes."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    old_role = user.role
    user = _replace_user_roles(db, user, [body.role], current_user)
    return {
        "message": f"User '{user.name}' role changed from '{old_role.value}' to '{user.role.value}'",
        "user_id": user.id,
        "role": user.role.value,
        "roles": user.roles,
    }


# ===================== DEACTIVATE / REACTIVATE USER =====================

@router.patch(
    "/admin/users/{user_id}/status",
    summary="Activate or deactivate a user",
    tags=["Admin"],
)
def toggle_user_status(
    user_id: str = Path(...),
    active: bool = Body(..., embed=True),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Activate or deactivate a user account. Admin only."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")

    user.is_active = active
    db.commit()
    action = "activated" if active else "deactivated"
    return {"message": f"User '{user.name}' {action}", "user_id": user.id, "is_active": user.is_active}


# ===================== DELETE USER (ADMIN DIRECT) =====================

@router.delete(
    "/admin/users/{user_id}",
    summary="Permanently delete a user",
    tags=["Admin"],
)
def delete_user(
    user_id: str = Path(...),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Permanently delete a user. Admin only."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    name = user.name
    db.delete(user)
    db.commit()
    return {"message": f"User '{name}' permanently deleted"}


# ===================== REVIEW DELETION REQUEST =====================

@router.get(
    "/admin/deletion-requests",
    summary="List pending deletion requests",
    tags=["Admin"],
)
def list_deletion_requests(
    status_filter: str = Query("pending", alias="status", description="pending, approved, rejected"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List deletion requests submitted by staff. Admin only."""
    query = db.query(DeletionRequest).filter(DeletionRequest.status == status_filter)
    total = query.count()
    items = query.order_by(DeletionRequest.created_at.desc()).offset(skip).limit(limit).all()
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "items": serialize_deletion_requests(db, items),
    }


@router.patch(
    "/admin/deletion-requests/{request_id}",
    response_model=DeletionRequestResponse,
    summary="Approve or reject a deletion request",
    tags=["Admin"],
)
def review_deletion_request(
    request_id: str = Path(...),
    body: DeletionReviewRequest = Body(...),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Approve or reject a staff deletion request.
    If approved, the target user is permanently deleted.
    """
    req = db.query(DeletionRequest).filter(DeletionRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Deletion request not found")
    if req.status != "pending":
        raise HTTPException(status_code=400, detail=f"Request already {req.status}")

    req.status = body.status
    req.reviewed_by_id = current_user.id
    req.review_note = body.review_note
    req.reviewed_at = datetime.now(timezone.utc)

    if body.status == "approved":
        target = db.query(User).filter(User.id == req.target_user_id).first()
        if target:
            req.target_user_name_snapshot = target.name
            db.delete(target)

    db.commit()
    db.refresh(req)
    return req


# ===================== FEATURED LISTINGS (admin promotion) =====================
# Featured is a FREE, editorial promotion: an admin elevates a trustworthy listing
# into the home carousel. The carousel itself ranks by trust (engineer-certified /
# verified agent / InSAR-monitored) — see services.ranking.trust_signal — so this
# panel just decides WHICH listings are eligible, and for how long.

@router.get(
    "/admin/featured",
    response_model=PaginatedPropertyResponse,
    summary="List active featured listings",
    tags=["Admin"],
)
def list_active_featured(
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Currently-active promotions (featured AND active AND not past expiry), newest
    first. Mirrors the read-path base filter so this panel and the public carousel
    agree on what 'featured' means."""
    now = datetime.now(timezone.utc)
    not_expired = or_(
        Property.featured_expires_at.is_(None),
        Property.featured_expires_at > now,
    )
    base = and_(
        Property.is_featured == True,  # noqa: E712
        Property.is_active == True,    # noqa: E712
        not_expired,
    )
    total = db.query(Property).filter(base).count()
    rows = (
        db.query(Property)
        .options(*_list_load_options())
        .filter(base)
        .order_by(Property.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return PaginatedPropertyResponse(
        total=total,
        skip=skip,
        limit=limit,
        items=[PropertyService._format_list_response(p) for p in rows],
    )


@router.post(
    "/admin/properties/{property_id}/feature",
    response_model=PropertyListResponse,
    summary="Feature or unfeature a listing (with optional duration)",
    tags=["Admin"],
)
def set_property_featured(
    property_id: str,
    body: FeatureRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Set a listing's featured state. Featuring with `duration_days` sets an expiry of
    now + N days; an explicit `featured_expires_at` overrides that; neither = no expiry
    (permanent). Unfeaturing clears the expiry. Cache is invalidated via the standard
    property-write fanout so the carousel reflects the change on its next miss/warm."""
    prop = db.query(Property).filter(Property.id == property_id).first()
    if prop is None:
        raise HTTPException(status_code=404, detail="Property not found")

    prop.is_featured = body.is_featured
    if not body.is_featured:
        prop.featured_expires_at = None
    elif body.featured_expires_at is not None:
        prop.featured_expires_at = body.featured_expires_at
    elif body.duration_days is not None:
        prop.featured_expires_at = datetime.now(timezone.utc) + timedelta(days=body.duration_days)
    else:
        prop.featured_expires_at = None  # explicit "no expiry"

    db.commit()
    db.refresh(prop)

    # Reuse the property-write fanout so featured:global (and related caches) is blown.
    from PE.weespas.routers.properties import _dispatch_property_write_fanout
    _dispatch_property_write_fanout(db, property_id)

    return PropertyService._format_list_response(prop)
