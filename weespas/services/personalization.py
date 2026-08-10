"""Personalized "For You" feed for the home page.

Pipeline per request:
  1. Build a cache key (user_id, or 'anon:<geo_city>'/'anon:global').
  2. Redis GET — if hit, slice ranked IDs by skip/limit and bulk-load rows.
  3. If miss: pull a bounded candidate pool, score each candidate with the
     existing ``rank_score`` primitive plus personal signals, mix in a small
     amount of exploration, store the ranked IDs in Redis with a TTL, then
     slice/load.

Performance budget per request (see plan):
  - Hit:  one Redis GET + one PK IN-query w/ eager loads → <10ms p99
  - Miss: four indexed SQL aggregates + O(n) Python scoring of ≤400 rows
          + one Redis SETEX → <120ms p99

The module-level Redis client (``services.cache.redis_client``) is shared
across workers; on Redis unavailability we degrade to recomputing every
request (still correct, just slower).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import random
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from sqlalchemy import desc, func, or_
from sqlalchemy.orm import Session

from PE.weespas.core.config import settings
from PE.weespas.models.analytics import Favorite, PropertyDismissal, PropertyViewEvent, SearchLog, UserSession
from PE.weespas.models.property import Address, Property
from PE.weespas.models.user import User
from PE.weespas.schemas.property import PaginatedPropertyResponse, PaginatedShortsResponse
from PE.weespas.services.cache import (
    feed_anon_key,
    feed_user_key,
    feed_videos_anon_key,
    feed_videos_user_key,
    redis_client,
)
from PE.weespas.services.property_service import PropertyService, _list_load_options, _shorts_load_options

logger = logging.getLogger(__name__)


# ---- Tunables (all keep the model deterministic + auditable) ----------------

MAX_RANKED_IDS = 200          # how many IDs we store in cache per key
CANDIDATE_GEO_RADIUS_KM = 60  # bounding-box radius for geo-aware candidate pull
CANDIDATE_GEO_LIMIT = 300
CANDIDATE_RECENT_LIMIT = 200
CANDIDATE_TRENDING_LIMIT = 50
CANDIDATE_CITY_LIMIT = 50

FAVORITE_LOOKBACK = 50        # last N favorites form the user profile
SEARCH_WINDOW_DAYS = 30
VIEW_WINDOW_DAYS = 30
TRENDING_WINDOW_DAYS = 7
SEEN_PENALTY_WINDOW_DAYS = 14
SEARCH_HALFLIFE_DAYS = 14.0

EXPLORATION_RATIO = 0.15      # ~15% of top slots replaced by mid-pack picks
FRESHNESS_HALFLIFE_DAYS = 30.0

# Score weights (must reflect plan; sum of positive weights = 1.00)
W_FAVORITE = 0.30
W_SEARCH = 0.20
W_RECENT_VIEW = 0.15
W_TRENDING = 0.15
W_FRESHNESS = 0.10
W_FEATURED = 0.10
W_SEEN_PENALTY = 0.20


# ---- Public surface ---------------------------------------------------------


class PersonalFeedService:
    @staticmethod
    def get_personal_feed(
        db: Session,
        user: Optional[User],
        session_id: Optional[str],
        skip: int = 0,
        limit: int = 20,
    ) -> PaginatedPropertyResponse:
        skip = max(0, skip)
        limit = max(1, min(100, limit))

        cache_key, anon_geo_city = _resolve_cache_key(db, user, session_id)
        ranked_ids = _read_cache(cache_key)

        if ranked_ids is None:
            ranked_ids = _compute_ranking(
                db,
                user,
                anon_geo_city,
                session_id if user is None else None,
                cache_key,
            )
            _write_cache(cache_key, ranked_ids)

        total = len(ranked_ids)
        page_ids = ranked_ids[skip : skip + limit]
        items = _load_in_order(db, page_ids) if page_ids else []
        return PaginatedPropertyResponse(total=total, skip=skip, limit=limit, items=items)

    @staticmethod
    def invalidate(user_id: str) -> None:
        """Delete a user's cached feed. Safe to call from any thread/process."""
        try:
            redis_client.delete(feed_user_key(user_id))
            redis_client.delete(feed_videos_user_key(user_id))
        except Exception as exc:  # pragma: no cover - cache is best-effort
            logger.warning("feed cache invalidate failed for %s: %s", user_id, exc)

    @staticmethod
    def get_shorts_feed(
        db: Session,
        *,
        user: Optional[User],
        session_id: Optional[str],
        skip: int = 0,
        limit: int = 10,
    ) -> PaginatedShortsResponse:
        """Personalized short-video feed.

        Same ranking pipeline as ``get_personal_feed`` but the candidate pool
        is filtered to properties that have at least one video. Cached under a
        separate namespace (``feed:v:*``) so the image and video feeds do not
        invalidate each other.
        """
        skip = max(0, skip)
        limit = max(1, min(50, limit))

        cache_key, anon_geo_city = _resolve_videos_cache_key(db, user, session_id)
        ranked_ids = _read_cache(cache_key)

        if ranked_ids is None:
            ranked_ids = _compute_ranking(
                db,
                user,
                anon_geo_city,
                session_id if user is None else None,
                cache_key,
                videos_only=True,
            )
            _write_cache(cache_key, ranked_ids)

        total = len(ranked_ids)
        page_ids = ranked_ids[skip : skip + limit]
        items = _load_shorts_in_order(db, page_ids) if page_ids else []
        return PaginatedShortsResponse(total=total, skip=skip, limit=limit, items=items)


