from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy import and_, or_, func, desc, asc
from PE.weespas.models.property import Property, PropertyImage, Agent, Address, PropertyCategory
from PE.weespas.models.property import PropertyListingType as ModelListingType
from PE.weespas.models.property import VERIFICATION_PENDING
from PE.weespas.schemas.property import (
    PropertyCreateRequest, PropertyResponse, PropertyListResponse,
    PaginatedPropertyResponse, PropertyFilterParams, PropertyUpdateRequest,
    PropertyListingType, PropertyCategory as PropertyCategoryEnum,
    PropertyShortResponse, PropertyShortVideoEmbed,
)
from PE.weespas.services.ranking import Item, rank_score, trust_signal
from PE.weespas.services import geo_fuzz
from PE.weespas.models.insar_link import BuildingLink
import math
from collections import Counter
from datetime import datetime, timezone
from typing import Optional, List, Tuple


def _list_load_options():
    """Eager-load options for property list queries (address, agent, main image, category)."""
    return [
        joinedload(Property.address),
        joinedload(Property.agent),
        joinedload(Property.category),
        selectinload(Property.images),
    ]


def _monitored_ids(db: Session, candidate_ids: list[str]) -> set[str]:
    """Listing ids (from `candidate_ids`) that have an InSAR BuildingLink — i.e. fall
    inside a monitored AOI. One batched IN-query (no N+1, no per-request DuckDB); used
    as a small trust nudge in featured ranking. Empty input → empty set."""
    if not candidate_ids:
        return set()
    rows = (
        db.query(BuildingLink.listing_id)
        .filter(BuildingLink.listing_id.in_(candidate_ids))
        .all()
    )
    return {r[0] for r in rows}


def _detail_load_options():
    """Eager-load options for property detail queries (all relationships)."""
    return [
        joinedload(Property.address),
        joinedload(Property.agent),
        joinedload(Property.category),
        selectinload(Property.images),
        selectinload(Property.videos),
    ]


def _shorts_load_options():
    """Eager-load options for the shorts feed: address/agent/category plus
    both images (for poster fallback) and videos (one will be embedded)."""
    return [
        joinedload(Property.address),
        joinedload(Property.agent),
        joinedload(Property.category),
        selectinload(Property.images),
        selectinload(Property.videos),
    ]


