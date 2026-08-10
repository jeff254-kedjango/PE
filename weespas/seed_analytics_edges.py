#!/usr/bin/env python3
"""
Seed analytics edge cases for the Stats / Analytics dashboard.

Generates time-distributed PropertyViewEvent, UserSession, SearchLog, Favorite,
and ContactSubmission rows so every chart on /stats can be visually exercised:

  - AnalyticsSummaryStrip   (sessions / views / searches / favorites / inquiries)
  - CategoryInterestChart   (engagement weighted by category)
  - PriceRangeChart         (engagement weighted by price bucket, sale + rent)
  - HeatmapMap (access)     (UserSession.geo_* by county / city)
  - HeatmapMap (interest)   (Property addresses + SearchLog lat/lng)
  - StatsPage donut + bar   (Property.view_count, listing_type, featured, certified)

Edge cases produced
-------------------
  * Zero state         — at least one category and one price bucket left empty.
  * Low activity       — a "quiet" property gets only a handful of events.
  * Peak performance   — one outlier property gets 10x the median engagement,
                         to test Y-axis scaling and bar chart layout.
  * Time spread        — events distributed across the last 90 days so the
                         7d / 30d / 90d / all time-range picker shows different
                         numbers in every window.
  * Geo spread         — sessions across multiple Kenyan counties + a couple
                         outside Kenya, so the access heatmap has variety.

Idempotency
-----------
  * Re-running this script will NOT duplicate analytics rows. We tag every row
    we create with a deterministic marker (session_token prefix, search query
    prefix, view-event timestamps anchored to the run date) and clear our
    previously-seeded rows before re-inserting.
  * Property / Agent / User rows are never deleted — only analytics tables are
    refreshed. Run seed.py + seed_expanded.py + seed_stats.py first.

Usage
-----
    python seed_analytics_edges.py
"""

from __future__ import annotations

import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import func
from PE.weespas.core.database import SessionLocal
from PE.weespas.models.analytics import (
    UserSession,
    PropertyViewEvent,
    SearchLog,
    Favorite,
)
from PE.weespas.models.contact import ContactSubmission
from PE.weespas.models.property import (
    Property,
    PropertyCategory,
    PropertyListingType,
    Address,
)
from PE.weespas.models.user import User


# ── Markers used to identify rows this script created (for idempotency) ──
SEED_TAG = "edges-seed"
SESSION_TOKEN_PREFIX = f"{SEED_TAG}-"
SEARCH_QUERY_PREFIX = f"[{SEED_TAG}] "
CONTACT_PURPOSE_TAG = f"{SEED_TAG}-inquiry"

random.seed(2026)


# ── Geo profile for access heatmap (Kenya + 2 extras) ─────────────────────
ACCESS_GEO = [
    # (city, county, lat, lng, weight_target)
    ("Nairobi",   "Nairobi",   -1.2921, 36.8219, 80),   # peak
    ("Mombasa",   "Mombasa",   -4.0435, 39.6682, 30),
    ("Kisumu",    "Kisumu",    -0.0917, 34.7680, 18),
    ("Nakuru",    "Nakuru",    -0.3031, 36.0800, 12),
    ("Eldoret",   "Uasin Gishu", 0.5143, 35.2698, 8),
    ("Thika",     "Kiambu",    -1.0333, 37.0833, 6),
    ("Malindi",   "Kilifi",    -3.2175, 40.1191, 3),
    ("Kampala",   "Central",    0.3476, 32.5825, 2),    # outside KE
    ("Dar es Salaam", "Dar",   -6.7924, 39.2083, 1),    # outside KE
]


# ── Time helpers ──────────────────────────────────────────────────────────
def _spread_dates(n: int, max_days_ago: int) -> list[datetime]:
    """Return n datetimes within the last `max_days_ago` days, biased recent."""
    now = datetime.now(timezone.utc)
    out = []
    for _ in range(n):
        # bias towards recent days with a square distribution
        r = random.random() ** 2
        days = r * max_days_ago
        out.append(now - timedelta(days=days, hours=random.randint(0, 23)))
    return out


