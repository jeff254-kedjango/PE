"""Boost service — the §8.3 reach economy: allowance ledger + grant creation + eligibility pull.

Three responsibilities, all holding the project rules (O(bounded), fail-closed, no over-spend):

  * **consume_allowance** — atomically spend one daily "chance" for a (seller, tier, day). The
    hard cap is enforced *in the UPDATE* (``WHERE used < cap``), never by trusting a read-then-
    write — so two concurrent taps can never both succeed past the cap (fail-closed). A new
    business day is a fresh row at used=0 — the midnight reset, no job.
  * **grant_boost** — owner-checked, idempotent (one grant per target/tier/day via the unique
    constraint), and ATOMIC: the chance is consumed and the grant inserted in one transaction, so
    a lost race on the unique constraint rolls back BOTH (the chance is refunded — a no-op never
    burns an allowance).
  * **eligible_grants** — the bounded sponsored-candidate pull: live grants whose scope contains
    the buyer's point, GiST-indexed, capped at K. A nationwide (Sovereign) grant set can never
    make the feed O(everyone) — K bounds the lane (mirrors the organic candidate window).

Eligibility is a PURE function of the stored scope + window vs the buyer point and now() — no
sweep, no materialization (same discipline as the ephemerality boost).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from geoalchemy2.functions import ST_DWithin, ST_MakePoint, ST_SetSRID
from sqlalchemy import and_, cast, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from PE.commerce.core.config import settings
from PE.commerce.models.boost import (
    BOOST_HUSTLE,
    BOOST_MTAA,
    BOOST_SOVEREIGN,
    BOOST_TARGETS,
    BOOST_TIERS,
    SCOPE_NATION,
    SCOPE_RADIUS,
    SOURCE_FREE,
    TARGET_LISTING,
    BoostAllowance,
    BoostGrant,
)
from PE.commerce.models.listing import Listing
from PE.commerce.models.seller import Seller, Shop
from PE.commerce.services.proximity import _haversine_distance_m


class BoostError(ValueError):
    """Bad boost input (unknown tier/target, out-of-bounds duration). Router → 422."""


class QuotaExceeded(Exception):
    """The seller has spent all of today's free chances for this tier. Router → 429."""


# ----------------------------- tier config -----------------------------

def tier_daily_cap(tier: str) -> int:
    """Free daily allowance for a tier (the number of chances)."""
    return {
        BOOST_MTAA: settings.boost_mtaa_daily_free,
        BOOST_HUSTLE: settings.boost_hustle_daily_free,
        BOOST_SOVEREIGN: settings.boost_sovereign_daily_free,
    }[tier]


def tier_scope(tier: str) -> tuple[str, float | None]:
    """(scope_kind, radius_m) for a tier. Sovereign is nationwide (no radius)."""
    if tier == BOOST_MTAA:
        return SCOPE_RADIUS, settings.boost_mtaa_radius_m
    if tier == BOOST_HUSTLE:
        return SCOPE_RADIUS, settings.boost_hustle_radius_m
    return SCOPE_NATION, None  # sovereign


