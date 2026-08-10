"""Proximity search — the dual-path radius query.

Prod (PostgreSQL/PostGIS): ST_DWithin on a GiST-indexed geography column — index-backed,
O(log n + k) where k = rows in the radius. This is the moat query.

Tests (SQLite): a btree bounding-box prefilter on lat/lng followed by an exact Haversine
distance in metres (R = 6371000, matching weespas PropertyService.haversine_distance). The
two paths agree on metres and "within radius" semantics; they differ only by the
sphere-vs-spheroid epsilon (< 0.5%), irrelevant for a "within 2 km" feed and absorbed by
test tolerances at the boundary.

``set_location`` is the single writer that keeps the geography column and the lat/lng
floats from drifting — always set a shop/listing's position through it.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from geoalchemy2.functions import ST_DWithin, ST_Distance, ST_MakePoint, ST_SetSRID
from sqlalchemy import Float, and_, cast, func, or_
from sqlalchemy.orm import Session

from PE.commerce.models.listing import POST_KIND_POST, PROMO_STORY, Listing

# Feed post-kind filter (§8 social toggle). The seller declares a post as a dedicated short video
# (is_short_video) or an ordinary listing; the buyer's "Listings | Videos" toggle maps to these.
# None ⇒ no filter (the unified feed — both kinds, the default).
FEED_KIND_LISTINGS = "listings"
FEED_KIND_VIDEOS = "videos"
FEED_KINDS = (FEED_KIND_LISTINGS, FEED_KIND_VIDEOS)

# WGS84 mean Earth radius in metres — same constant weespas uses for Haversine.
_EARTH_RADIUS_M = 6371000.0
# Degrees-per-metre latitude bound for the bbox prefilter (a safe over-estimate; longitude
# degrees shrink with latitude, so using the latitude figure widens the box slightly — the
# exact Haversine filter then trims it. Conservative = never drops a true match).
_M_PER_DEG = 111_320.0


def set_location(obj: Any, lat: float, lng: float) -> None:
    """Set both the float lat/lng and the PostGIS geography point atomically, so they can
    never disagree. ``geog`` is written as WKT 'POINT(lng lat)' (PostGIS x=lng, y=lat);
    GeoAlchemy2 ignores it harmlessly on SQLite (the column compiles to TEXT there)."""
    obj.lat = lat
    obj.lng = lng
    obj.geog = f"SRID=4326;POINT({lng} {lat})"


def _haversine_distance_m(lat_col, lng_col, lat: float, lng: float):
    """SQL expression for great-circle distance in metres from (lat,lng) to a row's
    (lat_col,lng_col). Uses only acos/cos/sin/radians — registered on the SQLite test
    engine in conftest (stock SQLite lacks them); native in PostgreSQL."""
    lat_r = math.radians(lat)
    return _EARTH_RADIUS_M * func.acos(
        func.min(  # guard acos domain against float drift > 1.0
            1.0,
            func.cos(lat_r) * func.cos(func.radians(lat_col))
            * func.cos(func.radians(lng_col) - math.radians(lng))
            + func.sin(lat_r) * func.sin(func.radians(lat_col)),
        )
    )


def within_clause(db: Session, lat: float, lng: float, radius_m: float):
    """Return ``(predicate, distance_expr)`` for the active dialect. ``distance_expr`` is
    the SAME number the predicate filters on, so ranking reads a consistent distance."""
    if db.bind.dialect.name == "postgresql":
        point = cast(ST_SetSRID(ST_MakePoint(lng, lat), 4326), Listing.geog.type)
        distance = ST_Distance(Listing.geog, point)            # metres (geography)
        predicate = ST_DWithin(Listing.geog, point, radius_m)  # GiST-backed
        return predicate, distance

    # SQLite (tests): bbox prefilter (indexable) + exact Haversine.
    deg = radius_m / _M_PER_DEG
    distance = _haversine_distance_m(Listing.lat, Listing.lng, lat, lng)
    predicate = and_(
        Listing.lat.between(lat - deg, lat + deg),
        Listing.lng.between(lng - deg, lng + deg),
        distance <= radius_m,
    )
    return predicate, distance


def _kind_predicate(kind: str | None):
    """The §8 post-kind toggle as a SQL predicate (None ⇒ no filter). Rides
    ``ix_listings_is_short_video`` so the toggle stays index-backed — no extra scan. Single source
    of the kind→predicate mapping, used by BOTH the radius feed and the sponsored lane so a Videos
    view can never leak the wrong post kind in either lane. An unknown kind (rejected upstream by
    the router) maps to None here — defensive, never a silent wrong filter."""
    if kind == FEED_KIND_VIDEOS:
        return Listing.is_short_video.is_(True)
    if kind == FEED_KIND_LISTINGS:
        return Listing.is_short_video.is_(False)
    return None


def _buyable_or_post():
    """Feed-visibility predicate over inventory (§8 timeline). A PRODUCT is shown only while it has
    stock (``stock_qty > 0`` — selling the last unit hides it); a POST has no inventory and is
    always shown while active. So the buyer feed keeps a row iff it is a post OR is in stock. This
    is the single source of that rule, used by both the radius feed and the sponsored lane so they
    can never diverge."""
    return or_(Listing.post_kind == POST_KIND_POST, Listing.stock_qty > 0)


def _not_expired_story(now: datetime):
    """Predicate: keep a row UNLESS it is an expired STORY-mode promotion. An expired story post
    disappears from the feed (§8) while its listing + stock remain untouched; evergreen and
    un-promoted listings are never excluded. NULL-safe: a row with no promo (promo_mode/expiry
    NULL) trivially satisfies the OR. The seller's own storefront does NOT apply this gate."""
    return or_(
        Listing.promo_mode != PROMO_STORY,
        Listing.promo_mode.is_(None),
        Listing.promo_expires_at.is_(None),
        Listing.promo_expires_at > now,
    )