class PropertyService:
    """
    Service layer for property operations with enterprise-level performance.
    Optimized for millions of concurrent users.
    """

    @staticmethod
    def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """
        Calculate distance between two coordinates using Haversine formula.
        Returns distance in kilometers.
        """
        R = 6371.0  # Earth's radius in km
        lat1_rad = math.radians(lat1)
        lon1_rad = math.radians(lon1)
        lat2_rad = math.radians(lat2)
        lon2_rad = math.radians(lon2)

        dlat = lat2_rad - lat1_rad
        dlon = lon2_rad - lon1_rad

        a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        return R * c

    @staticmethod
    def _bounding_box(lat: float, lon: float, radius_km: float) -> Tuple[float, float, float, float]:
        """
        Compute a lat/lon bounding box for a given radius (km).
        Returns (min_lat, max_lat, min_lon, max_lon).
        Used as a SQL pre-filter before accurate haversine calculation.
        """
        # ~111 km per degree latitude
        delta_lat = radius_km / 111.0
        # longitude degrees shrink with latitude
        delta_lon = radius_km / (111.0 * math.cos(math.radians(lat)))
        return (lat - delta_lat, lat + delta_lat, lon - delta_lon, lon + delta_lon)

    @staticmethod
    def get_properties_paginated(
        db: Session,
        skip: int = 0,
        limit: int = 20
    ) -> PaginatedPropertyResponse:
        """
        Get all properties with pagination.
        Performance: O(log n) with proper indexing on created_at.
        """
        query = db.query(Property).filter(Property.is_active == True)
        total = query.count()

        properties = (
            query.options(*_list_load_options())
            .order_by(desc(Property.created_at))
            .offset(skip).limit(limit).all()
        )

        items = [PropertyService._format_list_response(prop) for prop in properties]

        return PaginatedPropertyResponse(
            total=total,
            skip=skip,
            limit=limit,
            items=items
        )

    @staticmethod
    def create_property(
        db: Session,
        property_data: PropertyCreateRequest
    ) -> PropertyResponse:
        """
        Create a new property with address and agent association.
        Performance: Single insert with cascading relationships.
        """
        try:
            # Look up category by slug
            cat = db.query(PropertyCategory).filter(
                PropertyCategory.slug == property_data.category.value
            ).first()
            if not cat:
                raise ValueError(f"Invalid category: {property_data.category}")

            # Create address
            address = Address(
                location_name=property_data.location_name,
                latitude=property_data.latitude,
                longitude=property_data.longitude
            )

            # Create property
            db_property = Property(
                title=property_data.title,
                description=property_data.description,
                price=property_data.price,
                currency=property_data.currency,
                listing_type=ModelListingType(property_data.listing_type.value),
                category_id=cat.id,
                is_engineer_certified=property_data.is_engineer_certified,
                bedrooms=property_data.bedrooms,
                bathrooms=property_data.bathrooms,
                size=property_data.size,
                size_numeric=property_data.size_numeric,
                parking_spaces=property_data.parking_spaces,
                year_built=property_data.year_built,
                agent_id=property_data.agent_id,
                # Honest default: the background verify task hasn't run yet.
                verification_status=VERIFICATION_PENDING,
                address=address
            )
            
            db.add(db_property)
            db.commit()

            # Re-query with eager loading — db.refresh() does NOT reload
            # relationships (expire_on_commit=True causes lazy-load N+1)
            db_property = (
                db.query(Property)
                .options(*_detail_load_options())
                .filter(Property.id == db_property.id)
                .first()
            )

            return PropertyService._format_detail_response(db_property)
        except Exception as e:
            db.rollback()
            raise e

    @staticmethod
    def get_property_by_id(
        db: Session,
        property_id: str,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        record_view: bool = True,
    ) -> Optional[PropertyResponse]:
        """
        Get property by ID with all relationships eagerly loaded.

        When ``record_view`` is True (legacy fallback), this method bumps
        ``view_count`` and inserts a ``PropertyViewEvent`` inline before
        returning. When False, the caller is responsible for dispatching
        ``analytics.record_property_view`` to Celery — used by the Phase 1.3
        offload so the read endpoint does zero writes.
        """
        # Eager-loaded query up front — when we skip the analytics writes, we
        # no longer need a separate "fetch lightweight, commit, refetch" dance.
        # Falls back to the two-step pattern only when we still write inline,
        # to preserve the original commit-then-refetch behavior.
        if not record_view:
            property_obj = (
                db.query(Property)
                .options(*_detail_load_options())
                .filter(Property.id == property_id)
                .first()
            )
            if not property_obj:
                return None
            return PropertyService._format_detail_response(property_obj)

        # Legacy inline path — kept until celery_record_view_enabled is on
        # in production for 7+ days and Phase 1.3 verification passes.
        property_obj = db.query(Property).filter(Property.id == property_id).first()

        if not property_obj:
            return None

        property_obj.view_count += 1

        try:
            from PE.weespas.models.analytics import PropertyViewEvent
            db.add(PropertyViewEvent(
                property_id=property_id,
                user_id=user_id,
                session_id=session_id,
            ))
        except Exception:
            pass

        db.commit()

        property_obj = (
            db.query(Property)
            .options(*_detail_load_options())
            .filter(Property.id == property_id)
            .first()
        )
        return PropertyService._format_detail_response(property_obj)

    @staticmethod
    def get_nearby_properties(
        db: Session,
        latitude: float,
        longitude: float,
        radius: float,
        skip: int = 0,
        limit: int = 20
    ) -> PaginatedPropertyResponse:
        """
        Get properties within a specified radius using haversine distance.
        Performance: Geo-indexed query with database-level filtering recommended for production.
        For SQLite, we do client-side filtering (acceptable for moderate datasets).
        For production: Use PostGIS with native geo-indexes.
        """
        query = db.query(Property).filter(Property.is_active == True)
        query = query.join(Address, Property.id == Address.property_id)

        # Bounding box pre-filter: drastically reduces rows before haversine
        min_lat, max_lat, min_lon, max_lon = PropertyService._bounding_box(latitude, longitude, radius)
        query = query.filter(
            Address.latitude.between(min_lat, max_lat),
            Address.longitude.between(min_lon, max_lon),
        )

        all_properties = query.options(*_list_load_options()).all()
        nearby_properties = []

        for prop in all_properties:
            distance = PropertyService.haversine_distance(
                latitude, longitude,
                prop.address.latitude, prop.address.longitude
            )
            if distance <= radius:
                nearby_properties.append((prop, distance))
        
        total = len(nearby_properties)
        nearby_properties.sort(key=lambda x: x[1])
        
        paginated = nearby_properties[skip:skip + limit]
        items = [PropertyService._format_list_response(prop, distance) for prop, distance in paginated]
        
        return PaginatedPropertyResponse(
            total=total,
            skip=skip,
            limit=limit,
            items=items
        )

    @staticmethod
    def filter_properties(
        db: Session,
        filters: PropertyFilterParams
    ) -> PaginatedPropertyResponse:
        """
        All filters optional. No filters = return all active properties.
        Any combination of filters = AND logic for hyper-refined search.
        """
        query = db.query(Property).filter(Property.is_active == True)

        # Only join Address when a location filter is actually provided
        has_location_filter = (
            filters.city is not None
            or filters.county is not None
            or filters.location_name is not None
            or (filters.latitude is not None and filters.longitude is not None and filters.radius is not None)
        )

        if has_location_filter:
            query = query.join(Address, Property.id == Address.property_id)

        # Build filters list — only add conditions for non-None values
        conditions = []

        # Listing type
        if filters.listing_type is not None:
            try:
                model_lt = ModelListingType(filters.listing_type.value)
                conditions.append(Property.listing_type == model_lt)
            except (ValueError, KeyError):
                pass

        # Category — lookup by slug, skip filter if category not found in DB
        if filters.category is not None:
            cat = db.query(PropertyCategory).filter(
                PropertyCategory.slug == filters.category.value
            ).first()
            if cat:
                conditions.append(Property.category_id == cat.id)

        # Price range
        if filters.min_price is not None:
            conditions.append(Property.price >= filters.min_price)

        if filters.max_price is not None:
            conditions.append(Property.price <= filters.max_price)

        # Engineer certified
        if filters.engineer_certified is not None:
            conditions.append(Property.is_engineer_certified == filters.engineer_certified)

        # Featured
        if filters.is_featured is not None:
            conditions.append(Property.is_featured == filters.is_featured)

        # Bedrooms (minimum)
        if filters.bedrooms is not None:
            conditions.append(Property.bedrooms >= filters.bedrooms)

        # Bathrooms (minimum)
        if filters.bathrooms is not None:
            conditions.append(Property.bathrooms >= filters.bathrooms)

        # Size range
        if filters.min_size is not None:
            conditions.append(Property.size_numeric >= filters.min_size)

        if filters.max_size is not None:
            conditions.append(Property.size_numeric <= filters.max_size)

        # Parking spaces (minimum)
        if filters.parking_spaces is not None:
            conditions.append(Property.parking_spaces >= filters.parking_spaces)

        # Year built (minimum)
        if filters.year_built is not None:
            conditions.append(Property.year_built >= filters.year_built)

        # Text-based location (case-insensitive partial match)
        if filters.city is not None:
            conditions.append(Address.city.ilike(f"%{filters.city}%"))

        if filters.county is not None:
            conditions.append(Address.county.ilike(f"%{filters.county}%"))

        if filters.location_name is not None:
            conditions.append(Address.location_name.ilike(f"%{filters.location_name}%"))

        # Text search on title/description (sent by frontend as 'query')
        if filters.query is not None and filters.query.strip():
            q = filters.query.strip()
            conditions.append(
                or_(
                    Property.title.ilike(f"%{q}%"),
                    Property.description.ilike(f"%{q}%")
                )
            )

        # Apply ALL collected conditions at once
        if conditions:
            query = query.filter(and_(*conditions))

        # Geo-spatial filtering (must happen after all SQL filters)
        use_geo = (
            filters.latitude is not None
            and filters.longitude is not None
            and filters.radius is not None
        )

        if use_geo:
            # Bounding box pre-filter to avoid full table scan
            min_lat, max_lat, min_lon, max_lon = PropertyService._bounding_box(
                filters.latitude, filters.longitude, filters.radius
            )
            # Ensure Address join is present (may already be joined for text filters)
            if not has_location_filter:
                query = query.join(Address, Property.id == Address.property_id)
            query = query.filter(
                Address.latitude.between(min_lat, max_lat),
                Address.longitude.between(min_lon, max_lon),
            )

            all_props = query.options(*_list_load_options()).all()
            properties_in_radius = []
            for prop in all_props:
                if not prop.address:
                    continue
                dist = PropertyService.haversine_distance(
                    filters.latitude, filters.longitude,
                    prop.address.latitude, prop.address.longitude
                )
                if dist <= filters.radius:
                    properties_in_radius.append((prop, dist))

            total = len(properties_in_radius)

            if filters.sort_by == "distance":
                properties_in_radius.sort(key=lambda x: x[1], reverse=(filters.sort_order == "desc"))
            elif filters.sort_by == "price":
                properties_in_radius.sort(key=lambda x: float(x[0].price), reverse=(filters.sort_order == "desc"))
            else:
                properties_in_radius.sort(key=lambda x: x[0].created_at, reverse=(filters.sort_order == "desc"))

            paginated = properties_in_radius[filters.skip:filters.skip + filters.limit]
            items = [PropertyService._format_list_response(prop, dist) for prop, dist in paginated]
        else:
            # Standard sorting (no geo)
            if filters.sort_by == "price":
                sort_col = desc(Property.price) if filters.sort_order == "desc" else asc(Property.price)
            elif filters.sort_by == "distance":
                sort_col = desc(Property.created_at)
            else:
                sort_col = desc(Property.created_at) if filters.sort_order == "desc" else asc(Property.created_at)

            total = query.count()
            properties = query.options(*_list_load_options()).order_by(sort_col).offset(filters.skip).limit(filters.limit).all()
            items = [PropertyService._format_list_response(prop) for prop in properties]

        return PaginatedPropertyResponse(
            total=total,
            skip=filters.skip,
            limit=filters.limit,
            items=items
        )

    @staticmethod
    def update_property(
        db: Session,
        property_id: str,
        update_data: PropertyUpdateRequest
    ) -> Optional[PropertyResponse]:
        """
        Update property fields selectively.
        Only updates non-null fields.
        """
        property_obj = db.query(Property).filter(Property.id == property_id).first()

        if not property_obj:
            return None

        update_fields = update_data.dict(exclude_unset=True)

        for field, value in update_fields.items():
            if value is not None and hasattr(property_obj, field):
                # Convert schema enum to model enum for listing_type
                if field == "listing_type":
                    value = ModelListingType(value.value if hasattr(value, 'value') else value)
                setattr(property_obj, field, value)

        db.commit()

        # Re-query with eager loading — commit expires relationships
        property_obj = (
            db.query(Property)
            .options(*_detail_load_options())
            .filter(Property.id == property_id)
            .first()
        )

        return PropertyService._format_detail_response(property_obj)

    @staticmethod
    def delete_property(db: Session, property_id: str) -> bool:
        """
        Soft delete: Mark property as inactive.
        For hard delete: Use property_obj.delete() which cascades.
        """
        property_obj = db.query(Property).filter(Property.id == property_id).first()
        
        if not property_obj:
            return False
        
        property_obj.is_active = False
        db.commit()
        
        return True

    @staticmethod
    def search_properties(
        db: Session,
        query_string: str,
        skip: int = 0,
        limit: int = 20
    ) -> PaginatedPropertyResponse:
        """
        Full-text search on property title and description.
        Performance: Indexed on title for production use.
        For production: Use PostgreSQL full-text search or Elasticsearch.
        """
        query = db.query(Property).filter(Property.is_active == True)
        
        search_filter = or_(
            Property.title.ilike(f"%{query_string}%"),
            Property.description.ilike(f"%{query_string}%")
        )
        
        query = query.filter(search_filter)
        total = query.count()

        properties = query.options(*_list_load_options()).order_by(desc(Property.created_at)).offset(skip).limit(limit).all()
        
        items = [PropertyService._format_list_response(prop) for prop in properties]
        
        return PaginatedPropertyResponse(
            total=total,
            skip=skip,
            limit=limit,
            items=items
        )

    @staticmethod
    def get_featured_properties(
        db: Session,
        limit: int = 10,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        radius_km: float = 25.0,
        now: Optional[datetime] = None,
    ) -> List[PropertyListResponse]:
        """
        Admin-promoted featured properties.

        - Always filters out promotions whose `featured_expires_at` is in the past
          (NULL is treated as "no expiry" so legacy rows still show).
        - When `latitude`/`longitude` are provided, candidates are restricted to a
          bounding box around the user, scored with services.ranking.rank_score
          (proximity dominant at 0.55, TRUST/relevance 0.35 via trust_signal —
          engineer-certified / verified-agent / InSAR-monitored —, engagement 0.10),
          and returned with `distance` populated.
        - Without geo, leads with the most trustworthy featured listings
          (trust_signal), recency as tiebreak, over a bounded recent over-fetch.

        Geo NEVER returns empty when featured listings exist: if fewer than `limit`
        fall inside the radius, the result is topped up with the nationwide trust-
        sorted featured listings (deduped, distance omitted) so the carousel always
        has content after "search with my location".
        """
        current_time = now or datetime.now(timezone.utc)
        not_expired = or_(
            Property.featured_expires_at.is_(None),
            Property.featured_expires_at > current_time,
        )
        base_filter = and_(
            Property.is_featured == True,
            Property.is_active == True,
            not_expired,
        )

        has_geo = latitude is not None and longitude is not None
        if not has_geo:
            rows = PropertyService._featured_nationwide(db, base_filter, limit)
            return [PropertyService._format_list_response(prop) for prop in rows]

        # Geo path: bounding box pre-filter then haversine, then weighted ranking.
        min_lat, max_lat, min_lon, max_lon = PropertyService._bounding_box(
            latitude, longitude, radius_km
        )
        candidates = (
            db.query(Property)
            .options(*_list_load_options())
            .join(Address, Property.id == Address.property_id)
            .filter(base_filter)
            .filter(
                Address.latitude.between(min_lat, max_lat),
                Address.longitude.between(min_lon, max_lon),
            )
            .all()
        )

        monitored = _monitored_ids(db, [c.id for c in candidates])
        scored: List[Tuple[Property, float, float]] = []
        for prop in candidates:
            if not prop.address:
                continue
            distance = PropertyService.haversine_distance(
                latitude, longitude,
                float(prop.address.latitude), float(prop.address.longitude),
            )
            if distance > radius_km:
                continue
            # Trust (safety/anti-scam) is the relevance term: proximity stays dominant,
            # but among nearby featured listings the certified/verified/monitored ones win.
            score = rank_score(
                Item(
                    id=0,
                    distance_km=distance,
                    clicks=0,  # no click tracking yet → engagement term inert (see ranking.py)
                    views=prop.view_count or 0,
                    relevance_score=trust_signal(prop, monitored_ids=monitored),
                ),
                w_d=0.55, w_e=0.10, w_r=0.35,
            )
            scored.append((prop, distance, score))

        scored.sort(key=lambda x: x[2], reverse=True)
        top = scored[:limit]
        results = [
            PropertyService._format_list_response(prop, dist) for prop, dist, _ in top
        ]

        # Fallback: never let "near me" empty the carousel. If too few featured
        # listings are in-radius, top up with the best nationwide featured ones
        # (no distance — they're outside the radius), skipping any already shown.
        if len(results) < limit:
            seen = {prop.id for prop, _, _ in top}
            # Request enough that, after removing the already-shown in-radius ones,
            # we can still fill up to `limit`.
            fillers = PropertyService._featured_nationwide(db, base_filter, limit + len(seen))
            for prop in fillers:
                if len(results) >= limit:
                    break
                if prop.id in seen:
                    continue
                seen.add(prop.id)
                results.append(PropertyService._format_list_response(prop))

        return results

    @staticmethod
    def _featured_nationwide(db: Session, base_filter, limit: int) -> List[Property]:
        """Nationwide featured listings, most TRUSTWORTHY first (recency as tiebreak).

        Over-fetches a bounded multiple of `limit` by recency, then re-sorts by
        (trust, recency) in Python so a newly-certified-but-older listing can still
        surface — without an unbounded scan. Shared by the no-geo path and the geo
        top-up so both agree on ordering.
        """
        rows = (
            db.query(Property)
            .options(*_list_load_options())
            .filter(base_filter)
            .order_by(desc(Property.created_at))
            .limit(max(limit * 4, 40))
            .all()
        )
        monitored = _monitored_ids(db, [r.id for r in rows])
        epoch = datetime.min.replace(tzinfo=timezone.utc)
        rows.sort(
            key=lambda p: (
                trust_signal(p, monitored_ids=monitored),
                p.created_at or epoch,
            ),
            reverse=True,
        )
        return rows[:limit]

    @staticmethod
    def get_related_properties(
        db: Session,
        source_ids: List[str],
        limit: int = 12,
        exclude_ids: Optional[List[str]] = None,
    ) -> List[PropertyListResponse]:
        """
        Ranked related-properties recommender.

        Pipeline:
          1. Load the source set with addresses + categories.
          2. Derive target signals: mode(category), mode(listing_type),
             mode(city), bedrooms mode, centroid lat/lng, magnitude-aware
             price band.
          3. SQL candidate query (active + same category + same listing type
             + price band, excluding source/exclude ids), capped at 200 newest.
          4. Score each candidate with services.ranking.rank_score using
             distance to centroid, view_count engagement, and a composite
             relevance score (city match + bedrooms match + baseline).
          5. Fall back to no-price-band, then to newest-in-category, if scoring
             yields nothing — guarantees the carousel never returns empty when
             there are any plausible candidates.
        """
        if not source_ids:
            return []

        exclude_set = set(exclude_ids or []) | set(source_ids)

        sources = (
            db.query(Property)
            .options(joinedload(Property.address), joinedload(Property.category))
            .filter(Property.id.in_(source_ids))
            .all()
        )
        if not sources:
            return []

        category_ids = [s.category_id for s in sources if s.category_id]
        listing_types = [s.listing_type for s in sources if s.listing_type]
        cities = [s.address.city for s in sources if s.address and s.address.city]
        bedroom_values = [s.bedrooms for s in sources if s.bedrooms is not None]
        prices = [float(s.price) for s in sources if s.price is not None]
        coords = [
            (float(s.address.latitude), float(s.address.longitude))
            for s in sources
            if s.address and s.address.latitude is not None and s.address.longitude is not None
        ]

        target_category_id = Counter(category_ids).most_common(1)[0][0] if category_ids else None
        target_listing_type = Counter(listing_types).most_common(1)[0][0] if listing_types else None
        target_city = Counter(cities).most_common(1)[0][0] if cities else None
        target_bedrooms = Counter(bedroom_values).most_common(1)[0][0] if bedroom_values else None

        if coords:
            centroid_lat = sum(lat for lat, _ in coords) / len(coords)
            centroid_lng = sum(lng for _, lng in coords) / len(coords)
        else:
            centroid_lat = centroid_lng = None

        if prices:
            mean_price = sum(prices) / len(prices)
            if mean_price < 5_000_000:
                spread = 0.5
            elif mean_price < 50_000_000:
                spread = 0.25
            else:
                spread = 0.15
            min_price = max(0.0, min(prices) * (1.0 - spread))
            max_price = max(prices) * (1.0 + spread)
        else:
            min_price = max_price = None

        def candidate_query(apply_price_band: bool):
            q = (
                db.query(Property)
                .options(*_list_load_options())
                .filter(Property.is_active == True)
                .filter(~Property.id.in_(exclude_set))
            )
            if target_category_id is not None:
                q = q.filter(Property.category_id == target_category_id)
            if target_listing_type is not None:
                q = q.filter(Property.listing_type == target_listing_type)
            if apply_price_band and min_price is not None and max_price is not None:
                q = q.filter(Property.price >= min_price, Property.price <= max_price)
            return q.order_by(desc(Property.created_at)).limit(200).all()

        candidates = candidate_query(apply_price_band=True)
        if not candidates:
            candidates = candidate_query(apply_price_band=False)
        if not candidates:
            # Last-ditch fallback: newest active in target category, ignoring listing_type.
            fallback_q = (
                db.query(Property)
                .options(*_list_load_options())
                .filter(Property.is_active == True)
                .filter(~Property.id.in_(exclude_set))
            )
            if target_category_id is not None:
                fallback_q = fallback_q.filter(Property.category_id == target_category_id)
            candidates = fallback_q.order_by(desc(Property.created_at)).limit(limit).all()

        if not candidates:
            return []

        # Score each candidate.
        DEFAULT_DISTANCE_KM = 50.0  # used when no source coords known
        scored: List[Tuple[Property, float, float]] = []
        for prop in candidates:
            if centroid_lat is not None and prop.address and prop.address.latitude is not None:
                distance = PropertyService.haversine_distance(
                    centroid_lat, centroid_lng,
                    float(prop.address.latitude), float(prop.address.longitude),
                )
            else:
                distance = DEFAULT_DISTANCE_KM

            relevance = 0.3  # baseline (category + listing + price already match)
            if target_city and prop.address and prop.address.city == target_city:
                relevance += 0.4
            if target_bedrooms is not None and prop.bedrooms == target_bedrooms:
                relevance += 0.3
            relevance = min(1.0, relevance)

            score = rank_score(
                Item(
                    id=0,
                    distance_km=distance,
                    clicks=0,
                    views=prop.view_count or 0,
                    relevance_score=relevance,
                ),
                w_d=0.4, w_e=0.3, w_r=0.3,
            )
            scored.append((prop, distance, score))

        scored.sort(key=lambda x: x[2], reverse=True)
        top = scored[:limit]
        return [PropertyService._format_list_response(prop, dist) for prop, dist, _ in top]

    @staticmethod
    def _fuzzed_latlon(property_obj: Property) -> tuple[float, float]:
        """Fuzzed (lat, lon) for a list payload, or (0, 0) when there's no address
        (matching the prior fallback). Single place so list + detail blur identically."""
        if not property_obj.address:
            return 0, 0
        return geo_fuzz.fuzz_coords(
            property_obj.address.latitude, property_obj.address.longitude,
            listing_id=property_obj.id,
        )

    @staticmethod
    def _format_detail_response(property_obj: Property) -> PropertyResponse:
        """
        Format property object for detailed response with all relationships.
        Performance: Assumes relationships are already loaded (eager loading).
        """
        agent_data = None
        if property_obj.agent:
            agent_data = {
                "id": property_obj.agent.id,
                "agent_name": property_obj.agent.agent_name,
                "agent_phone_number": property_obj.agent.agent_phone_number,
                "agent_profile_picture": property_obj.agent.agent_profile_picture,
                "email": property_obj.agent.email,
                "bio": property_obj.agent.bio,
                "is_verified": property_obj.agent.is_verified,
                "is_active": property_obj.agent.is_active,
                "created_at": property_obj.agent.created_at,
                "updated_at": property_obj.agent.updated_at
            }
        
        address_data = None
        if property_obj.address:
            # Coordinates are FUZZED here, unconditionally. The exact lat/lon is the
            # paid good (commercial_model.md §3.4) and is served ONLY by the dedicated
            # /reveal endpoint after an entitlement check — never in this (cacheable)
            # detail/list payload. Fuzzing at the single serializer means no endpoint
            # can accidentally leak the precise pin. street_address is coarsened too
            # (house number is as revealing as the pin).
            fz_lat, fz_lon = geo_fuzz.fuzz_coords(
                property_obj.address.latitude, property_obj.address.longitude,
                listing_id=property_obj.id,
            )
            address_data = {
                "id": property_obj.address.id,
                "location_name": property_obj.address.location_name,
                "street_address": geo_fuzz.coarse_address(property_obj.address.street_address),
                "city": property_obj.address.city,
                "county": property_obj.address.county,
                "postal_code": property_obj.address.postal_code,
                "country": property_obj.address.country,
                "latitude": fz_lat,
                "longitude": fz_lon,
                "created_at": property_obj.address.created_at,
                "updated_at": property_obj.address.updated_at
            }
        
        images_data = [
            {
                "id": img.id,
                "url": img.url,
                "thumbnail_url": img.thumbnail_url,
                "alt_text": img.alt_text,
                "order": img.order,
                "is_main": img.is_main,
                "file_size": img.file_size,
                "mime_type": img.mime_type,
                "created_at": img.created_at
            } for img in property_obj.images
        ]
        
        videos_data = [
            {
                "id": vid.id,
                "url": vid.url,
                "thumbnail_url": vid.thumbnail_url,
                "streaming_url": vid.streaming_url,
                "title": vid.title,
                "description": vid.description,
                "duration": vid.duration,
                "order": vid.order,
                "file_size": vid.file_size,
                "mime_type": vid.mime_type,
                "created_at": vid.created_at
            } for vid in property_obj.videos
        ]
        
        return PropertyResponse(
            id=property_obj.id,
            title=property_obj.title,
            description=property_obj.description,
            price=float(property_obj.price),
            currency=property_obj.currency,
            listing_type=property_obj.listing_type.value,
            category=property_obj.category.slug if property_obj.category else property_obj.category_id,
            is_engineer_certified=property_obj.is_engineer_certified,
            bedrooms=property_obj.bedrooms,
            bathrooms=property_obj.bathrooms,
            size=property_obj.size,
            size_numeric=property_obj.size_numeric,
            parking_spaces=property_obj.parking_spaces,
            year_built=property_obj.year_built,
            address=address_data,
            agent=agent_data,
            images=images_data,
            videos=videos_data,
            is_active=property_obj.is_active,
            is_featured=property_obj.is_featured,
            view_count=property_obj.view_count,
            created_at=property_obj.created_at,
            updated_at=property_obj.updated_at,
            expires_at=property_obj.expires_at,
            featured_expires_at=property_obj.featured_expires_at,
            verification_status=property_obj.verification_status or "pending",
        )

    @staticmethod
    def _format_list_response(property_obj: Property, distance: Optional[float] = None) -> PropertyListResponse:
        """
        Format property object for list response (lightweight).
        Performance: Minimal data transfer for pagination.
        """
        main_image = None
        if property_obj.images:
            main = next((img for img in property_obj.images if img.is_main), None)
            if not main and property_obj.images:
                main = property_obj.images[0]
            
            if main:
                main_image = {
                    "id": main.id,
                    "url": main.url,
                    "thumbnail_url": main.thumbnail_url,
                    "alt_text": main.alt_text,
                    "order": main.order,
                    "is_main": main.is_main,
                    "file_size": main.file_size,
                    "mime_type": main.mime_type,
                    "created_at": main.created_at
                }
        
        return PropertyListResponse(
            id=property_obj.id,
            title=property_obj.title,
            price=float(property_obj.price),
            currency=property_obj.currency,
            listing_type=property_obj.listing_type.value,
            category=property_obj.category.slug if property_obj.category else property_obj.category_id,
            is_engineer_certified=property_obj.is_engineer_certified,
            location_name=property_obj.address.location_name if property_obj.address else "Unknown",
            # Fuzzed (see _format_detail_response). List/feed payloads never carry the
            # exact pin; /reveal does. _fuzzed_latlon returns (0, 0) for the no-address
            # edge case, preserving prior behaviour.
            **dict(zip(("latitude", "longitude"), PropertyService._fuzzed_latlon(property_obj))),
            agent_name=property_obj.agent.agent_name if property_obj.agent else None,
            main_image=main_image,
            is_featured=property_obj.is_featured,
            featured_expires_at=property_obj.featured_expires_at,
            view_count=property_obj.view_count,
            created_at=property_obj.created_at,
            distance=distance,
            bedrooms=property_obj.bedrooms,
            bathrooms=property_obj.bathrooms
        )

    @staticmethod
    def _format_short_response(property_obj: Property) -> Optional[PropertyShortResponse]:
        """Format a property for the short-video feed. Returns ``None`` if the
        property has no video (callers should skip these silently — the
        candidate pool already filters for ``Property.videos.any()`` but the
        in-memory list may have changed since cache write).
        """
        if not property_obj.videos:
            return None

        # First by order (ascending) — matches PropertyVideo.order index intent.
        first_video = sorted(
            property_obj.videos, key=lambda v: (v.order if v.order is not None else 0)
        )[0]

        main_image = None
        if property_obj.images:
            main = next((img for img in property_obj.images if img.is_main), None)
            if not main:
                main = property_obj.images[0]
            if main:
                main_image = {
                    "id": main.id,
                    "url": main.url,
                    "thumbnail_url": main.thumbnail_url,
                    "alt_text": main.alt_text,
                    "order": main.order,
                    "is_main": main.is_main,
                    "file_size": main.file_size,
                    "mime_type": main.mime_type,
                    "created_at": main.created_at,
                }

        return PropertyShortResponse(
            id=property_obj.id,
            title=property_obj.title,
            price=float(property_obj.price),
            currency=property_obj.currency,
            listing_type=property_obj.listing_type.value,
            category=property_obj.category.slug if property_obj.category else property_obj.category_id,
            agent_name=property_obj.agent.agent_name if property_obj.agent else None,
            location_name=property_obj.address.location_name if property_obj.address else "Unknown",
            main_image=main_image,
            video=PropertyShortVideoEmbed(
                url=first_video.url,
                streaming_url=first_video.streaming_url,
                thumbnail_url=first_video.thumbnail_url,
                duration=first_video.duration,
            ),
            is_featured=property_obj.is_featured,
            bedrooms=property_obj.bedrooms,
            bathrooms=property_obj.bathrooms,
        )