# ---- Cache helpers ----------------------------------------------------------


def _resolve_cache_key(
    db: Session, user: Optional[User], session_id: Optional[str]
) -> Tuple[str, Optional[str]]:
    """Return (redis_key, anon_geo_city). anon_geo_city is only set in the
    anonymous path; it feeds into ranking too."""
    if user is not None:
        return feed_user_key(user.id), None

    geo_city = _session_geo_city(db, session_id)
    geo_bucket = (geo_city or "global").lower().strip()
    # Bucket anon visitors by (city, short-hash(session_id)) so each session
    # gets its own ranking. Without this, every anon hitting the "global"
    # bucket sees an identical list for 5 minutes.
    # 10 hex chars = 40 bits — collision risk is negligible at our scale and
    # we accept it as "two strangers occasionally share a bucket".
    if session_id:
        sid_short = hashlib.blake2s(session_id.encode("utf-8"), digest_size=5).hexdigest()
        bucket = f"{geo_bucket}:{sid_short}"
    else:
        bucket = geo_bucket
    return feed_anon_key(bucket), geo_city


def _resolve_videos_cache_key(
    db: Session, user: Optional[User], session_id: Optional[str]
) -> Tuple[str, Optional[str]]:
    """Same bucketing rules as ``_resolve_cache_key`` but in the ``feed:v:*``
    namespace so the shorts feed never collides with the image feed."""
    if user is not None:
        return feed_videos_user_key(user.id), None

    geo_city = _session_geo_city(db, session_id)
    geo_bucket = (geo_city or "global").lower().strip()
    if session_id:
        sid_short = hashlib.blake2s(session_id.encode("utf-8"), digest_size=5).hexdigest()
        bucket = f"{geo_bucket}:{sid_short}"
    else:
        bucket = geo_bucket
    return feed_videos_anon_key(bucket), geo_city


def _session_geo_city(db: Session, session_id: Optional[str]) -> Optional[str]:
    if not session_id:
        return None
    row = (
        db.query(UserSession.geo_city, UserSession.geo_county)
        .filter(UserSession.id == session_id)
        .first()
    )
    if not row:
        return None
    return row.geo_city or row.geo_county


def _read_cache(key: str) -> Optional[List[str]]:
    try:
        raw = redis_client.get(key)
    except Exception as exc:  # pragma: no cover - degrade gracefully
        logger.warning("feed cache read failed: %s", exc)
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _write_cache(key: str, ranked_ids: List[str]) -> None:
    if not ranked_ids:
        return
    try:
        redis_client.setex(key, settings.feed_cache_ttl, json.dumps(ranked_ids))
    except Exception as exc:  # pragma: no cover
        logger.warning("feed cache write failed: %s", exc)


# ---- Bulk load with order preserved ----------------------------------------