def visible_listings_by_ids(
    db: Session, listing_ids: list[str], lat: float, lng: float, *, kind: str | None = None,
):
    """Resolve a bounded set of listing ids → ``(Listing, distance_m)`` for those that are still
    buyable (active AND in stock), with the buyer-relative distance. Used by the §8.3 sponsored
    lane: a Boost grant points at a target, but a sponsored slot must still show a VALID buyable
    listing — a sold-out / deactivated target is silently dropped (never shown as sponsored).

    ``listing_ids`` is already bounded by the sponsored-candidate cap, so this is O(k) on an
    indexed id ``IN`` — not a scan. Distance uses the SAME dual-path expression as the radius
    search, so a sponsored item's distance is consistent with organic items. NOTE: no radius
    filter — a Sovereign-boosted item can be far away by design (that is the point of national
    reach); the distance is informational, not a gate here.

    ``kind`` applies the §8 post-kind toggle IN SQL (same ``_kind_predicate`` as the radius feed) so
    a Videos view never surfaces a boosted ordinary-listing post (or vice-versa) — the DB does the
    filtering, not a Python post-pass. None (the default) ⇒ both kinds; callers with no toggle
    (e.g. quick_buys) are unaffected."""
    if not listing_ids:
        return []
    _, distance = within_clause(db, lat, lng, 1.0)  # radius arg unused; we only take the distance expr
    filters = [
        Listing.id.in_(listing_ids),
        Listing.is_active.is_(True),
        _buyable_or_post(),  # a sold-out product is dropped; a boosted post is allowed
    ]
    kind_pred = _kind_predicate(kind)
    if kind_pred is not None:
        filters.append(kind_pred)
    rows = db.query(Listing, distance.label("distance_m")).filter(*filters).all()
    return [(row[0], float(row[1])) for row in rows]


def search_listings(
    db: Session, lat: float, lng: float, radius_m: float, *, limit: int,
    now: datetime | None = None, kind: str | None = None,
):
    """Active listings within ``radius_m``, nearest first, capped at ``limit`` candidates.
    Returns a list of ``(Listing, distance_m)`` rows. Ordering by the index-backed distance
    keeps the candidate pull O(log n + k); ranking/sort happens in Python over this bounded
    set (feed.py). Expired STORY-mode promotions are excluded here (their post has decayed).

    ``kind`` is the §8 feed toggle: "videos" keeps only short-video posts, "listings" keeps only
    ordinary listing posts, None (default) returns both. The predicate rides the
    ``is_short_video`` index so the toggle stays index-backed — no extra scan."""
    now = now or datetime.now(timezone.utc)
    predicate, distance = within_clause(db, lat, lng, radius_m)
    filters = [
        Listing.is_active.is_(True),
        _buyable_or_post(),  # products need stock; posts (no inventory) are always shown
        _not_expired_story(now),
        predicate,
    ]
    kind_pred = _kind_predicate(kind)  # §8 toggle, index-backed; None ⇒ both kinds
    if kind_pred is not None:
        filters.append(kind_pred)
    rows = (
        db.query(Listing, distance.label("distance_m"))
        # Out-of-stock PRODUCTS are hidden from the buyer feed (_buyable_or_post); posts have no
        # inventory and stay visible. Rides ix_listings_feed / ix_listings_post_kind — still
        # index-backed, no extra scan. The seller's own storefront does NOT apply this gate.
        .filter(*filters)
        .order_by(cast(distance, Float).asc())
        .limit(limit)
        .all()
    )
    return [(row[0], float(row[1])) for row in rows]
