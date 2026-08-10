"""Analytics aggregation + event-logging helpers.

All write helpers are best-effort — failures must never break the request path.
Aggregations are read-only and simple SQL — no Redis cache yet (can be added
without changing the public function signatures).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, case, text
from sqlalchemy.orm import Session

from PE.weespas.models.analytics import (
    UserSession, PropertyViewEvent, SearchLog, Favorite,
)
from PE.weespas.models.property import Property, PropertyCategory, Address, PropertyListingType
from PE.weespas.models.contact import ContactSubmission
from PE.weespas.models.insar_link import (
    BuildingLink, StructuralFlag, FLAG_UNSAFE, FLAG_AUTH_UNSAFE,
)

logger = logging.getLogger(__name__)


# Composite weighting for "interest" — kept here so it can be tuned in one place.
WEIGHT_VIEW = 1.0
WEIGHT_SEARCH = 2.0
WEIGHT_FAVORITE = 3.0
WEIGHT_INQUIRY = 5.0


# ===================== EVENT LOGGING =====================

def log_search(
    db: Session,
    *,
    session_id: Optional[str],
    user_id: Optional[str] = None,
    query_text: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    radius_km: Optional[float] = None,
    category_id: Optional[str] = None,
    listing_type: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    result_count: int = 0,
) -> None:
    try:
        db.add(SearchLog(
            session_id=session_id,
            user_id=user_id,
            query_text=query_text,
            latitude=latitude,
            longitude=longitude,
            radius_km=radius_km,
            category_id=category_id,
            listing_type=listing_type,
            min_price=min_price,
            max_price=max_price,
            result_count=result_count,
        ))
        db.commit()
    except Exception as e:
        logger.warning("log_search failed: %s", e)
        try:
            db.rollback()
        except Exception:
            pass


# ===================== HELPERS =====================

def _since_dt(since: Optional[str]) -> Optional[datetime]:
    """Parse a relative window like '7d', '30d', '90d', 'all'. Returns None for 'all'."""
    if not since or since == "all":
        return None
    try:
        if since.endswith("d"):
            days = int(since[:-1])
            return datetime.now(timezone.utc) - timedelta(days=days)
    except Exception:
        pass
    return datetime.now(timezone.utc) - timedelta(days=30)


# ===================== SUMMARY =====================

def aggregate_summary(db: Session, since: Optional[str] = "30d") -> dict:
    cutoff = _since_dt(since)

    sess_q = db.query(func.count(UserSession.id))
    if cutoff:
        sess_q = sess_q.filter(UserSession.created_at >= cutoff)

    view_q = db.query(func.count(PropertyViewEvent.id))
    if cutoff:
        view_q = view_q.filter(PropertyViewEvent.viewed_at >= cutoff)

    search_q = db.query(func.count(SearchLog.id))
    if cutoff:
        search_q = search_q.filter(SearchLog.created_at >= cutoff)

    fav_q = db.query(func.count(Favorite.id))
    if cutoff:
        fav_q = fav_q.filter(Favorite.created_at >= cutoff)

    inq_q = db.query(func.count(ContactSubmission.id)).filter(
        ContactSubmission.property_id.isnot(None)
    )
    if cutoff:
        inq_q = inq_q.filter(ContactSubmission.created_at >= cutoff)

    return {
        "since": since or "all",
        "sessions": sess_q.scalar() or 0,
        "views": view_q.scalar() or 0,
        "searches": search_q.scalar() or 0,
        "favorites": fav_q.scalar() or 0,
        "inquiries": inq_q.scalar() or 0,
    }


# ===================== CATEGORY INTEREST =====================

def aggregate_categories(db: Session, since: Optional[str] = "30d") -> list[dict]:
    cutoff = _since_dt(since)

    # Views per category — join PropertyViewEvent → Property → PropertyCategory
    view_q = (
        db.query(PropertyCategory.id, PropertyCategory.slug, PropertyCategory.name,
                 func.count(PropertyViewEvent.id).label("c"))
        .join(Property, Property.category_id == PropertyCategory.id)
        .join(PropertyViewEvent, PropertyViewEvent.property_id == Property.id)
        .group_by(PropertyCategory.id, PropertyCategory.slug, PropertyCategory.name)
    )
    if cutoff:
        view_q = view_q.filter(PropertyViewEvent.viewed_at >= cutoff)
    views = {row[0]: (row[1], row[2], row[3]) for row in view_q.all()}

    # Favorites per category
    fav_q = (
        db.query(PropertyCategory.id, func.count(Favorite.id).label("c"))
        .join(Property, Property.category_id == PropertyCategory.id)
        .join(Favorite, Favorite.property_id == Property.id)
        .group_by(PropertyCategory.id)
    )
    if cutoff:
        fav_q = fav_q.filter(Favorite.created_at >= cutoff)
    favs = {row[0]: row[1] for row in fav_q.all()}

    # Inquiries per category (via ContactSubmission.property_id)
    inq_q = (
        db.query(PropertyCategory.id, func.count(ContactSubmission.id).label("c"))
        .join(Property, Property.category_id == PropertyCategory.id)
        .join(ContactSubmission, ContactSubmission.property_id == Property.id)
        .group_by(PropertyCategory.id)
    )
    if cutoff:
        inq_q = inq_q.filter(ContactSubmission.created_at >= cutoff)
    inqs = {row[0]: row[1] for row in inq_q.all()}

    # Searches by category_id directly
    s_q = (
        db.query(SearchLog.category_id, func.count(SearchLog.id))
        .filter(SearchLog.category_id.isnot(None))
        .group_by(SearchLog.category_id)
    )
    if cutoff:
        s_q = s_q.filter(SearchLog.created_at >= cutoff)
    searches = {row[0]: row[1] for row in s_q.all()}

    # All categories so we can show even zero-engagement ones if asked; return only non-zero
    cat_rows = db.query(PropertyCategory.id, PropertyCategory.slug, PropertyCategory.name).all()

    out: list[dict] = []
    for cid, slug, name in cat_rows:
        v = views.get(cid, (None, None, 0))[2] if cid in views else 0
        f = favs.get(cid, 0)
        i = inqs.get(cid, 0)
        s = searches.get(cid, 0)
        score = v * WEIGHT_VIEW + s * WEIGHT_SEARCH + f * WEIGHT_FAVORITE + i * WEIGHT_INQUIRY
        if score == 0:
            continue
        out.append({
            "category_id": cid,
            "slug": slug,
            "name": name,
            "view_count": v,
            "search_count": s,
            "favorite_count": f,
            "inquiry_count": i,
            "score": score,
        })
    out.sort(key=lambda r: r["score"], reverse=True)
    return out


# ===================== PRICE HISTOGRAM =====================

PRICE_BUCKETS_SALE = [
    (0, 5_000_000), (5_000_000, 10_000_000), (10_000_000, 25_000_000),
    (25_000_000, 50_000_000), (50_000_000, 100_000_000), (100_000_000, None),
]
PRICE_BUCKETS_RENT = [
    (0, 25_000), (25_000, 50_000), (50_000, 100_000),
    (100_000, 200_000), (200_000, 500_000), (500_000, None),
]


def aggregate_prices(
    db: Session,
    since: Optional[str] = "30d",
    listing_type: Optional[str] = None,
) -> dict:
    """Histogram of property prices weighted by user engagement.

    For each property, engagement = views + 2*searches-on-it + 3*favs + 5*inquiries.
    We then bucket by Property.price.
    """
    cutoff = _since_dt(since)

    # Per-property engagement counts
    view_subq = (
        db.query(PropertyViewEvent.property_id, func.count("*").label("c"))
        .group_by(PropertyViewEvent.property_id)
    )
    if cutoff:
        view_subq = view_subq.filter(PropertyViewEvent.viewed_at >= cutoff)
    view_subq = view_subq.subquery()

    fav_subq = (
        db.query(Favorite.property_id, func.count("*").label("c"))
        .group_by(Favorite.property_id)
    )
    if cutoff:
        fav_subq = fav_subq.filter(Favorite.created_at >= cutoff)
    fav_subq = fav_subq.subquery()

    inq_subq = (
        db.query(ContactSubmission.property_id, func.count("*").label("c"))
        .filter(ContactSubmission.property_id.isnot(None))
        .group_by(ContactSubmission.property_id)
    )
    if cutoff:
        inq_subq = inq_subq.filter(ContactSubmission.created_at >= cutoff)
    inq_subq = inq_subq.subquery()

    q = (
        db.query(
            Property.id,
            Property.price,
            Property.listing_type,
            func.coalesce(view_subq.c.c, 0).label("v"),
            func.coalesce(fav_subq.c.c, 0).label("f"),
            func.coalesce(inq_subq.c.c, 0).label("i"),
        )
        .outerjoin(view_subq, view_subq.c.property_id == Property.id)
        .outerjoin(fav_subq, fav_subq.c.property_id == Property.id)
        .outerjoin(inq_subq, inq_subq.c.property_id == Property.id)
        .filter(Property.is_active == True)  # noqa: E712
    )
    if listing_type:
        # Property.listing_type is an Enum
        q = q.filter(Property.listing_type == PropertyListingType(listing_type))

    rows = q.all()

    def _bucket(price: float, lt) -> str:
        buckets = (
            PRICE_BUCKETS_RENT if lt == PropertyListingType.RENT else PRICE_BUCKETS_SALE
        )
        for lo, hi in buckets:
            if hi is None and price >= lo:
                return f"{int(lo):,}+"
            if hi is not None and lo <= price < hi:
                return f"{int(lo):,}-{int(hi):,}"
        return "uncategorized"

    sale: dict[str, float] = {}
    rent: dict[str, float] = {}
    for pid, price, lt, v, f, i in rows:
        if price is None:
            continue
        engagement = (
            (v or 0) * WEIGHT_VIEW
            + (f or 0) * WEIGHT_FAVORITE
            + (i or 0) * WEIGHT_INQUIRY
        )
        if engagement == 0:
            continue
        bucket = _bucket(float(price), lt)
        target = rent if lt == PropertyListingType.RENT else sale
        target[bucket] = target.get(bucket, 0.0) + engagement

    def _series(buckets, totals):
        out = []
        for lo, hi in buckets:
            label = f"{int(lo):,}+" if hi is None else f"{int(lo):,}-{int(hi):,}"
            out.append({"bucket": label, "score": totals.get(label, 0.0)})
        return out

    return {
        "since": since or "all",
        "listing_type": listing_type,
        "sale": _series(PRICE_BUCKETS_SALE, sale),
        "rent": _series(PRICE_BUCKETS_RENT, rent),
    }


# ===================== HEATMAPS =====================

def aggregate_access_heatmap(
    db: Session,
    since: Optional[str] = "30d",
    county: Optional[str] = None,
) -> list[dict]:
    """Where users access the app from (UserSession.geo_*).

    - county is None → group by county
    - county set → group by city within that county
    """
    cutoff = _since_dt(since)

    if county:
        q = (
            db.query(
                UserSession.geo_city.label("name"),
                func.avg(UserSession.geo_lat).label("lat"),
                func.avg(UserSession.geo_lng).label("lng"),
                func.count(UserSession.id).label("weight"),
            )
            .filter(UserSession.geo_county == county)
            .filter(UserSession.geo_lat.isnot(None))
            .filter(UserSession.geo_lng.isnot(None))
            .group_by(UserSession.geo_city)
        )
    else:
        q = (
            db.query(
                UserSession.geo_county.label("name"),
                func.avg(UserSession.geo_lat).label("lat"),
                func.avg(UserSession.geo_lng).label("lng"),
                func.count(UserSession.id).label("weight"),
            )
            .filter(UserSession.geo_lat.isnot(None))
            .filter(UserSession.geo_lng.isnot(None))
            .group_by(UserSession.geo_county)
        )
    if cutoff:
        q = q.filter(UserSession.created_at >= cutoff)

    return [
        {
            "name": r.name,
            "lat": float(r.lat) if r.lat is not None else None,
            "lng": float(r.lng) if r.lng is not None else None,
            "weight": int(r.weight or 0),
        }
        for r in q.all()
        if r.lat is not None and r.lng is not None
    ]


def aggregate_interest_heatmap(
    db: Session,
    since: Optional[str] = "30d",
    county: Optional[str] = None,
) -> list[dict]:
    """Where people are *moving to* — interest in property locations.

    Composite of views + searches + favorites + inquiries on properties whose
    Address falls in a given county/city.
    """
    cutoff = _since_dt(since)

    # Per-property engagement
    view_subq = db.query(PropertyViewEvent.property_id, func.count("*").label("c"))
    if cutoff:
        view_subq = view_subq.filter(PropertyViewEvent.viewed_at >= cutoff)
    view_subq = view_subq.group_by(PropertyViewEvent.property_id).subquery()

    fav_subq = db.query(Favorite.property_id, func.count("*").label("c"))
    if cutoff:
        fav_subq = fav_subq.filter(Favorite.created_at >= cutoff)
    fav_subq = fav_subq.group_by(Favorite.property_id).subquery()

    inq_subq = (
        db.query(ContactSubmission.property_id, func.count("*").label("c"))
        .filter(ContactSubmission.property_id.isnot(None))
    )
    if cutoff:
        inq_subq = inq_subq.filter(ContactSubmission.created_at >= cutoff)
    inq_subq = inq_subq.group_by(ContactSubmission.property_id).subquery()

    weight_expr = (
        func.coalesce(view_subq.c.c, 0) * WEIGHT_VIEW
        + func.coalesce(fav_subq.c.c, 0) * WEIGHT_FAVORITE
        + func.coalesce(inq_subq.c.c, 0) * WEIGHT_INQUIRY
    )

    group_col = Address.city if county else Address.county

    q = (
        db.query(
            group_col.label("name"),
            func.avg(Address.latitude).label("lat"),
            func.avg(Address.longitude).label("lng"),
            func.sum(weight_expr).label("weight"),
        )
        .select_from(Property)
        .join(Address, Address.property_id == Property.id)
        .outerjoin(view_subq, view_subq.c.property_id == Property.id)
        .outerjoin(fav_subq, fav_subq.c.property_id == Property.id)
        .outerjoin(inq_subq, inq_subq.c.property_id == Property.id)
        .group_by(group_col)
    )
    if county:
        q = q.filter(Address.county == county)

    rows = q.all()

    # Add searches with explicit lat/lng — only at county level, since we don't
    # reverse-geocode them. At city level (when `county` is set) raw search
    # points would not be scoped to the drill-down area and would visually pollute it.
    if county:
        search_points: list[dict] = []
    else:
        search_q = db.query(SearchLog.latitude, SearchLog.longitude).filter(
            SearchLog.latitude.isnot(None), SearchLog.longitude.isnot(None)
        )
        if cutoff:
            search_q = search_q.filter(SearchLog.created_at >= cutoff)
        search_points = [
            {"name": None, "lat": float(lat), "lng": float(lng), "weight": WEIGHT_SEARCH}
            for lat, lng in search_q.all()
        ]

    grouped = [
        {
            "name": r.name,
            "lat": float(r.lat) if r.lat is not None else None,
            "lng": float(r.lng) if r.lng is not None else None,
            "weight": float(r.weight or 0),
        }
        for r in rows
        if r.lat is not None and r.lng is not None and (r.weight or 0) > 0
    ]
    return grouped + search_points


# ===================== ENGAGEMENT =====================
#
# Powers the StaffDashboard "How long do they take to come back vs how long they
# stay" trend lines, per role. Two metrics, one round-trip:
#
#   avg_usage_minutes    = AVG(last_seen_at - created_at)  per session, per day
#   return_interval_hours = AVG of per-user MEDIAN gap between consecutive
#                          session start times, per day. We use median per-user
#                          (then mean across users on that day) because raw
#                          per-user means are skewed by one-time visitors who
#                          never return — that single ∞-gap would dominate.
#
# Performance:
#  - Single query per role; the optional `roles` filter joins on the indexed
#    `users.role` column.
#  - Window function (LAG) reads sessions ordered by (user_id, created_at) —
#    served directly by `idx_session_user_created` with no sort step.
#  - Day bucketing in SQL (date_trunc) avoids shipping raw events to Python.
#  - Anonymous sessions (user_id IS NULL) are excluded — a single anon cookie
#    can blast thousands of sessions and would dominate the average.

ENGAGEMENT_ROLES = ("user", "agent", "staff")


def _engagement_for_role(db: Session, role: str, cutoff: Optional[datetime]) -> list[dict]:
    """Return a list of {date, return_interval_hours, avg_usage_minutes} rows.

    `role` is matched against users.role (the canonical column — same one
    indexed and used by every other staff/admin query).
    """
    # Bind params via SQLAlchemy text() — no string interpolation of `role`
    # (would bypass parameter binding and risk SQL injection if signature
    # ever widens to user input).
    sql = text(
        """
        WITH role_sessions AS (
            SELECT
                s.user_id,
                s.created_at,
                s.last_seen_at,
                EXTRACT(EPOCH FROM (s.last_seen_at - s.created_at)) / 60.0
                    AS usage_minutes,
                LAG(s.created_at) OVER (
                    PARTITION BY s.user_id ORDER BY s.created_at
                ) AS prev_started_at
            FROM user_sessions s
            JOIN users u ON u.id = s.user_id
            WHERE u.role = :role
              AND s.user_id IS NOT NULL
              AND (:cutoff IS NULL OR s.created_at >= :cutoff)
        ),
        per_day AS (
            SELECT
                date_trunc('day', created_at)::date AS day,
                user_id,
                AVG(usage_minutes) AS user_usage_min,
                -- Median gap per user per day. NULL-safe: PERCENTILE_CONT
                -- ignores NULLs, and the first session per user emits NULL
                -- via LAG which is correctly skipped.
                PERCENTILE_CONT(0.5) WITHIN GROUP (
                    ORDER BY EXTRACT(EPOCH FROM (created_at - prev_started_at)) / 3600.0
                ) AS user_return_hours
            FROM role_sessions
            GROUP BY 1, 2
        )
        SELECT
            day,
            AVG(user_return_hours) AS return_interval_hours,
            AVG(user_usage_min)    AS avg_usage_minutes
        FROM per_day
        GROUP BY day
        ORDER BY day ASC
        """
    )
    rows = db.execute(sql, {"role": role, "cutoff": cutoff}).all()
    return [
        {
            "date": r.day.isoformat(),
            # Round to keep payload small and the chart legend tidy.
            "return_interval_hours": (
                round(float(r.return_interval_hours), 2)
                if r.return_interval_hours is not None else None
            ),
            "avg_usage_minutes": (
                round(float(r.avg_usage_minutes), 2)
                if r.avg_usage_minutes is not None else None
            ),
        }
        for r in rows
    ]


def compute_engagement(db: Session, since: Optional[str] = "30d") -> dict:
    """Per-role engagement trend series for the Staff dashboard.

    Returns one entry per role in `ENGAGEMENT_ROLES` so the frontend renders
    three line charts from a single response. Empty series (no sessions in
    window) come back as `series: []` — callers render an empty state.
    """
    cutoff = _since_dt(since)
    try:
        return {
            "since": since or "all",
            "roles": {
                role: {"series": _engagement_for_role(db, role, cutoff)}
                for role in ENGAGEMENT_ROLES
            },
        }
    except Exception as e:
        # Aggregation must never 500 the dashboard — log and return empty
        # series so the UI degrades to the empty state instead of breaking.
        logger.warning("compute_engagement failed: %s", e)
        return {
            "since": since or "all",
            "roles": {role: {"series": []} for role in ENGAGEMENT_ROLES},
        }


# ===================== RISK OVERSIGHT (staff/admin) =====================
# Sensitive: the "flagged-unsafe building × active listing" join is exactly the
# corruption-threat surface (work_flow.md §4.2 / §9.7). The endpoint is staff-gated
# and this aggregate returns COUNTS only — never the listing/flag rows themselves.

def aggregate_risk_summary(db: Session) -> dict:
    """Coverage mix across the catalog + a count of active listings sitting on a
    building whose latest structural flag is UNSAFE / AUTH_UNSAFE.

    Complexity: the coverage mix is a single grouped count over the indexed
    `verification_status` column. The unsafe-listing count joins the small
    BuildingLink/StructuralFlag tables (one flagged building has at most a handful of
    rows) against the latest-flag-per-building subquery — bounded by the flag count,
    not the listing count.
    """
    try:
        # 1) Coverage mix — grouped count on the indexed status column.
        rows = (
            db.query(Property.verification_status, func.count(Property.id))
            .filter(Property.is_active.is_(True))
            .group_by(Property.verification_status)
            .all()
        )
        coverage = {"monitored": 0, "not_monitored": 0, "pending": 0, "unavailable": 0}
        for status_val, n in rows:
            coverage[status_val or "pending"] = coverage.get(status_val or "pending", 0) + n

        # 2) Latest flag per (aoi, building): max(created_at) grouped, joined back.
        latest = (
            db.query(
                StructuralFlag.aoi_code.label("aoi_code"),
                StructuralFlag.insar_building_id.label("bid"),
                func.max(StructuralFlag.created_at).label("mx"),
            )
            .group_by(StructuralFlag.aoi_code, StructuralFlag.insar_building_id)
            .subquery()
        )
        latest_flag = (
            db.query(StructuralFlag)
            .join(
                latest,
                (StructuralFlag.aoi_code == latest.c.aoi_code)
                & (StructuralFlag.insar_building_id == latest.c.bid)
                & (StructuralFlag.created_at == latest.c.mx),
            )
            .filter(StructuralFlag.state.in_((FLAG_UNSAFE, FLAG_AUTH_UNSAFE)))
            .subquery()
        )

        # Active listings linked to a currently-unsafe building.
        unsafe_listings = (
            db.query(func.count(func.distinct(Property.id)))
            .join(BuildingLink, BuildingLink.listing_id == Property.id)
            .join(
                latest_flag,
                (BuildingLink.aoi_code == latest_flag.c.aoi_code)
                & (BuildingLink.insar_building_id == latest_flag.c.insar_building_id),
            )
            .filter(Property.is_active.is_(True))
            .scalar()
            or 0
        )

        return {
            "coverage": coverage,
            "monitored": coverage.get("monitored", 0),
            "not_monitored": coverage.get("not_monitored", 0),
            "pending": coverage.get("pending", 0),
            "unavailable": coverage.get("unavailable", 0),
            "unsafe_listings": int(unsafe_listings),
        }
    except Exception as e:
        logger.warning("aggregate_risk_summary failed: %s", e)
        return {
            "coverage": {"monitored": 0, "not_monitored": 0, "pending": 0, "unavailable": 0},
            "monitored": 0, "not_monitored": 0, "pending": 0, "unavailable": 0,
            "unsafe_listings": 0,
        }