def _load_in_order(db: Session, ids: List[str]):
    rows = (
        db.query(Property)
        .options(*_list_load_options())
        .filter(Property.id.in_(ids), Property.is_active == True)  # noqa: E712
        .all()
    )
    by_id = {p.id: p for p in rows}
    ordered = [by_id[pid] for pid in ids if pid in by_id]
    return [PropertyService._format_list_response(p) for p in ordered]


def _load_shorts_in_order(db: Session, ids: List[str]):
    """Bulk-load shorts in cached order. Skips properties that have lost their
    last video between cache write and read (rare; defensive)."""
    rows = (
        db.query(Property)
        .options(*_shorts_load_options())
        .filter(Property.id.in_(ids), Property.is_active == True)  # noqa: E712
        .all()
    )
    by_id = {p.id: p for p in rows}
    ordered = [by_id[pid] for pid in ids if pid in by_id]
    formatted = []
    for p in ordered:
        item = PropertyService._format_short_response(p)
        if item is not None:
            formatted.append(item)
    return formatted


# ---- Candidate pool ---------------------------------------------------------


def _candidate_pool(
    db: Session,
    user: Optional[User],
    user_geo_lat: Optional[float],
    user_geo_lng: Optional[float],
    user_favorite_city: Optional[str],
    anon_geo_city: Optional[str],
    videos_only: bool = False,
) -> List[Property]:
    """Bounded, eager-loaded set of candidates. Always dedup'd by id.

    Three sources are unioned in priority order:
      1) Nearby (bounding-box on user's geo or session geo, when available).
      2) Same-city as user's favorite (if known).
      3) Globally newest (always — guarantees a non-empty pool).

    When ``videos_only`` is True the pool is restricted to properties that have
    at least one ``PropertyVideo`` row (compiles to a correlated EXISTS on the
    FK-indexed ``property_videos.property_id``). Per-branch limits are scaled up
    to compensate for filter selectivity. Eager-load options are swapped to a
    video-aware loader so the caller does not need a second round-trip.
    """
    pool: dict[str, Property] = {}

    load_options = _shorts_load_options() if videos_only else _list_load_options()
    # Filter selectivity adjustment: video coverage is sparse in v1, so widen
    # each branch by ~1.5x to keep the post-filter pool large enough to score.
    branch_mult = 1.5 if videos_only else 1.0
    geo_limit = int(CANDIDATE_GEO_LIMIT * branch_mult)
    city_limit = int(CANDIDATE_CITY_LIMIT * branch_mult)
    recent_limit = int(CANDIDATE_RECENT_LIMIT * branch_mult)

    def _apply_video_filter(q):
        if videos_only:
            return q.filter(Property.videos.any())
        return q

    # 1. Nearby (only when we have coordinates from session geo)
    if user_geo_lat is not None and user_geo_lng is not None:
        min_lat, max_lat, min_lon, max_lon = PropertyService._bounding_box(
            user_geo_lat, user_geo_lng, CANDIDATE_GEO_RADIUS_KM
        )
        q = (
            db.query(Property)
            .options(*load_options)
            .join(Address, Property.id == Address.property_id)
            .filter(Property.is_active == True)  # noqa: E712
            .filter(Address.latitude.between(min_lat, max_lat))
            .filter(Address.longitude.between(min_lon, max_lon))
        )
        nearby = (
            _apply_video_filter(q)
            .order_by(desc(Property.created_at))
            .limit(geo_limit)
            .all()
        )
        for p in nearby:
            pool[p.id] = p

    # 2. Same-city as user's top favorite, or anon's session city
    target_city = user_favorite_city or anon_geo_city
    if target_city:
        q = (
            db.query(Property)
            .options(*load_options)
            .join(Address, Property.id == Address.property_id)
            .filter(Property.is_active == True, Address.city.ilike(target_city))  # noqa: E712
        )
        same_city = (
            _apply_video_filter(q)
            .order_by(desc(Property.created_at))
            .limit(city_limit)
            .all()
        )
        for p in same_city:
            pool.setdefault(p.id, p)

    # 3. Globally newest — always pulled (guarantees non-empty)
    q = (
        db.query(Property)
        .options(*load_options)
        .filter(Property.is_active == True)  # noqa: E712
    )
    newest = (
        _apply_video_filter(q)
        .order_by(desc(Property.created_at))
        .limit(recent_limit)
        .all()
    )
    for p in newest:
        pool.setdefault(p.id, p)

    return list(pool.values())