def _clear_prior_seed(db) -> None:
    """Remove any rows this script previously inserted (tag-based)."""
    print("Clearing prior edge-seed analytics rows...")

    # Sessions are linked to view/search rows via session_id; delete child rows
    # first, then sessions.
    sess_ids = [
        sid for (sid,) in db.query(UserSession.id)
        .filter(UserSession.session_token.like(f"{SESSION_TOKEN_PREFIX}%"))
        .all()
    ]
    if sess_ids:
        db.query(PropertyViewEvent).filter(
            PropertyViewEvent.session_id.in_(sess_ids)
        ).delete(synchronize_session=False)
        db.query(SearchLog).filter(
            SearchLog.session_id.in_(sess_ids)
        ).delete(synchronize_session=False)
        db.query(UserSession).filter(
            UserSession.id.in_(sess_ids)
        ).delete(synchronize_session=False)

    # Searches keyed by our prefix (covers anonymous / no-session ones too)
    db.query(SearchLog).filter(
        SearchLog.query_text.like(f"{SEARCH_QUERY_PREFIX}%")
    ).delete(synchronize_session=False)

    # Inquiries we created
    db.query(ContactSubmission).filter(
        ContactSubmission.inquiry_purpose == CONTACT_PURPOSE_TAG
    ).delete(synchronize_session=False)

    db.commit()


# ── Sessions ──────────────────────────────────────────────────────────────
def _seed_sessions(db) -> list[UserSession]:
    print("Seeding user sessions across counties...")
    created: list[UserSession] = []
    for city, county, lat, lng, target in ACCESS_GEO:
        for _ in range(target):
            ts = datetime.now(timezone.utc) - timedelta(
                days=random.randint(0, 89),
                hours=random.randint(0, 23),
            )
            sess = UserSession(
                id=str(uuid.uuid4()),
                session_token=f"{SESSION_TOKEN_PREFIX}{uuid.uuid4().hex}",
                ip_address=f"10.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(0, 255)}",
                geo_lat=lat + random.uniform(-0.05, 0.05),
                geo_lng=lng + random.uniform(-0.05, 0.05),
                geo_city=city,
                geo_county=county,
                geo_source=random.choice(["browser", "ip"]),
                user_agent="seed/edges 1.0",
                created_at=ts,
                last_seen_at=ts,
            )
            db.add(sess)
            created.append(sess)
    db.commit()
    print(f"  Created {len(created)} sessions.")
    return created


