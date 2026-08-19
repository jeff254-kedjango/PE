from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Path, Body, Request, status
from sqlalchemy.orm import Session
from PE.weespas.core.database import get_db
from PE.weespas.services.property_service import PropertyService
from PE.weespas.services.auth_service import require_agent, require_admin, verify_property_ownership, get_current_user_optional
from PE.weespas.services.analytics_service import log_search
from PE.weespas.services.personalization import PersonalFeedService
from PE.weespas.services.celery_helpers import safe_delay
from PE.weespas.core.config import settings
from PE.weespas.models.user import User, UserRole
from PE.weespas.models.property import Property
from PE.weespas.schemas.property import (
    PropertyCreateRequest, PropertyResponse, PropertyListResponse,
    PaginatedPropertyResponse, PropertyFilterParams, PropertyUpdateRequest,
    PropertyCategory, RelatedPropertiesRequest, PaginatedShortsResponse
)

router = APIRouter()


def _session_id(request: Request) -> str | None:
    return getattr(request.state, "session_id", None)


def _dispatch_property_write_fanout(db, property_id: str) -> None:
    """Chord: fan out cache invalidations, then per-user feed blow.

    Per audit §4: when a property mutates, every cache that referenced it is
    stale. The chord runs each invalidator in parallel (group) and only fires
    the per-user fanout once they all complete. Without this, writers either
    leave the cache stale (TTL guessing) or do every invalidation inline.

    Best-effort: failures here never break the write itself.
    """
    if not settings.celery_feed_warm_enabled:
        return
    try:
        from celery import chord, group
        from PE.weespas.models.property import Property as _Prop
        from PE.weespas.models.property import Address as _Addr
        from PE.weespas.services.property_tasks import (
            invalidate_featured_cache,
            invalidate_nearby_cache,
            invalidate_related_for_sources,
            invalidate_agent_stats,
            fanout_invalidate_user_feeds,
        )
        # One indexed lookup to find the city + agent — far cheaper than
        # having each invalidation task re-resolve those keys itself.
        row = (
            db.query(_Prop.agent_id, _Addr.city)
              .join(_Addr, _Addr.property_id == _Prop.id)
              .filter(_Prop.id == property_id)
              .first()
        )
        city = row.city if row else None
        agent_id = row.agent_id if row else None

        chord(
            group(
                invalidate_featured_cache.s(city),
                invalidate_nearby_cache.s(city),
                invalidate_related_for_sources.s(property_id),
                invalidate_agent_stats.s(agent_id),
            ),
            fanout_invalidate_user_feeds.s(property_id=property_id),
        ).apply_async()
    except Exception:
        # Never let a fanout failure break the write.
        pass


def _log_search(db, **kwargs):
    """Dispatch a search log write — Celery when the flag is on, inline otherwise.

    Inline keeps backwards compatibility while the flag is off. Once the
    Celery path proves stable, this wrapper can be removed and the call
    sites can use ``safe_delay(log_search_async, ...)`` directly.
    """
    if settings.celery_log_search_enabled:
        from PE.weespas.services.analytics_tasks import log_search_async
        # The task opens its own DB session — pass only primitives.
        safe_delay(log_search_async, **kwargs)
        return
    log_search(db, **kwargs)


# ===================== PROPERTY LIST =====================