# ---- Profile builders -------------------------------------------------------


class _FavoritesProfile:
    __slots__ = ("cities", "categories", "listing_types", "bedrooms_mode", "price_min", "price_max", "favorite_ids")

    def __init__(self) -> None:
        self.cities: Counter = Counter()
        self.categories: Counter = Counter()
        self.listing_types: Counter = Counter()
        self.bedrooms_mode: Optional[int] = None
        self.price_min: Optional[float] = None
        self.price_max: Optional[float] = None
        self.favorite_ids: set[str] = set()

    @property
    def top_city(self) -> Optional[str]:
        return self.cities.most_common(1)[0][0] if self.cities else None


def _favorites_profile(db: Session, user: User) -> _FavoritesProfile:
    profile = _FavoritesProfile()

    rows = (
        db.query(Property)
        .options(_list_load_options()[0])  # joinedload(address) only — enough here
        .join(Favorite, Favorite.property_id == Property.id)
        .filter(Favorite.user_id == user.id)
        .order_by(desc(Favorite.created_at))
        .limit(FAVORITE_LOOKBACK)
        .all()
    )
    if not rows:
        return profile

    bedrooms_counter: Counter = Counter()
    prices: List[float] = []
    for p in rows:
        profile.favorite_ids.add(p.id)
        if p.address and p.address.city:
            profile.cities[p.address.city.strip().lower()] += 1
        if p.category_id:
            profile.categories[p.category_id] += 1
        if p.listing_type:
            profile.listing_types[p.listing_type] += 1
        if p.bedrooms is not None:
            bedrooms_counter[p.bedrooms] += 1
        if p.price is not None:
            prices.append(float(p.price))

    if bedrooms_counter:
        profile.bedrooms_mode = bedrooms_counter.most_common(1)[0][0]
    if prices:
        prices.sort()
        lo = prices[int(0.1 * (len(prices) - 1))]
        hi = prices[int(0.9 * (len(prices) - 1))]
        # Soft floors/ceilings so single-price users still get a band.
        profile.price_min = lo * 0.7
        profile.price_max = hi * 1.3
    return profile


class _SearchProfile:
    __slots__ = ("category_ids", "listing_types", "price_min", "price_max", "lat", "lng", "radius_km")

    def __init__(self) -> None:
        self.category_ids: Counter = Counter()
        self.listing_types: Counter = Counter()
        self.price_min: Optional[float] = None
        self.price_max: Optional[float] = None
        self.lat: Optional[float] = None
        self.lng: Optional[float] = None
        self.radius_km: Optional[float] = None


def _search_profile(
    db: Session,
    user: Optional[User] = None,
    session_id: Optional[str] = None,
) -> _SearchProfile:
    """Build a search profile from either a user's history or a session's history.

    Indexed scan: SearchLog has indexes on both user_id and session_id, so this
    is a single B-tree range scan + LIMIT 200 in either branch.
    """
    profile = _SearchProfile()
    if user is None and not session_id:
        return profile
    cutoff = datetime.now(timezone.utc) - timedelta(days=SEARCH_WINDOW_DAYS)

    q = db.query(SearchLog).filter(SearchLog.created_at >= cutoff)
    if user is not None:
        q = q.filter(SearchLog.user_id == user.id)
    else:
        q = q.filter(SearchLog.session_id == session_id)
    rows = q.order_by(desc(SearchLog.created_at)).limit(200).all()
    if not rows:
        return profile

    now = datetime.now(timezone.utc)
    weighted_min: List[Tuple[float, float]] = []
    weighted_max: List[Tuple[float, float]] = []
    weighted_lat: List[Tuple[float, float]] = []
    weighted_lng: List[Tuple[float, float]] = []
    weighted_radius: List[Tuple[float, float]] = []
    for row in rows:
        # Exponential time decay: weight = 2^(-age_days / halflife)
        row_created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (now - row_created).total_seconds() / 86400.0)
        w = 0.5 ** (age_days / SEARCH_HALFLIFE_DAYS)
        if row.category_id:
            profile.category_ids[row.category_id] += w
        if row.listing_type:
            profile.listing_types[row.listing_type.lower()] += w
        if row.min_price is not None:
            weighted_min.append((row.min_price, w))
        if row.max_price is not None:
            weighted_max.append((row.max_price, w))
        if row.latitude is not None and row.longitude is not None:
            weighted_lat.append((float(row.latitude), w))
            weighted_lng.append((float(row.longitude), w))
            if row.radius_km is not None:
                weighted_radius.append((float(row.radius_km), w))

    def _wavg(pairs: List[Tuple[float, float]]) -> Optional[float]:
        if not pairs:
            return None
        total_w = sum(w for _, w in pairs)
        if total_w <= 0:
            return None
        return sum(v * w for v, w in pairs) / total_w

    profile.price_min = _wavg(weighted_min)
    profile.price_max = _wavg(weighted_max)
    profile.lat = _wavg(weighted_lat)
    profile.lng = _wavg(weighted_lng)
    profile.radius_km = _wavg(weighted_radius)
    return profile


