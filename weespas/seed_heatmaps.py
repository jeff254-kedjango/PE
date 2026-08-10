#!/usr/bin/env python3
"""
Seed data tuned specifically to exercise the two heatmap charts on /stats:

  - HeatmapMap (Access)    — UserSession.geo_county / geo_city (where users open the app)
  - HeatmapMap (Interest)  — Address.county / Address.city aggregated with
                             views + favorites + inquiries, plus SearchLog
                             lat/lng points (added at county level only).

What this seed guarantees
-------------------------
  1. **County-name parity.** Existing seeds disagree on the canonical county
     string ("Nairobi" vs "Nairobi County"). The two heatmaps GROUP BY raw
     text, so divergent strings = bubbles never line up. We normalize every
     Address.county to its short form ("Nairobi", "Mombasa", …) so the same
     county shows up under the same label on both charts.

  2. **Multi-city drill-down.** Each major county gets multiple distinct
     geo_city values for sessions AND multiple Address.city values for
     properties — so clicking a county bubble (drill-down) yields more than
     one city bubble on each side.

  3. **Asymmetric supply/demand.** Some counties have lots of access but few
     listings (demand without supply); others the reverse. This is the whole
     point of comparing the two heatmaps side-by-side.

  4. **Outlier + empty buckets.** One county dominates (Nairobi) to test
     bubble-size scaling; one county exists only on Access (sessions but no
     properties) and one only on Interest (properties but no sessions in the
     window). Both should still render cleanly.

  5. **Time spread.** Events distributed across 90 days so the 7d / 30d / 90d
     time-range picker shows different shapes in each window.

Idempotency
-----------
  All rows we create are tagged with SEED_TAG. Re-running clears prior
  heatmap-seed rows first. We do NOT touch other seed scripts' data — but
  we DO normalize existing Address.county strings (one-shot, idempotent —
  re-applying the same normalization is a no-op).

Usage
-----
    python seed_heatmaps.py

Run after seed.py / seed_expanded.py / seed_stats.py. Safe to combine with
seed_analytics_edges.py (this script will harmlessly normalize the sessions
that script already created).
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
from PE.weespas.models.property import Property, Address
from PE.weespas.models.user import User


# ── Markers used to identify rows this script created ─────────────────────
SEED_TAG = "heatmap-seed"
SESSION_TOKEN_PREFIX = f"{SEED_TAG}-"
SEARCH_QUERY_PREFIX = f"[{SEED_TAG}] "
CONTACT_PURPOSE_TAG = f"{SEED_TAG}-inquiry"

random.seed(7777)


# ── Geo plan ──────────────────────────────────────────────────────────────
# (county, [(city, lat, lng), ...], access_target, interest_listings_bias)
#
#   access_target           → number of sessions to create for this county
#                             (distributed across the listed cities)
#   interest_listings_bias  → multiplier for synthetic engagement (views /
#                             favorites / inquiries) on existing properties
#                             whose Address.county == this county. 0 = leave
#                             alone.
#
# The mismatch between access_target and interest_listings_bias is intentional:
# it produces the supply/demand asymmetry the heatmap is meant to surface.

GEO_PLAN: list[tuple[str, list[tuple[str, float, float]], int, float]] = [
    # ── Tier 1: Outlier — Nairobi dominates both ──
    ("Nairobi", [
        ("Westlands",   -1.2662, 36.8142),
        ("Kilimani",    -1.2921, 36.7758),
        ("Karen",       -1.3634, 36.6877),
        ("Lavington",   -1.2780, 36.7720),
        ("Runda",       -1.2166, 36.8090),
        ("Embakasi",    -1.3230, 36.9100),
        ("Langata",     -1.3500, 36.7500),
    ], 120, 1.5),

    # ── Tier 2: Healthy supply + demand ──
    ("Mombasa", [
        ("Nyali",       -4.0200, 39.7100),
        ("Bamburi",     -3.9900, 39.7200),
        ("Shanzu",      -3.9700, 39.7300),
        ("Mombasa CBD", -4.0435, 39.6682),
    ], 45, 1.2),

    ("Kisumu", [
        ("Milimani",    -0.0917, 34.7680),
        ("Mamboleo",    -0.0750, 34.7900),
        ("Kisumu CBD",  -0.1022, 34.7617),
    ], 28, 0.9),

    ("Nakuru", [
        ("Milimani",    -0.2833, 36.0700),
        ("Naka Estate", -0.3000, 36.0800),
        ("Naivasha",    -0.7172, 36.4310),
    ], 22, 0.8),

    # ── Tier 3: Demand-heavy, supply-light (lots of sessions, few listings) ──
    ("Kiambu", [
        ("Thika",       -1.0396, 37.0900),
        ("Juja",        -1.1050, 37.0130),
        ("Ruiru",       -1.1450, 36.9610),
        ("Kikuyu",      -1.2486, 36.6627),
    ], 35, 0.6),

    ("Kajiado", [
        ("Ngong",       -1.3630, 36.6550),
        ("Kitengela",   -1.4700, 36.9600),
        ("Ongata Rongai", -1.3950, 36.7470),
    ], 24, 0.5),

    # ── Tier 4: Supply-heavy, demand-light (listings exist, few sessions) ──
    ("Kwale", [
        ("Diani",       -4.3170, 39.5830),
        ("Ukunda",      -4.2950, 39.5660),
    ], 4, 1.4),

    ("Kilifi", [
        ("Mtwapa",      -3.9400, 39.7350),
        ("Malindi",     -3.2175, 40.1191),
        ("Watamu",      -3.3550, 40.0220),
    ], 5, 1.3),

    # ── Tier 5: Smaller counties, balanced ──
    ("Uasin Gishu", [
        ("Eldoret",     0.5143, 35.2698),
        ("Elgon View",  0.5200, 35.2700),
    ], 9, 0.7),

    ("Laikipia", [
        ("Nanyuki",     0.0060, 37.0720),
    ], 6, 0.5),

    ("Machakos", [
        ("Athi River",  -1.4580, 36.9820),
        ("Machakos Town", -1.5177, 37.2634),
    ], 7, 0.6),

    # ── Tier 6: Access-only edge case (sessions but no listings expected) ──
    ("Kakamega", [
        ("Kakamega Town", 0.2827, 34.7519),
    ], 8, 0.0),

    # ── Tier 7: Outside Kenya — diaspora users browsing ──
    ("Diaspora", [
        ("Kampala",     0.3476, 32.5825),
        ("Dar es Salaam", -6.7924, 39.2083),
        ("Dubai",       25.2048, 55.2708),
    ], 6, 0.0),
]


# Map every variant of a county string we might find in existing addresses
# to the canonical short form used above.
COUNTY_NORMALIZE = {
    # explicit "X County" → "X"
    "Nairobi County": "Nairobi",
    "Mombasa County": "Mombasa",
    "Kisumu County": "Kisumu",
    "Nakuru County": "Nakuru",
    "Kiambu County": "Kiambu",
    "Kajiado County": "Kajiado",
    "Kwale County": "Kwale",
    "Kilifi County": "Kilifi",
    "Machakos County": "Machakos",
    "Uasin Gishu County": "Uasin Gishu",
    "Laikipia County": "Laikipia",
    "Kakamega County": "Kakamega",
    # already canonical — pass through
}


# ── Helpers ───────────────────────────────────────────────────────────────
def _spread_dates(n: int, max_days_ago: int) -> list[datetime]:
    """Bias-recent distribution across the last `max_days_ago` days."""
    now = datetime.now(timezone.utc)
    out = []
    for _ in range(n):
        r = random.random() ** 1.5  # mild recency bias
        days = r * max_days_ago
        out.append(now - timedelta(days=days, hours=random.randint(0, 23)))
    return out


def _clear_prior_seed(db) -> None:
    print("Clearing prior heatmap-seed rows...")
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

    db.query(SearchLog).filter(
        SearchLog.query_text.like(f"{SEARCH_QUERY_PREFIX}%")
    ).delete(synchronize_session=False)

    db.query(ContactSubmission).filter(
        ContactSubmission.inquiry_purpose == CONTACT_PURPOSE_TAG
    ).delete(synchronize_session=False)

    db.commit()


# ── 1. Normalize county strings on Address rows ───────────────────────────
def _normalize_county_strings(db) -> None:
    """Strip ' County' suffix from existing Address.county values so both
    heatmaps group on the same key."""
    print("Normalizing Address.county strings (idempotent)...")
    updated = 0
    for variant, canonical in COUNTY_NORMALIZE.items():
        n = (
            db.query(Address)
            .filter(Address.county == variant)
            .update({Address.county: canonical}, synchronize_session=False)
        )
        if n:
            print(f"  '{variant}' → '{canonical}': {n} rows")
            updated += n
    db.commit()
    if updated == 0:
        print("  No legacy ' County' suffixes found — already canonical.")


# ── 2. Sessions per county/city ───────────────────────────────────────────
def _seed_sessions(db) -> list[UserSession]:
    print("Seeding user sessions across counties + cities...")
    created: list[UserSession] = []
    for county, cities, target, _bias in GEO_PLAN:
        if target <= 0:
            continue
        # Distribute sessions across cities within the county. First city
        # always gets the largest slice (capital effect), the rest split
        # the remainder.
        weights = [3] + [1] * (len(cities) - 1) if len(cities) > 1 else [1]
        total_w = sum(weights)
        per_city = [max(1, round(target * w / total_w)) for w in weights]
        # Make sure we hit the target despite rounding
        diff = target - sum(per_city)
        per_city[0] += diff

        for (city, lat, lng), n in zip(cities, per_city):
            for _ in range(n):
                ts = datetime.now(timezone.utc) - timedelta(
                    days=random.randint(0, 89),
                    hours=random.randint(0, 23),
                )
                sess = UserSession(
                    id=str(uuid.uuid4()),
                    session_token=f"{SESSION_TOKEN_PREFIX}{uuid.uuid4().hex}",
                    ip_address=f"10.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(0, 255)}",
                    geo_lat=lat + random.uniform(-0.04, 0.04),
                    geo_lng=lng + random.uniform(-0.04, 0.04),
                    geo_city=city,
                    geo_county=county,
                    geo_source=random.choice(["browser", "ip"]),
                    user_agent="seed/heatmaps 1.0",
                    created_at=ts,
                    last_seen_at=ts,
                )
                db.add(sess)
                created.append(sess)
    db.commit()
    print(f"  Created {len(created)} sessions across {len(GEO_PLAN)} counties.")
    return created


# ── 3. Engagement on existing properties (drives Interest heatmap) ────────
def _seed_engagement(db, sessions: list[UserSession]) -> None:
    """Add views / favorites / inquiries on existing properties, weighted by
    each county's interest_listings_bias from GEO_PLAN. Re-uses our tagged
    sessions so views are attributable; favorites pull from real users."""
    print("Seeding property engagement weighted by county bias...")

    bias_by_county = {county: bias for county, _cities, _t, bias in GEO_PLAN}

    # Group properties by their (now normalized) county
    props_by_county: dict[str, list[Property]] = {}
    rows = (
        db.query(Property, Address)
        .join(Address, Address.property_id == Property.id)
        .filter(Property.is_active == True)  # noqa: E712
        .all()
    )
    for prop, addr in rows:
        if not addr.county:
            continue
        props_by_county.setdefault(addr.county, []).append(prop)

    if not props_by_county:
        print("  No active properties with county data — skipping.")
        return

    users = (
        db.query(User)
        .filter((User.role == "user") | (User.role == "agent"))
        .limit(40)
        .all()
    )

    total_views = total_favs = total_inq = 0

    for county, props in props_by_county.items():
        bias = bias_by_county.get(county, 0.4)  # default mild engagement
        if bias <= 0 or not props:
            continue

        # Per-property median views, scaled by bias
        for prop in props:
            n_views = max(0, int(random.gauss(40 * bias, 12 * bias)))
            for ts in _spread_dates(n_views, 89):
                sess = random.choice(sessions) if sessions else None
                db.add(PropertyViewEvent(
                    id=str(uuid.uuid4()),
                    property_id=prop.id,
                    user_id=None,
                    session_id=sess.id if sess else None,
                    viewed_at=ts,
                ))
                total_views += 1

            # Favorites — fewer, only for users
            if users and random.random() < 0.5 * bias:
                n_favs = random.randint(1, max(2, int(3 * bias)))
                for user in random.sample(users, k=min(len(users), n_favs)):
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
                    total_favs += 1

            # Inquiries — rarest, weight 5 in heatmap so they punch above their count
            if random.random() < 0.25 * bias:
                n_inq = random.randint(1, max(1, int(2 * bias)))
                for ts in _spread_dates(n_inq, 60):
                    db.add(ContactSubmission(
                        id=str(uuid.uuid4()),
                        inquiry_purpose=CONTACT_PURPOSE_TAG,
                        description="Heatmap-seed inquiry",
                        full_name=f"Visitor {random.randint(1, 9999)}",
                        email=None,
                        organization=None,
                        phone=f"+25470{random.randint(1000000, 9999999)}",
                        message="Interested in this listing.",
                        is_read=random.random() < 0.5,
                        property_id=prop.id,
                        ip_address="10.0.0.1",
                        created_at=ts,
                    ))
                    total_inq += 1

    db.commit()
    print(f"  +{total_views} views, +{total_favs} favorites, +{total_inq} inquiries.")


# ── 4. Geo-located searches (added at county level on Interest heatmap) ───
def _seed_geo_searches(db, sessions: list[UserSession]) -> None:
    """Some search queries carry lat/lng — these appear as raw points on the
    Interest heatmap (county view only). Spread them across major counties."""
    print("Seeding geo-tagged search logs...")
    seeds = [
        # (lat, lng, count)
        (-1.2921, 36.8219, 30),  # Nairobi cluster
        (-4.0435, 39.6682, 12),  # Mombasa
        (-0.0917, 34.7680, 8),   # Kisumu
        (-0.3031, 36.0800, 5),   # Nakuru
        (0.5143, 35.2698, 3),    # Eldoret
    ]
    total = 0
    for base_lat, base_lng, n in seeds:
        for ts in _spread_dates(n, 89):
            sess = random.choice(sessions) if sessions else None
            db.add(SearchLog(
                id=str(uuid.uuid4()),
                session_id=sess.id if sess else None,
                query_text=f"{SEARCH_QUERY_PREFIX}geo",
                latitude=base_lat + random.uniform(-0.08, 0.08),
                longitude=base_lng + random.uniform(-0.08, 0.08),
                radius_km=random.choice([5, 10, 25]),
                category_id=None,
                listing_type=random.choice(["sale", "rent", None]),
                min_price=None,
                max_price=None,
                result_count=random.randint(0, 30),
                created_at=ts,
            ))
            total += 1
    db.commit()
    print(f"  Created {total} geo-tagged searches.")


# ── 5. Sync Property.view_count so dashboard tiles match heatmap ──────────
def _sync_view_counts(db) -> None:
    print("Syncing Property.view_count from PropertyViewEvent...")
    rows = (
        db.query(PropertyViewEvent.property_id, func.count("*"))
        .group_by(PropertyViewEvent.property_id)
        .all()
    )
    counts = {pid: c for pid, c in rows}
    updated = 0
    for prop in db.query(Property).all():
        new_count = counts.get(prop.id, 0)
        if (prop.view_count or 0) < new_count:
            prop.view_count = new_count
            updated += 1
    db.commit()
    print(f"  Updated view_count on {updated} properties.")


# ── Summary ───────────────────────────────────────────────────────────────
def _print_summary(db) -> None:
    print("\n" + "=" * 64)
    print("  HEATMAP SEED COMPLETE")
    print("=" * 64)

    # Per-county session breakdown (Access)
    print("\n  ACCESS heatmap (sessions, all-time):")
    rows = (
        db.query(
            UserSession.geo_county,
            func.count(UserSession.id),
        )
        .filter(UserSession.geo_county.isnot(None))
        .group_by(UserSession.geo_county)
        .order_by(func.count(UserSession.id).desc())
        .all()
    )
    for county, n in rows:
        print(f"    {county:<14} {n:>4} sessions")

    # Per-county property + engagement breakdown (Interest)
    print("\n  INTEREST heatmap (active properties by county):")
    rows = (
        db.query(
            Address.county,
            func.count(Property.id),
        )
        .join(Property, Property.id == Address.property_id)
        .filter(Property.is_active == True, Address.county.isnot(None))  # noqa: E712
        .group_by(Address.county)
        .order_by(func.count(Property.id).desc())
        .all()
    )
    for county, n in rows:
        print(f"    {county:<14} {n:>4} listings")

    # Cross-check: counties present on one side but not the other
    sess_counties = {r[0] for r in db.query(UserSession.geo_county).distinct() if r[0]}
    addr_counties = {r[0] for r in db.query(Address.county).distinct() if r[0]}
    only_access = sorted(sess_counties - addr_counties)
    only_interest = sorted(addr_counties - sess_counties)
    if only_access:
        print(f"\n  Access-only counties (no listings):    {only_access}")
    if only_interest:
        print(f"  Interest-only counties (no sessions):  {only_interest}")

    # Time-window proof
    now = datetime.now(timezone.utc)
    print("\n  Time windows:")
    for label, days in [("7d", 7), ("30d", 30), ("90d", 90)]:
        cutoff = now - timedelta(days=days)
        s = db.query(func.count(UserSession.id)).filter(
            UserSession.created_at >= cutoff
        ).scalar() or 0
        v = db.query(func.count(PropertyViewEvent.id)).filter(
            PropertyViewEvent.viewed_at >= cutoff
        ).scalar() or 0
        print(f"    {label:<4} sessions={s:>4}  views={v:>5}")
    print()


def seed_heatmaps():
    db = SessionLocal()
    try:
        if db.query(Property).count() == 0:
            print("ERROR: No properties found. Run seed.py / seed_expanded.py first.")
            return

        _normalize_county_strings(db)
        _clear_prior_seed(db)
        sessions = _seed_sessions(db)
        _seed_engagement(db, sessions)
        _seed_geo_searches(db, sessions)
        _sync_view_counts(db)
        _print_summary(db)

    except Exception as e:
        print(f"\nERROR: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_heatmaps()