@router.get(
    "/properties",
    response_model=PaginatedPropertyResponse,
    summary="Personalized home feed (anonymous-safe)",
    tags=["Properties"]
)
def list_properties(
    request: Request,
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(10, ge=1, le=100, description="Number of records per page"),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Home-feed listing.

    - Authenticated callers: ranked by personal signals (favorites, recent
      searches, recently viewed) blended with local-trending and freshness.
    - Anonymous callers: ranked by local-trending (session geo if available)
      + freshness + featured boost.

    Ranking is cached per user (or per anon-geo bucket) in Redis with a 5-minute
    TTL; subsequent scroll pages are an O(limit) PK lookup.
    """
    return PersonalFeedService.get_personal_feed(
        db,
        user=current_user,
        session_id=getattr(request.state, "session_id", None),
        skip=skip,
        limit=limit,
    )


# ===================== SEARCH & FILTERING =====================
# These MUST be defined before /properties/{property_id} to avoid route conflicts

@router.get(
    "/properties/search/query",
    response_model=PaginatedPropertyResponse,
    summary="Search properties by keyword",
    tags=["Search"]
)
def search_properties(
    request: Request,
    q: str = Query(..., min_length=1, max_length=100, description="Search query"),
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db)
):
    """
    Full-text search on property title and description.

    **Performance**: Indexed on title for optimal performance.
    """
    result = PropertyService.search_properties(db, q, skip=skip, limit=limit)
    _log_search(
        db,
        session_id=_session_id(request),
        query_text=q,
        result_count=getattr(result, "total", 0) or 0,
    )
    return result


@router.get(
    "/properties/nearby",
    response_model=PaginatedPropertyResponse,
    summary="Find nearby properties",
    tags=["Search"]
)
def get_nearby_properties(
    request: Request,
    latitude: float = Query(..., ge=-90, le=90, description="Latitude"),
    longitude: float = Query(..., ge=-180, le=180, description="Longitude"),
    radius: float = Query(10, gt=0, description="Radius in kilometers"),
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db)
):
    """
    Find all properties within a specified radius of coordinates.

    **Default radius**: 10km
    """
    result = PropertyService.get_nearby_properties(
        db, latitude, longitude, radius,
        skip=skip, limit=limit
    )
    _log_search(
        db,
        session_id=_session_id(request),
        latitude=latitude,
        longitude=longitude,
        radius_km=radius,
        result_count=getattr(result, "total", 0) or 0,
    )
    return result


@router.get(
    "/properties/categories",
    response_model=list[str],
    summary="List all property categories",
    tags=["Properties"]
)
def list_categories():
    """Return all available property category values."""
    return [c.value for c in PropertyCategory]


@router.get(
    "/properties/featured",
    response_model=list[PropertyListResponse],
    summary="Get featured properties",
    tags=["Special"]
)
def get_featured_properties(
    # Carousel shows ALL active featured promotions; cap is a safety bound, not a
    # product limit (admin-curated set is small). Default mirrors the cap so a
    # caller that omits `limit` still gets the full set.
    limit: int = Query(100, ge=1, le=100),
    latitude: Optional[float] = Query(None, ge=-90, le=90, description="User latitude for nearby ranking"),
    longitude: Optional[float] = Query(None, ge=-180, le=180, description="User longitude for nearby ranking"),
    radius: float = Query(25, gt=0, le=200, description="Search radius in km (geo only)"),
    db: Session = Depends(get_db)
):
    """
    Admin-promoted featured properties.

    - Always excludes promotions past `featured_expires_at`.
    - With `latitude`/`longitude`: results are restricted to within `radius` km
      and ranked by proximity, engagement (view count), and relevance.
    - Without geo: falls back to nationwide newest-featured.
    """
    return PropertyService.get_featured_properties(
        db,
        limit=limit,
        latitude=latitude,
        longitude=longitude,
        radius_km=radius,
    )


@router.post(
    "/properties/related",
    response_model=list[PropertyListResponse],
    summary="Ranked related properties for a set of source listings",
    tags=["Special"]
)
def get_related_properties(
    payload: RelatedPropertiesRequest = Body(...),
    db: Session = Depends(get_db)
):
    """
    Given a set of source property IDs (e.g. user's favorites or an agent's
    listings), return up to `limit` related properties ranked by proximity to
    the source centroid, engagement, and similarity (city + bedrooms).

    Source IDs and any `exclude_ids` are always omitted from results.
    """
    return PropertyService.get_related_properties(
        db,
        source_ids=payload.source_ids,
        limit=payload.limit,
        exclude_ids=payload.exclude_ids,
    )


@router.post(
    "/properties/filter",
    response_model=PaginatedPropertyResponse,
    summary="Advanced property filtering",
    tags=["Search"]
)
def filter_properties(
    request: Request,
    filters: PropertyFilterParams = Body(default={}),
    db: Session = Depends(get_db)
):
    """
    Hyper-refined filtering — send any combination of filters or an empty body to get all.

    - **Location (geo)**: latitude, longitude, radius (in km)
    - **Location (text)**: city, county, location_name (partial match)
    - **Price**: min_price, max_price
    - **Category**: house, apartment, villa, studio, office, land, etc.
    - **Listing type**: rent or sale
    - **Attributes**: bedrooms, bathrooms, min_size, max_size, parking_spaces, engineer_certified, is_featured
    - **Sorting**: sort_by (created_at, price, distance), sort_order (asc, desc)

    All filters are optional. Combine with AND logic for hyper-refined search.
    """
    result = PropertyService.filter_properties(db, filters)
    f = filters.model_dump() if hasattr(filters, "model_dump") else (filters.dict() if hasattr(filters, "dict") else {})
    _log_search(
        db,
        session_id=_session_id(request),
        latitude=f.get("latitude"),
        longitude=f.get("longitude"),
        radius_km=f.get("radius"),
        listing_type=f.get("listing_type"),
        min_price=f.get("min_price"),
        max_price=f.get("max_price"),
        result_count=getattr(result, "total", 0) or 0,
    )
    return result


# ===================== SHORTS (vertical video feed) =====================
# Must come BEFORE /properties/{property_id} so the path is matched correctly.

@router.get(
    "/properties/shorts",
    response_model=PaginatedShortsResponse,
    summary="Personalized short-video feed (anonymous-safe)",
    tags=["Properties"]
)
def list_shorts(
    request: Request,
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(10, ge=1, le=50, description="Number of shorts per page"),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Short-video home feed.

    - Same ranking pipeline as the image feed, but the candidate pool is
      restricted to properties that have at least one ``PropertyVideo``.
    - Cached under a separate Redis namespace (``feed:v:*``) so toggling
      between image and video mode never invalidates the other surface.
    - Default page size is 10 to match TikTok-style consumption.
    """
    return PersonalFeedService.get_shorts_feed(
        db,
        user=current_user,
        session_id=_session_id(request),
        skip=skip,
        limit=limit,
    )


# ===================== PROPERTY DETAIL (must come AFTER specific paths) =====================

@router.get(
    "/properties/{property_id}",
    response_model=PropertyResponse,
    summary="Get property details",
    tags=["Properties"]
)
def get_property(
    request: Request,
    property_id: str = Path(..., description="Property UUID"),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Retrieve detailed information for a specific property.

    **Tracking**: View count is incremented; the viewing user's personalized
    feed cache is invalidated asynchronously (so the seen-penalty stays accurate).
    """
    session_id = _session_id(request)
    user_id = current_user.id if current_user else None

    # Phase 1.3: when the offload flag is on, the read path does zero writes —
    # the analytics task does the view-count bump + PropertyViewEvent insert.
    use_offload = settings.celery_record_view_enabled
    property_obj = PropertyService.get_property_by_id(
        db,
        property_id,
        session_id=session_id,
        user_id=user_id,
        record_view=not use_offload,
    )
    if not property_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Property not found"
        )

    if use_offload:
        # Dispatch *after* we know the property exists — never bump views for 404s.
        from datetime import datetime, timezone
        from PE.weespas.services.analytics_tasks import record_property_view
        safe_delay(
            record_property_view,
            property_id, user_id, session_id,
            datetime.now(timezone.utc).isoformat(),
        )

    if current_user is not None:
        try:
            from PE.weespas.services.personalization_tasks import invalidate_user_feed
            invalidate_user_feed.delay(current_user.id)
        except Exception:
            PersonalFeedService.invalidate(current_user.id)
    return property_obj


# ===================== PROPERTY CREATION & MANAGEMENT =====================

@router.post(
    "/properties",
    response_model=PropertyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new property",
    tags=["Properties"]
)
def create_property(
    property_data: PropertyCreateRequest = Body(...),
    current_user: User = Depends(require_agent),
    db: Session = Depends(get_db)
):
    """
    Create a new property listing. Requires agent or admin role.

    Agents: property is automatically assigned to their agent profile.
    Admins: can optionally specify agent_id in the body; otherwise auto-assigned.
    """
    # Admins may pass an explicit agent_id; everyone else (including staff+agent
    # multi-role users) is forced to their own linked agent profile.
    is_admin = current_user.has_role(UserRole.ADMIN)
    if not is_admin or not property_data.agent_id:
        property_data.agent_id = current_user.agent_id

    if not property_data.agent_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Your account is not linked to an agent profile. Ask an admin to link one before creating listings."
        )

    try:
        created = PropertyService.create_property(db, property_data)
        # Phase 4.3 fanout — keep featured / nearby / related / agent caches
        # in sync without TTL-guessing.
        try:
            _dispatch_property_write_fanout(db, created.id if hasattr(created, "id") else None)
        except Exception:
            pass
        # Verify the listing against the InSAR footprints OFF the request thread
        # (the spatial search can be slow). The row is already committed, so a
        # worker crash just leaves it 'pending' for a re-run. The uploader
        # (current_user) gets an inbox notification with the result. safe_delay
        # never raises — the upload response is unaffected if the worker is down.
        try:
            from PE.weespas.services.insar_verify_tasks import verify_listing
            listing_id = created.id if hasattr(created, "id") else None
            if listing_id:
                safe_delay(verify_listing, listing_id, current_user.id, True)
        except Exception:
            pass
        return created
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to create property: {str(e)}"
        )


@router.put(
    "/properties/{property_id}",
    response_model=PropertyResponse,
    summary="Update property",
    tags=["Properties"]
)
def update_property(
    property_id: str,
    update_data: PropertyUpdateRequest = Body(...),
    current_user: User = Depends(require_agent),
    db: Session = Depends(get_db)
):
    """
    Update specific property fields. Requires agent or admin role.

    Agents can only update their own properties.
    Admins can update any property.
    """
    property_obj = db.query(Property).filter(Property.id == property_id).first()
    if not property_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Property not found"
        )
    verify_property_ownership(current_user, property_obj)

    # Non-admins cannot change is_featured, featured_expires_at, or is_active
    if not current_user.has_role(UserRole.ADMIN):
        update_fields = update_data.dict(exclude_unset=True)
        if "is_featured" in update_fields:
            update_data.is_featured = None
        if "featured_expires_at" in update_fields:
            update_data.featured_expires_at = None
        if "is_active" in update_fields:
            update_data.is_active = None

    result = PropertyService.update_property(db, property_id, update_data)
    _dispatch_property_write_fanout(db, property_id)
    return result


@router.delete(
    "/properties/{property_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete property",
    tags=["Properties"]
)
def delete_property(
    property_id: str,
    current_user: User = Depends(require_agent),
    db: Session = Depends(get_db)
):
    """
    Soft delete a property (marks as inactive).
    Agents can only delete their own properties. Admins can delete any.
    """
    property_obj = db.query(Property).filter(Property.id == property_id).first()
    if not property_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Property not found"
        )
    verify_property_ownership(current_user, property_obj)
    # Capture city/agent BEFORE soft-delete so the fanout still resolves them.
    _dispatch_property_write_fanout(db, property_id)
    PropertyService.delete_property(db, property_id)