def business_date(now: datetime, tz_name: str | None = None) -> date:
    """The civil date in the business timezone (§8.3 quota bucket / midnight reset). ``now`` is
    treated as UTC if naive."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(ZoneInfo(tz_name or settings.boost_business_tz)).date()


# ----------------------------- allowance ledger -----------------------------

def remaining_allowance(db: Session, seller_id: str, tier: str, *, now: datetime | None = None) -> int:
    """Chances left today for (seller, tier). cap minus used, floored at 0. A missing row means
    none used yet → full cap."""
    now = now or datetime.now(timezone.utc)
    bday = business_date(now)
    cap = tier_daily_cap(tier)
    row = (
        db.query(BoostAllowance.used)
        .filter(
            BoostAllowance.seller_id == seller_id,
            BoostAllowance.tier == tier,
            BoostAllowance.usage_date == bday,
        )
        .one_or_none()
    )
    used = row[0] if row else 0
    return max(0, cap - used)


def _consume_allowance(db: Session, seller_id: str, tier: str, bday: date) -> bool:
    """Spend one chance for (seller, tier, bday). Returns True if consumed, False if the daily cap
    is reached (fail-closed). The cap is enforced in the conditional UPDATE so two racing taps can
    never both pass it.

    Does NOT commit — the caller wraps consume + grant insert in one transaction so a lost race on
    the grant refunds the chance.
    """
    cap = tier_daily_cap(tier)
    if cap <= 0:
        return False

    # Fast path: the day's row exists and is under cap → atomic increment.
    res = db.execute(
        update(BoostAllowance)
        .where(
            BoostAllowance.seller_id == seller_id,
            BoostAllowance.tier == tier,
            BoostAllowance.usage_date == bday,
            BoostAllowance.used < cap,
        )
        .values(used=BoostAllowance.used + 1)
    )
    if res.rowcount == 1:
        return True

    # rowcount 0 ⇒ either no row yet (first chance today) OR the row is exhausted. Try to create
    # the day's row at used=1 inside a SAVEPOINT so a concurrent create doesn't poison the outer
    # transaction. If the row already existed (the exhausted case), the insert hits the unique
    # constraint → we re-attempt the conditional increment, which stays 0 → fail closed.
    try:
        with db.begin_nested():
            db.add(BoostAllowance(seller_id=seller_id, tier=tier, usage_date=bday, used=1))
    except IntegrityError:
        res = db.execute(
            update(BoostAllowance)
            .where(
                BoostAllowance.seller_id == seller_id,
                BoostAllowance.tier == tier,
                BoostAllowance.usage_date == bday,
                BoostAllowance.used < cap,
            )
            .values(used=BoostAllowance.used + 1)
        )
        return res.rowcount == 1
    return True


# ----------------------------- ownership-scoped target resolution -----------------------------

def _owned_target(db: Session, seller: Seller, target_type: str, target_id: str):
    """Return the (lat, lng) of the caller's target, or None if it isn't owned (router → 404, no
    existence leak). A listing carries its own location; a shop carries the shop's. Ownership is
    via the seller id resolved from the verified token — a cross-owner target is reported as not
    found, never 403 (S6)."""
    if target_type == TARGET_LISTING:
        row = (
            db.query(Listing.lat, Listing.lng)
            .filter(Listing.id == target_id, Listing.seller_id == seller.id)
            .one_or_none()
        )
    else:  # shop
        row = (
            db.query(Shop.lat, Shop.lng)
            .filter(Shop.id == target_id, Shop.seller_id == seller.id)
            .one_or_none()
        )
    return (row[0], row[1]) if row else None


def _find_grant(db: Session, target_type: str, target_id: str, tier: str, bday: date) -> BoostGrant | None:
    return (
        db.query(BoostGrant)
        .filter(
            BoostGrant.target_type == target_type,
            BoostGrant.target_id == target_id,
            BoostGrant.tier == tier,
            BoostGrant.business_date == bday,
        )
        .one_or_none()
    )


# ----------------------------- grant creation -----------------------------

def grant_boost(
    db: Session, user_uuid: str, *, target_type: str, target_id: str, tier: str,
    duration_seconds: int | None = None, now: datetime | None = None,
) -> BoostGrant | None:
    """Open a Boost on the caller's listing/shop (§8.3). Returns the grant, None if the target is
    not owned (router → 404), raises BoostError (422) on bad input, or QuotaExceeded (429) when the
    day's free chances for this tier are spent.

    Idempotent + atomic: one grant per (target, tier, business-day). A retry (or a re-promote of
    the same target/tier the same day) REPLAYS the existing grant and does NOT spend a second
    chance. The chance and the grant are written in one transaction, so a lost race on the unique
    constraint rolls back both — a no-op never burns an allowance."""
    if tier not in BOOST_TIERS:
        raise BoostError(f"tier must be one of {BOOST_TIERS}")
    if target_type not in BOOST_TARGETS:
        raise BoostError(f"target_type must be one of {BOOST_TARGETS}")
    duration = duration_seconds if duration_seconds is not None else settings.boost_default_duration_seconds
    if not (settings.boost_min_duration_seconds <= duration <= settings.boost_max_duration_seconds):
        raise BoostError(
            f"duration_seconds must be between {settings.boost_min_duration_seconds} and "
            f"{settings.boost_max_duration_seconds}"
        )

    seller = db.query(Seller).filter(Seller.user_uuid == user_uuid).one_or_none()
    if seller is None:
        return None  # never sold → owns no targets → 404 (no existence leak)
    loc = _owned_target(db, seller, target_type, target_id)
    if loc is None:
        return None
    lat, lng = loc

    now = now or datetime.now(timezone.utc)
    bday = business_date(now)

    # Replay: an existing grant for this (target, tier, day) is returned as-is — no second charge.
    existing = _find_grant(db, target_type, target_id, tier, bday)
    if existing is not None:
        return existing

    # Spend a free chance (fail-closed). Paid adverts (later) skip this with source='paid'.
    if not _consume_allowance(db, seller.id, tier, bday):
        db.rollback()
        raise QuotaExceeded(f"No '{tier}' boosts left today")

    scope_kind, radius_m = tier_scope(tier)
    grant = BoostGrant(
        seller_id=seller.id,
        target_type=target_type,
        target_id=target_id,
        tier=tier,
        scope_kind=scope_kind,
        started_at=now,
        expires_at=now + timedelta(seconds=duration),
        business_date=bday,
        source=SOURCE_FREE,
    )
    if scope_kind == SCOPE_RADIUS:
        grant.center_lat = lat
        grant.center_lng = lng
        grant.center_geog = f"SRID=4326;POINT({lng} {lat})"
        grant.radius_m = radius_m

    db.add(grant)
    try:
        db.flush()  # surfaces the unique-constraint race before we commit
    except IntegrityError:
        # A concurrent grant for the same (target, tier, day) won. Undo our chance (full rollback)
        # and replay the winner — net one chance spent for the slot, not two.
        db.rollback()
        return _find_grant(db, target_type, target_id, tier, bday)

    db.commit()
    db.refresh(grant)
    return grant


def revoke_boost(db: Session, user_uuid: str, grant_id: str) -> bool:
    """Owner-only delete of a live grant (end the reach early). Returns True if revoked, False if
    not found / not owned (router → 404). Allowances are NOT refunded — the chance was spent the
    moment reach began (same as the ephemerality clear not refunding time)."""
    seller = db.query(Seller).filter(Seller.user_uuid == user_uuid).one_or_none()
    if seller is None:
        return False
    grant = (
        db.query(BoostGrant)
        .filter(BoostGrant.id == grant_id, BoostGrant.seller_id == seller.id)
        .one_or_none()
    )
    if grant is None:
        return False
    db.delete(grant)
    db.commit()
    return True


# ----------------------------- eligibility (the bounded sponsored-candidate pull) -----------------------------

def _scope_contains(db: Session, lat: float, lng: float):
    """Predicate: a grant's scope contains the buyer's point. Nation matches everyone; radius is
    point-in-circle on the dual path (GiST ST_DWithin in prod, Haversine bbox+exact in tests)."""
    if db.bind.dialect.name == "postgresql":
        point = cast(ST_SetSRID(ST_MakePoint(lng, lat), 4326), BoostGrant.center_geog.type)
        radius_match = and_(
            BoostGrant.scope_kind == SCOPE_RADIUS,
            ST_DWithin(BoostGrant.center_geog, point, BoostGrant.radius_m),
        )
    else:
        # SQLite: a generous bbox prefilter (using the largest tier radius as a constant bound is
        # unnecessary — we compare the exact Haversine distance to the row's own radius_m).
        dist = _haversine_distance_m(BoostGrant.center_lat, BoostGrant.center_lng, lat, lng)
        radius_match = and_(
            BoostGrant.scope_kind == SCOPE_RADIUS,
            dist <= BoostGrant.radius_m,
        )
    return or_(BoostGrant.scope_kind == SCOPE_NATION, radius_match)


def eligible_grants(
    db: Session, lat: float, lng: float, *, limit: int | None = None,
    now: datetime | None = None, target_type: str | None = None,
) -> list[BoostGrant]:
    """Live grants whose scope contains the buyer's point, widest-reach first, capped at ``limit``
    (default ``feed_sponsored_max_candidates``). Bounded so a nationwide grant set never makes the
    feed O(everyone): the cap + the GiST index keep the pull O(log n + K).

    'Live' = started_at <= now < expires_at. Ordering puts wider tiers first (Sovereign > Hustle >
    Mtaa) then freshest, a deterministic base; the feed layer applies the slot lottery on top.

    ``target_type`` (optional) restricts the pull to one target kind ('shop' | 'listing') IN SQL,
    so the cap counts only that kind. The trending rail needs listing grants only; without this the
    shared cap would be consumed by shop grants and silently under-fill the product queue. Default
    None keeps the feed sponsored lane's behaviour (both kinds) unchanged."""
    now = now or datetime.now(timezone.utc)
    limit = limit or settings.feed_sponsored_max_candidates
    filters = [
        BoostGrant.started_at <= now,
        BoostGrant.expires_at > now,
        _scope_contains(db, lat, lng),
    ]
    if target_type is not None:
        filters.append(BoostGrant.target_type == target_type)
    rows = (
        db.query(BoostGrant)
        .filter(*filters)
        # Freshest windows first as the bounded DB-side cut; the feed layer applies the tier-weight
        # lottery (TIER_WEIGHT) over this capped set, so we don't need tier ordering in SQL.
        .order_by(BoostGrant.expires_at.desc())
        .limit(limit)
        .all()
    )
    return rows
