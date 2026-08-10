from fastapi import APIRouter, Depends, HTTPException, Query, Path, Body, status
from sqlalchemy.orm import Session
from sqlalchemy import func as sql_func

from PE.weespas.core.database import get_db
from PE.weespas.services.auth_service import require_agent, require_admin
from PE.weespas.models.user import User, UserRole, UserRoleRow
from PE.weespas.models.property import Property, Agent, PropertyListingType
from PE.weespas.services.property_service import PropertyService, _list_load_options
from PE.weespas.schemas.property import PaginatedPropertyResponse
from PE.weespas.schemas.agent import (
    AgentSearchResponse, PaginatedAgentResponse, AgentStatsResponse, PromoteAgentRequest
)

router = APIRouter()


# ===================== PUBLIC — LIST AGENTS =====================

@router.get(
    "/agents/public",
    response_model=PaginatedAgentResponse,
    summary="List active agents (public)",
    tags=["Agents"],
)
def list_agents_public(
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=50),
    q: str = Query(None, max_length=100, description="Search by agent name"),
    db: Session = Depends(get_db),
):
    """List all active agents with property count. No authentication required."""
    prop_count_sq = (
        db.query(
            Property.agent_id,
            sql_func.count(Property.id).label("prop_count"),
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

    items = [
        AgentSearchResponse(
            id=agent.id,
            agent_name=agent.agent_name,
            agent_phone_number=agent.agent_phone_number,
            agent_profile_picture=agent.agent_profile_picture,
            email=agent.email,
            bio=agent.bio,
            is_verified=agent.is_verified,
            property_count=prop_count,
        )
        for agent, prop_count in results
    ]

    return PaginatedAgentResponse(total=total, skip=skip, limit=limit, items=items)


# ===================== PUBLIC — SINGLE AGENT =====================

@router.get(
    "/agents/public/{agent_id}",
    response_model=AgentSearchResponse,
    summary="Get agent details (public)",
    tags=["Agents"],
)
def get_agent_public(
    agent_id: str = Path(..., description="Agent UUID"),
    db: Session = Depends(get_db),
):
    """Get a single agent's public profile with property count. No authentication required."""
    prop_count_sq = (
        db.query(
            Property.agent_id,
            sql_func.count(Property.id).label("prop_count"),
        )
        .filter(Property.is_active == True)
        .group_by(Property.agent_id)
        .subquery()
    )

    result = (
        db.query(Agent, sql_func.coalesce(prop_count_sq.c.prop_count, 0).label("property_count"))
        .outerjoin(prop_count_sq, Agent.id == prop_count_sq.c.agent_id)
        .filter(Agent.id == agent_id, Agent.is_active == True)
        .first()
    )
    if not result:
        raise HTTPException(status_code=404, detail="Agent not found")

    agent, prop_count = result
    return AgentSearchResponse(
        id=agent.id,
        agent_name=agent.agent_name,
        agent_phone_number=agent.agent_phone_number,
        agent_profile_picture=agent.agent_profile_picture,
        email=agent.email,
        bio=agent.bio,
        is_verified=agent.is_verified,
        property_count=prop_count,
    )


# ===================== PUBLIC — AGENT'S PROPERTIES =====================

@router.get(
    "/agents/public/{agent_id}/properties",
    response_model=PaginatedPropertyResponse,
    summary="List an agent's properties (public)",
    tags=["Agents"],
)
def agent_properties_public(
    agent_id: str = Path(..., description="Agent UUID"),
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """List all active properties for a specific agent. No authentication required."""
    agent = db.query(Agent).filter(Agent.id == agent_id, Agent.is_active == True).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    query = db.query(Property).filter(
        Property.agent_id == agent_id,
        Property.is_active == True,
    )
    total = query.count()
    properties = (
        query.options(*_list_load_options())
        .order_by(Property.created_at.desc())
        .offset(skip).limit(limit).all()
    )

    items = [PropertyService._format_list_response(prop) for prop in properties]
    return PaginatedPropertyResponse(total=total, skip=skip, limit=limit, items=items)


# ===================== AGENT — MY PROPERTIES =====================

@router.get(
    "/agents/me/properties",
    response_model=PaginatedPropertyResponse,
    summary="List my properties",
    tags=["Agents"]
)
def my_properties(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_agent),
    db: Session = Depends(get_db)
):
    """List properties belonging to the authenticated agent.

    For admins without a linked agent profile, "my listings" means platform
    listings not assigned to any agent (i.e. company-owned)."""

    if current_user.has_role(UserRole.ADMIN) and not current_user.agent_id:
        query = db.query(Property).filter(
            Property.agent_id.is_(None),
            Property.is_active == True,
        )
    else:
        if not current_user.agent_id:
            raise HTTPException(status_code=400, detail="No agent profile linked to your account")
        query = db.query(Property).filter(
            Property.agent_id == current_user.agent_id,
            Property.is_active == True
        )

    total = query.count()
    properties = (
        query.options(*_list_load_options())
        .order_by(Property.created_at.desc())
        .offset(skip).limit(limit).all()
    )

    items = [PropertyService._format_list_response(prop) for prop in properties]
    return PaginatedPropertyResponse(total=total, skip=skip, limit=limit, items=items)


# ===================== AGENT — MY STATS =====================

@router.get(
    "/agents/me/stats",
    response_model=AgentStatsResponse,
    summary="Get my dashboard stats",
    tags=["Agents"]
)
def my_stats(
    scope: str = Query("mine", regex="^(mine|global)$"),
    current_user: User = Depends(require_agent),
    db: Session = Depends(get_db)
):
    """Aggregate stats for the authenticated agent's properties.
    Admins without an agent profile see platform-wide stats.
    Pass ``scope=global`` to get platform-wide stats regardless of role."""

    # Admins without a linked agent profile always get platform-wide stats.
    # Any caller asking for ``scope=global`` also gets platform-wide stats.
    is_admin_overview = current_user.has_role(UserRole.ADMIN) and not current_user.agent_id
    is_global = scope == "global" or is_admin_overview

    if not is_global:
        if not current_user.agent_id:
            raise HTTPException(status_code=400, detail="No agent profile linked to your account")
        agent = db.query(Agent).filter(Agent.id == current_user.agent_id).first()
        if not agent:
            raise HTTPException(status_code=404, detail="Agent profile not found")
        agent_name = agent.agent_name
        agent_id = current_user.agent_id
        base = db.query(Property).filter(Property.agent_id == current_user.agent_id)
        views_filter = Property.agent_id == current_user.agent_id
    else:
        agent_name = "Global" if scope == "global" and not is_admin_overview else current_user.name
        agent_id = "global" if scope == "global" else "admin"
        base = db.query(Property)
        views_filter = True  # all properties

    total = base.count()
    active = base.filter(Property.is_active == True).count()
    inactive = total - active
    total_views = db.query(sql_func.coalesce(sql_func.sum(Property.view_count), 0)).filter(
        views_filter
    ).scalar()
    for_sale = base.filter(Property.is_active == True, Property.listing_type == PropertyListingType.SALE).count()
    for_rent = base.filter(Property.is_active == True, Property.listing_type == PropertyListingType.RENT).count()
    featured = base.filter(Property.is_active == True, Property.is_featured == True).count()
    certified = base.filter(Property.is_active == True, Property.is_engineer_certified == True).count()

    return AgentStatsResponse(
        agent_id=agent_id,
        agent_name=agent_name,
        total_properties=total,
        active_properties=active,
        inactive_properties=inactive,
        total_views=int(total_views),
        properties_for_sale=for_sale,
        properties_for_rent=for_rent,
        featured_count=featured,
        engineer_certified_count=certified,
    )


# ===================== SEARCH AGENTS =====================

@router.get(
    "/agents/search",
    response_model=PaginatedAgentResponse,
    summary="Search agents by name",
    tags=["Agents"]
)
def search_agents(
    q: str = Query(..., min_length=1, max_length=100, description="Agent name search"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_agent),
    db: Session = Depends(get_db)
):
    """Search for agents by name. Returns agent profiles with property count."""
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
        .filter(Agent.is_active == True, Agent.agent_name.ilike(f"%{q}%"))
    )
    total = query.count()
    results = query.order_by(Agent.agent_name).offset(skip).limit(limit).all()

    items = [
        AgentSearchResponse(
            id=agent.id,
            agent_name=agent.agent_name,
            agent_phone_number=agent.agent_phone_number,
            agent_profile_picture=agent.agent_profile_picture,
            email=agent.email,
            bio=agent.bio,
            is_verified=agent.is_verified,
            property_count=prop_count,
        )
        for agent, prop_count in results
    ]

    return PaginatedAgentResponse(total=total, skip=skip, limit=limit, items=items)


# ===================== VIEW ANOTHER AGENT'S PROPERTIES =====================

@router.get(
    "/agents/{agent_id}/properties",
    response_model=PaginatedPropertyResponse,
    summary="View an agent's property listings",
    tags=["Agents"]
)
def agent_properties(
    agent_id: str = Path(..., description="Agent UUID"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_agent),
    db: Session = Depends(get_db)
):
    """List all active properties for a specific agent."""
    agent = db.query(Agent).filter(Agent.id == agent_id, Agent.is_active == True).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    query = db.query(Property).filter(
        Property.agent_id == agent_id,
        Property.is_active == True
    )
    total = query.count()
    properties = (
        query.options(*_list_load_options())
        .order_by(Property.created_at.desc())
        .offset(skip).limit(limit).all()
    )

    items = [PropertyService._format_list_response(prop) for prop in properties]
    return PaginatedPropertyResponse(total=total, skip=skip, limit=limit, items=items)


# ===================== ADMIN — PROMOTE USER TO AGENT =====================

@router.post(
    "/admin/promote-agent/{user_id}",
    summary="Promote a user to agent role",
    tags=["Admin"]
)
def promote_to_agent(
    user_id: str = Path(..., description="User UUID to promote"),
    body: PromoteAgentRequest = Body(...),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Link a user account to an agent profile and set their role to agent. Admin only."""
    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    agent = db.query(Agent).filter(Agent.id == body.agent_id, Agent.is_active == True).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent profile not found")

    # Check agent isn't already linked to another user
    existing_link = db.query(User).filter(
        User.agent_id == body.agent_id, User.id != user_id
    ).first()
    if existing_link:
        raise HTTPException(status_code=409, detail="This agent profile is already linked to another user")

    # Additive: keep any existing roles (e.g. staff, admin) and add agent.
    existing_roles = set(user.roles or [])
    existing_roles.add(UserRole.AGENT.value)
    existing_roles.discard(UserRole.USER.value)  # plain "user" is implicit when any role exists

    db.query(UserRoleRow).filter(UserRoleRow.user_id == user.id).delete(synchronize_session=False)
    for role in existing_roles:
        db.add(UserRoleRow(user_id=user.id, role=role))

    user.role = UserRole.AGENT
    user.agent_id = body.agent_id
    db.commit()
    db.refresh(user)

    return {
        "message": f"User '{user.name}' promoted to agent",
        "user_id": user.id,
        "agent_id": user.agent_id,
        "role": user.role.value,
        "roles": user.roles,
    }
