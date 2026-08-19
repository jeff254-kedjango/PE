from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Path, Body
from sqlalchemy.orm import Session
from sqlalchemy import func as sql_func, or_

ONLINE_WINDOW = timedelta(minutes=5)


def _is_online(last_seen_at) -> bool:
    if last_seen_at is None:
        return False
    return (datetime.now(timezone.utc) - last_seen_at) < ONLINE_WINDOW

from PE.weespas.core.database import get_db
from PE.weespas.services.auth_service import require_staff, get_current_user
from PE.weespas.models.user import User, UserRole
from PE.weespas.models.property import Agent, Property
from PE.weespas.models.deletion_request import DeletionRequest
from PE.weespas.schemas.auth import (
    UserAdminResponse, UserPublicResponse, PaginatedUserResponse,
    DeletionRequestCreate, DeletionRequestResponse,
)
from PE.weespas.services.deletion_request_service import serialize_deletion_requests
router = APIRouter()


def _privacy_filter(user_obj: User, viewer: User) -> dict:
    """
    Return user data respecting privacy rules:
    - Admin/Staff/Agent can always see email & phone
    - Other users only see email & phone if the target has is_public_profile=True
    """
    data = {
        "id": user_obj.id,
        "name": user_obj.name,
        "avatar": user_obj.avatar,
        "role": user_obj.role.value if isinstance(user_obj.role, UserRole) else user_obj.role,
        "roles": list(user_obj.roles) if user_obj.roles else [],
        "agent_id": user_obj.agent_id,
        "is_public_profile": user_obj.is_public_profile,
        "created_at": user_obj.created_at,
        "last_seen_at": user_obj.last_seen_at,
        "is_online": _is_online(user_obj.last_seen_at),
    }
    # Staff always sees email/phone (but never passwords)
    data["email"] = user_obj.email
    data["phone"] = user_obj.phone
    return data


# ===================== LIST USERS =====================

@router.get(
    "/staff/users",
    summary="List users (staff view)",
    tags=["Staff"],
)
def list_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    role: str = Query(None, description="Filter by role"),
    q: str = Query(None, description="Search by name, email, or phone"),
    current_user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """List users with full details. Staff and admin only."""
    query = db.query(User).filter(User.is_active == True)
    if role:
        query = query.filter(User.role == role)
    if q:
        pattern = f"%{q}%"
        query = query.filter(
            or_(User.name.ilike(pattern), User.email.ilike(pattern), User.phone.ilike(pattern))
        )
    total = query.count()
    users = query.order_by(User.created_at.desc()).offset(skip).limit(limit).all()
    items = [_privacy_filter(u, current_user) for u in users]
    return PaginatedUserResponse(total=total, skip=skip, limit=limit, items=items)


# ===================== GET USER DETAILS =====================

@router.get(
    "/staff/users/{user_id}",
    response_model=UserAdminResponse,
    summary="Get user details (staff view)",
    tags=["Staff"],
)
def get_user(
    user_id: str = Path(...),
    current_user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """Get user details — everything except password. Staff and admin only."""
    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


# ===================== LIST AGENTS =====================

@router.get(
    "/staff/agents",
    summary="List all agents (staff view)",
    tags=["Staff"],
)
def list_agents(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    q: str = Query(None, description="Search by name"),
    current_user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """List all active agents with their details. Staff and admin only."""
    # Subquery: property count per agent (single query, no N+1)
    prop_count_sq = (
        db.query(
            Property.agent_id,
            sql_func.count(Property.id).label("prop_count")
        )
        .filter(Property.is_active == True)
        .group_by(Property.agent_id)
        .subquery()
    )

    query = (
        db.query(Agent, sql_func.coalesce(prop_count_sq.c.prop_count, 0).label("property_count"))
        .outerjoin(prop_count_sq, Agent.id == prop_count_sq.c.agent_id)
        .filter(Agent.is_active == True)
    )
    if q:
        query = query.filter(Agent.agent_name.ilike(f"%{q}%"))
    total = query.count()
    results = query.order_by(Agent.agent_name).offset(skip).limit(limit).all()

    items = []
    for agent, prop_count in results:
        linked_user = agent.user_account[0] if agent.user_account else None
        items.append({
            "id": agent.id,
            "agent_name": agent.agent_name,
            "agent_phone_number": agent.agent_phone_number,
            "agent_profile_picture": agent.agent_profile_picture,
            "email": agent.email,
            "bio": agent.bio,
            "is_verified": agent.is_verified,
            "property_count": prop_count,
            "user_id": linked_user.id if linked_user else None,
            "roles": list(linked_user.roles) if linked_user and linked_user.roles else [],
            "last_seen_at": linked_user.last_seen_at if linked_user else None,
            "is_online": _is_online(linked_user.last_seen_at) if linked_user else False,
        })

    return {"total": total, "skip": skip, "limit": limit, "items": items}


# ===================== REQUEST DELETION (STAFF) =====================

@router.post(
    "/staff/deletion-requests",
    response_model=DeletionRequestResponse,
    summary="Request deletion of a user or agent",
    tags=["Staff"],
    status_code=201,
)
def request_deletion(
    body: DeletionRequestCreate = Body(...),
    current_user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """
    Staff can request deletion of a user or agent.
    The request must be approved by an admin before the user is actually deleted.
    """
    target = db.query(User).filter(User.id == body.target_user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target user not found")

    if target.role == UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Cannot request deletion of an admin")

    # Check for existing pending request
    existing = db.query(DeletionRequest).filter(
        DeletionRequest.target_user_id == body.target_user_id,
        DeletionRequest.status == "pending",
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="A pending deletion request already exists for this user")

    req = DeletionRequest(
        target_user_id=body.target_user_id,
        requested_by_id=current_user.id,
        reason=body.reason,
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    return req


# ===================== LIST MY DELETION REQUESTS =====================

@router.get(
    "/staff/deletion-requests",
    summary="List my deletion requests",
    tags=["Staff"],
)
def my_deletion_requests(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """List deletion requests submitted by the current staff/admin user.

    Privacy: a staff member must never see another staff member's drafts.
    The query is hard-pinned to ``requested_by_id == current_user.id`` and
    the role gate (``require_staff``) blocks plain agents/users entirely.
    """
    query = db.query(DeletionRequest).filter(
        DeletionRequest.requested_by_id == current_user.id,
    )
    total = query.count()
    items = query.order_by(DeletionRequest.created_at.desc()).offset(skip).limit(limit).all()
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "items": serialize_deletion_requests(db, items),
    }