# ── Property views (peak / median / quiet / zero) ─────────────────────────
def _seed_views(db, sessions: list[UserSession]) -> tuple[Property | None, list[Property]]:
    print("Seeding property view events with peak / median / quiet / zero profiles...")
    properties = (
        db.query(Property)
        .filter(Property.is_active == True)  # noqa: E712
        .all()
    )
    if not properties:
        print("  No active properties — skip views.")
        return None, []

    random.shuffle(properties)

    # Reserve some properties as "zero" (no events at all) — keeps a slice of
    # the catalogue empty so charts render the empty bucket correctly.
    zero_count = max(2, len(properties) // 6)
    zero_props = properties[:zero_count]
    rest = properties[zero_count:]

    if not rest:
        return None, zero_props

    peak = rest[0]
    quiet = rest[1:1 + max(2, len(rest) // 5)]
    median = rest[1 + len(quiet):]

    def _emit(prop: Property, count: int, max_days: int) -> None:
        for ts in _spread_dates(count, max_days):
            sess = random.choice(sessions) if sessions else None
            db.add(PropertyViewEvent(
                id=str(uuid.uuid4()),
                property_id=prop.id,
                user_id=None,
                session_id=sess.id if sess else None,
                viewed_at=ts,
            ))

    # Peak outlier — 10x median
    _emit(peak, count=600, max_days=60)

    # Median band
    for prop in median:
        _emit(prop, count=random.randint(40, 90), max_days=89)

    # Quiet — only a handful, all recent
    for prop in quiet:
        _emit(prop, count=random.randint(1, 4), max_days=6)

    db.commit()

    total_views = db.query(func.count(PropertyViewEvent.id)).scalar() or 0
    print(f"  Peak={peak.title[:40]}, quiet={len(quiet)}, zero={len(zero_props)}, total events={total_views}")
    return peak, zero_props


# ── Searches (text + geo + by-category) ───────────────────────────────────
def _seed_searches(db, sessions: list[UserSession]) -> None:
    print("Seeding search logs (text, geo, category)...")
    categories = db.query(PropertyCategory).all()
    if not categories:
        print("  No categories — skip searches.")
        return

    SEARCH_TERMS = [
        "westlands", "kilimani", "karen", "3 bedroom", "office space",
        "land for sale", "studio apartment", "luxury villa",
    ]

    # Heavily search a couple of categories (drives CategoryInterestChart),
    # leave one or two with no searches at all (zero state).
    weighted_cats = random.sample(categories, k=min(len(categories), 4))
    cold_cats = [c for c in categories if c not in weighted_cats]

    for cat in weighted_cats:
        for ts in _spread_dates(random.randint(20, 80), 89):
            sess = random.choice(sessions) if sessions else None
            db.add(SearchLog(
                id=str(uuid.uuid4()),
                session_id=sess.id if sess else None,
                query_text=f"{SEARCH_QUERY_PREFIX}{random.choice(SEARCH_TERMS)}",
                latitude=random.uniform(-1.4, -1.2) if random.random() < 0.6 else None,
                longitude=random.uniform(36.6, 36.95) if random.random() < 0.6 else None,
                radius_km=random.choice([None, 5, 10, 25]),
                category_id=cat.id,
                listing_type=random.choice(["sale", "rent", None]),
                min_price=None,
                max_price=None,
                result_count=random.randint(0, 40),
                created_at=ts,
            ))

    # Make zero state explicit (no searches for cold cats)
    if cold_cats:
        print(f"  Zero-state categories: {[c.slug for c in cold_cats]}")

    db.commit()
    total = db.query(func.count(SearchLog.id)).filter(
        SearchLog.query_text.like(f"{SEARCH_QUERY_PREFIX}%")
    ).scalar()
    print(f"  Created {total} search logs.")


# ── Favorites (skewed towards expensive sale + cheap rent) ────────────────
def _seed_favorites(db) -> None:
    print("Seeding favorites with price-bucket bias...")
    users = db.query(User).filter(User.role == "user").all()
    if not users:
        # Fall back to any non-agent user; otherwise skip
        users = db.query(User).filter(User.role != "agent").limit(5).all()
    if not users:
        print("  No users to attach favorites to — skip.")
        return

    properties = db.query(Property).filter(Property.is_active == True).all()  # noqa: E712
    if not properties:
        return

    # Bias: expensive sale + cheap rent get the most favorites — exercises both
    # ends of the price-range chart.
    sale = sorted(
        [p for p in properties if p.listing_type == PropertyListingType.SALE and p.price],
        key=lambda p: p.price or 0,
        reverse=True,
    )
    rent = sorted(
        [p for p in properties if p.listing_type == PropertyListingType.RENT and p.price],
        key=lambda p: p.price or 0,
    )

    targets: list[Property] = sale[:5] + rent[:5]

    added = 0
    for prop in targets:
        for user in random.sample(users, k=min(len(users), random.randint(2, 5))):
            existing = db.query(Favorite).filter(
                Favorite.user_id == user.id,
                Favorite.property_id == prop.id,
            ).first()
            if existing:
                continue
            db.add(Favorite(
                id=str(uuid.uuid4()),
                user_id=user.id,
                property_id=prop.id,
                created_at=datetime.now(timezone.utc) - timedelta(
                    days=random.randint(0, 60)
                ),
            ))
            added += 1

    db.commit()
    print(f"  Added {added} favorite rows (idempotent — skips duplicates).")


# ── Inquiries (ContactSubmissions tagged so we can remove on re-run) ──────
def _seed_inquiries(db, peak: Property | None) -> None:
    print("Seeding contact inquiries (incl. extra weight on peak property)...")
    properties = db.query(Property).filter(Property.is_active == True).all()  # noqa: E712
    if not properties:
        return

    # 1-2 inquiries on a handful of properties; +5 on the peak.
    sampled = random.sample(properties, k=min(len(properties), 12))
    if peak and peak not in sampled:
        sampled.append(peak)

    for prop in sampled:
        n = 6 if prop is peak else random.randint(0, 2)
        for ts in _spread_dates(n, 60):
            db.add(ContactSubmission(
                id=str(uuid.uuid4()),
                inquiry_purpose=CONTACT_PURPOSE_TAG,
                description="Edge-seed inquiry",
                full_name=f"Seeded Visitor {random.randint(1, 9999)}",
                email=None,
                organization=None,
                phone=f"+25470{random.randint(1000000, 9999999)}",
                message="Interested in this listing, please contact me.",
                is_read=random.random() < 0.5,
                property_id=prop.id,
                ip_address="10.0.0.1",
                created_at=ts,
            ))
    db.commit()
    total = db.query(func.count(ContactSubmission.id)).filter(
        ContactSubmission.inquiry_purpose == CONTACT_PURPOSE_TAG
    ).scalar()
    print(f"  Created {total} inquiries.")


# ── view_count counter sync (for StatsPage donut/bar that reads it) ───────
def _sync_view_counts(db) -> None:
    print("Syncing Property.view_count from PropertyViewEvent (so dashboard tiles match)...")
    rows = (
        db.query(PropertyViewEvent.property_id, func.count("*"))
        .group_by(PropertyViewEvent.property_id)
        .all()
    )
    counts = {pid: c for pid, c in rows}
    updated = 0
    for prop in db.query(Property).all():
        new_count = counts.get(prop.id, 0)
        # Don't reset existing higher counts seeded by seed_stats.py — take max
        if (prop.view_count or 0) < new_count:
            prop.view_count = new_count
            updated += 1
    db.commit()
    print(f"  Updated view_count on {updated} properties.")


# ── Summary print ─────────────────────────────────────────────────────────
def _print_summary(db) -> None:
    print("\n" + "=" * 60)
    print("  ANALYTICS EDGE-SEED COMPLETE")
    print("=" * 60)

    sessions = db.query(func.count(UserSession.id)).scalar() or 0
    views = db.query(func.count(PropertyViewEvent.id)).scalar() or 0
    searches = db.query(func.count(SearchLog.id)).scalar() or 0
    favs = db.query(func.count(Favorite.id)).scalar() or 0
    inq = db.query(func.count(ContactSubmission.id)).scalar() or 0

    print(f"  Sessions:   {sessions}")
    print(f"  Views:      {views}")
    print(f"  Searches:   {searches}")
    print(f"  Favorites:  {favs}")
    print(f"  Inquiries:  {inq}")

    # Time-window breakdown — proves 7d / 30d / 90d picker will show diffs
    now = datetime.now(timezone.utc)
    for label, days in [("7d", 7), ("30d", 30), ("90d", 90)]:
        cutoff = now - timedelta(days=days)
        v = db.query(func.count(PropertyViewEvent.id)).filter(
            PropertyViewEvent.viewed_at >= cutoff
        ).scalar() or 0
        s = db.query(func.count(UserSession.id)).filter(
            UserSession.created_at >= cutoff
        ).scalar() or 0
        print(f"  Window {label:<4}: views={v:>5}  sessions={s:>4}")
    print()


def seed_analytics_edges():
    db = SessionLocal()
    try:
        # Prerequisites
        if db.query(Property).count() == 0:
            print("ERROR: No properties found. Run seed.py / seed_expanded.py / seed_stats.py first.")
            return

        _clear_prior_seed(db)
        sessions = _seed_sessions(db)
        peak, _zero = _seed_views(db, sessions)
        _seed_searches(db, sessions)
        _seed_favorites(db)
        _seed_inquiries(db, peak)
        _sync_view_counts(db)
        _print_summary(db)

    except Exception as e:
        print(f"\nERROR: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_analytics_edges()