def _dismissed_property_ids(db: Session, user: User) -> set[str]:
    rows = (
        db.query(PropertyDismissal.property_id)
        .filter(PropertyDismissal.user_id == user.id)
        .all()
    )
    return {pid for (pid,) in rows}


def _viewed_property_ids(
    db: Session,
    days: int,
    user: Optional[User] = None,
    session_id: Optional[str] = None,
) -> set[str]:
    """Distinct property_ids viewed in the window, scoped to a user OR a session.

    Both user_id and session_id are indexed on PropertyViewEvent, so this is
    a single bounded index scan returning at most a few hundred rows.
    """
    if user is None and not session_id:
        return set()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    q = db.query(PropertyViewEvent.property_id).filter(PropertyViewEvent.viewed_at >= cutoff)
    if user is not None:
        q = q.filter(PropertyViewEvent.user_id == user.id)
    else:
        q = q.filter(PropertyViewEvent.session_id == session_id)
    return {pid for (pid,) in q.distinct().all()}


def _user_session_geo(db: Session, user: User) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """Most-recent session geo for the user. Used for nearby candidate pull."""
    row = (
        db.query(UserSession.geo_lat, UserSession.geo_lng, UserSession.geo_city)
        .filter(UserSession.user_id == user.id)
        .order_by(desc(UserSession.last_seen_at))
        .first()
    )
    if not row:
        return None, None, None
    lat = float(row.geo_lat) if row.geo_lat is not None else None
    lng = float(row.geo_lng) if row.geo_lng is not None else None
    return lat, lng, row.geo_city


def _trending_counts(db: Session, anon_geo_city: Optional[str], candidate_ids: List[str]) -> dict[str, int]:
    """Views per property in the trending window, optionally restricted to a city.
    Returns dict keyed by property_id (only for candidates we care about)."""
    if not candidate_ids:
        return {}
    cutoff = datetime.now(timezone.utc) - timedelta(days=TRENDING_WINDOW_DAYS)
    q = (
        db.query(PropertyViewEvent.property_id, func.count().label("c"))
        .filter(PropertyViewEvent.viewed_at >= cutoff)
        .filter(PropertyViewEvent.property_id.in_(candidate_ids))
    )
    if anon_geo_city:
        # Anon path: bias trending to the session's city via the address.
        q = (
            q.join(Property, Property.id == PropertyViewEvent.property_id)
             .join(Address, Address.property_id == Property.id)
             .filter(Address.city.ilike(anon_geo_city))
        )
    q = q.group_by(PropertyViewEvent.property_id)
    return {pid: int(c) for pid, c in q.all()}


# ---- Scoring ---------------------------------------------------------------


def _normalize_listing_type(value) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "value"):
        return str(value.value).lower()
    return str(value).lower()


def _favorite_match(prop: Property, fav: _FavoritesProfile) -> float:
    if not fav.cities and not fav.categories and not fav.listing_types:
        return 0.0
    score = 0.0
    if fav.cities and prop.address and prop.address.city:
        if fav.cities.get(prop.address.city.strip().lower(), 0) > 0:
            score += 0.35
    if fav.categories and prop.category_id and fav.categories.get(prop.category_id, 0) > 0:
        score += 0.25
    if fav.listing_types and prop.listing_type and fav.listing_types.get(prop.listing_type, 0) > 0:
        score += 0.15
    if fav.bedrooms_mode is not None and prop.bedrooms == fav.bedrooms_mode:
        score += 0.10
    if fav.price_min is not None and fav.price_max is not None and prop.price is not None:
        price = float(prop.price)
        if fav.price_min <= price <= fav.price_max:
            score += 0.15
    return min(1.0, score)


def _search_match(prop: Property, sp: _SearchProfile) -> float:
    if not sp.category_ids and not sp.listing_types and sp.lat is None and sp.price_min is None:
        return 0.0
    score = 0.0
    if sp.category_ids and prop.category_id and sp.category_ids.get(prop.category_id, 0) > 0:
        score += 0.30
    if sp.listing_types and prop.listing_type is not None:
        lt = _normalize_listing_type(prop.listing_type)
        if lt and sp.listing_types.get(lt, 0) > 0:
            score += 0.20
    if sp.price_min is not None and sp.price_max is not None and prop.price is not None:
        price = float(prop.price)
        if sp.price_min <= price <= sp.price_max:
            score += 0.20
    if sp.lat is not None and sp.lng is not None and prop.address and prop.address.latitude is not None:
        dist = PropertyService.haversine_distance(
            sp.lat, sp.lng, float(prop.address.latitude), float(prop.address.longitude)
        )
        radius = sp.radius_km or 25.0
        if dist <= radius:
            # 1.0 at centre, 0 at edge
            score += 0.30 * max(0.0, 1.0 - (dist / radius))
    return min(1.0, score)


def _as_utc(value: datetime) -> datetime:
    """Treat naive datetimes from older rows as UTC (matches server_default=now)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _freshness(prop: Property, now: datetime) -> float:
    if not prop.created_at:
        return 0.0
    age_days = max(0.0, (now - _as_utc(prop.created_at)).total_seconds() / 86400.0)
    return math.exp(-age_days / FRESHNESS_HALFLIFE_DAYS)


def _featured_boost(prop: Property, now: datetime) -> float:
    """Feed boost for a featured listing, trust-graded so the promotion aligns with the
    anti-scam/safety mission: every active featured listing is boosted (base 0.6), but
    engineer-certified / verified-agent ones reach full strength. Mirrors the carousel's
    trust_signal so the two surfaces agree. The overall W_FEATURED weight stays small —
    this is a nudge, not a takeover of favourite/search relevance."""
    if not prop.is_featured:
        return 0.0
    if prop.featured_expires_at and _as_utc(prop.featured_expires_at) <= now:
        return 0.0
    base = 0.6
    if getattr(prop, "is_engineer_certified", False):
        base += 0.25
    agent = getattr(prop, "agent", None)
    if agent is not None and getattr(agent, "is_verified", False):
        base += 0.15
    return min(1.0, base)


def _compute_ranking(
    db: Session,
    user: Optional[User],
    anon_geo_city: Optional[str],
    anon_session_id: Optional[str] = None,
    cache_key: Optional[str] = None,
    videos_only: bool = False,
) -> List[str]:
    """The full miss-path: build profiles, gather candidates, score, mix, slice.

    Anonymous users still get personalized rankings when ``anon_session_id`` is
    provided: their session-scoped SearchLog and PropertyViewEvent rows feed
    the search-profile and recent-view signals, so two anons in the same city
    with different browsing histories see different feeds.
    """
    now = datetime.now(timezone.utc)
    has_signals = user is not None or bool(anon_session_id)

    # ---- Profiles
    fav_profile = _FavoritesProfile()
    search_profile = _SearchProfile()
    seen_ids: set[str] = set()
    dismissed_ids: set[str] = set()
    user_geo_lat: Optional[float] = None
    user_geo_lng: Optional[float] = None
    if user is not None:
        fav_profile = _favorites_profile(db, user)
        search_profile = _search_profile(db, user=user)
        seen_ids = _viewed_property_ids(db, SEEN_PENALTY_WINDOW_DAYS, user=user)
        dismissed_ids = _dismissed_property_ids(db, user)
        user_geo_lat, user_geo_lng, _ = _user_session_geo(db, user)
    elif anon_session_id:
        # Anon visitor with a session — pull their own searches/views so the
        # ranker differentiates them from other anons in the same geo.
        search_profile = _search_profile(db, session_id=anon_session_id)
        seen_ids = _viewed_property_ids(
            db, SEEN_PENALTY_WINDOW_DAYS, session_id=anon_session_id
        )

    # ---- Candidate pool
    favorite_city = fav_profile.top_city
    candidates = _candidate_pool(
        db,
        user=user,
        user_geo_lat=user_geo_lat,
        user_geo_lng=user_geo_lng,
        user_favorite_city=favorite_city,
        anon_geo_city=anon_geo_city,
        videos_only=videos_only,
    )
    if not candidates:
        return []

    # Exclude properties the user has already favorited from the surfaced feed
    # (they live on the Favorites page; surfacing them here is noise).
    if fav_profile.favorite_ids:
        candidates = [c for c in candidates if c.id not in fav_profile.favorite_ids]
        if not candidates:
            return []

    # Hard-exclude dismissed properties — explicit negative user intent.
    if dismissed_ids:
        candidates = [c for c in candidates if c.id not in dismissed_ids]
        if not candidates:
            return []

    candidate_ids = [c.id for c in candidates]

    # ---- Trending bucket (single SQL aggregate over the candidate set)
    trending = _trending_counts(db, anon_geo_city if user is None else None, candidate_ids)
    max_trend = max(trending.values()) if trending else 0

    # ---- Score
    # Search/view signals apply to both authed users and anon-with-session.
    # Favorites apply only to authed users (anons can't favorite).
    scored: List[Tuple[Property, float]] = []
    in_seen = seen_ids.__contains__
    for prop in candidates:
        fav_s = _favorite_match(prop, fav_profile) if user is not None else 0.0
        search_s = _search_match(prop, search_profile) if has_signals else 0.0
        seen_hit = has_signals and in_seen(prop.id)
        view_s = 0.5 if seen_hit else 0.0  # weak positive (recent interest)
        trend_s = (trending.get(prop.id, 0) / max_trend) if max_trend else 0.0
        fresh_s = _freshness(prop, now)
        feat_s = _featured_boost(prop, now)
        seen_pen = W_SEEN_PENALTY if seen_hit else 0.0

        score = (
            W_FAVORITE * fav_s
            + W_SEARCH * search_s
            + W_RECENT_VIEW * view_s
            + W_TRENDING * trend_s
            + W_FRESHNESS * fresh_s
            + W_FEATURED * feat_s
            - seen_pen
        )
        scored.append((prop, score))

    scored.sort(key=lambda x: x[1], reverse=True)

    # ---- Exploration mix: swap ~15% of top-50 with random next-100 picks
    head = [p for p, _ in scored[:MAX_RANKED_IDS]]
    if len(head) > 20:
        head = _mix_in_exploration(head, cache_key)

    return [p.id for p in head]


def _mix_in_exploration(ranked: List[Property], seed_key: Optional[str] = None) -> List[Property]:
    """Swap a fraction of top slots with random picks from the rest.

    Keeps the feed from collapsing to one neighborhood or one agent.
    Seeded by ``seed_key`` (the cache key) so each user/session gets its own
    stable shuffle — two visitors with otherwise-identical scores won't end
    up with byte-identical feeds.
    """
    n = len(ranked)
    top_zone = min(50, n)
    tail_zone_start = top_zone
    tail_zone_end = min(n, top_zone + 100)
    if tail_zone_end <= tail_zone_start:
        return ranked

    swap_count = max(1, int(top_zone * EXPLORATION_RATIO))
    rng = random.Random(seed_key) if seed_key else random.Random()
    # Pick distinct indices (cap at zone size)
    top_indices = rng.sample(range(top_zone), min(swap_count, top_zone))
    tail_indices = rng.sample(range(tail_zone_start, tail_zone_end), min(swap_count, tail_zone_end - tail_zone_start))

    result = list(ranked)
    for t, b in zip(top_indices, tail_indices):
        result[t], result[b] = result[b], result[t]
    return result
