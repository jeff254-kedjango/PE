"""Global text search over trade listings — the trade half of the navbar's unified search.

Commerce is proximity-native: before this, the ONLY way to reach a listing was the radius feed
(feed/trending/quick-buys). The navbar's magnifier needs a KEYWORD path — "find me a *drill*
anywhere" — so this module adds a text match RANKED by proximity: a buyer searches nationwide and
the closest matching sellers surface first (the locked design — see the navbar-search design memo).

Cost discipline (S8, the same as the feed):
  * The match is a bounded candidate pull (LIMIT ``search_max_candidates``) — never an O(n) scan.
    In prod a pg_trgm GIN index backs the ILIKE so the pull is index-assisted; on SQLite (tests)
    it is a bounded LIKE over the small test set. The router logs a saturation warning if the cap
    is hit (mirroring feed.py) so a densifying catalogue surfaces as an ops signal.
  * Distance is the SAME dual-path expression the feed uses (``proximity.within_clause``), so a
    search result's distance is consistent with everywhere else. No radius GATE (nationwide reach) —
    the radius arg is unused; we take only the distance expression, exactly like
    ``proximity.visible_listings_by_ids``.

Match fields (locked): listing title + listing description + owning shop name. One indexed join to
Shop supplies the shop name (also returned for display). Visibility reuses the feed's rules so a
sold-out product or an expired story never appears as a searchable, buyable-looking result.

No PII (S6): results carry only opaque ids + seller-published fields (title, price, shop name,
category, media) + the ``property_uuid`` stitch key — never a user id beyond the opaque seller id.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import Float, cast, func, or_
from sqlalchemy.orm import Session

from PE.commerce.core.config import settings
from PE.commerce.models.listing import Listing
from PE.commerce.models.seller import Shop
from PE.commerce.services import proximity
from PE.commerce.services.quick_buys import first_image_url

logger = logging.getLogger(__name__)

# LIKE-wildcard escape. User input is interpolated into a ``LIKE`` pattern, so its own ``%``/``_``
# (and the escape char itself) must be neutralised or a query like "50%" would match far too much
# and "a_b" would match "axb". We escape with a backslash and declare ESCAPE '\' on the clause.
_LIKE_ESCAPE = "\\"


def _escape_like(term: str) -> str:
    """Neutralise LIKE metacharacters in a user term so it matches literally. Escapes the escape
    char first (order matters — else we'd double-escape the ones we add), then the two wildcards."""
    return (
        term.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", _LIKE_ESCAPE + "%")
        .replace("_", _LIKE_ESCAPE + "_")
    )


@dataclass(frozen=True)
class SearchHit:
    """One trade search result: the listing ORM row, its owning shop's display fields, and the
    buyer-relative distance. Kept minimal — the schema maps it to the wire DTO."""
    listing: object            # Listing ORM row
    shop_name: str | None
    shop_category: str | None
    distance_m: float


def search_trade(
    db: Session, query: str, lat: float, lng: float, *, limit: int,
    now: datetime | None = None,
) -> list[SearchHit]:
    """Keyword search over active, buyable trade listings, ranked nearest-first (nationwide).

    ``query`` is matched (case-insensitively, literally — wildcards escaped) against the listing
    title, the listing description, and the owning shop's name. Returns up to ``limit`` hits ordered
    by ascending distance from (lat, lng). A blank/too-short query returns [] (the caller also gates
    on length; this is the backstop). ``now`` gates the expired-story visibility rule.

    Complexity: one indexed join Listing⋈Shop, filtered by a bounded ``LIKE`` (GIN-assisted in prod),
    ordered by the index-backed distance, capped at ``search_max_candidates`` then sliced to
    ``limit`` — O(log n + k), never a table scan (S8)."""
    term = (query or "").strip()
    if len(term) < settings.search_min_query_len:
        return []
    now = now or datetime.now(timezone.utc)

    # A single escaped ``%term%`` pattern reused across all three fields. func.lower on BOTH sides
    # makes the match case-insensitive identically on SQLite and Postgres (Postgres ILIKE would work
    # too, but lower()+LIKE is the one form that behaves the same on both dialects — no per-dialect
    # branch needed, and the prod trigram index is built on lower(col) to match).
    pattern = f"%{_escape_like(term.lower())}%"

    def _like(col):
        return func.lower(col).like(pattern, escape=_LIKE_ESCAPE)

    text_match = or_(_like(Listing.title), _like(Listing.description), _like(Shop.name))

    # Distance only — no radius predicate (nationwide). Radius arg is unused (mirrors
    # proximity.visible_listings_by_ids), we consume just the distance expression.
    _, distance = proximity.within_clause(db, lat, lng, 1.0)

    # Cap the candidate pull BEFORE the final slice — the anti-O(n) ceiling. Ordering by the
    # index-backed distance means the cap keeps the NEAREST k matches (the ones a buyer wants),
    # never an arbitrary slice. The router warns if this saturates.
    candidates = min(limit, settings.search_max_results)
    rows = (
        db.query(Listing, Shop.name, Shop.category, distance.label("distance_m"))
        .join(Shop, Listing.shop_id == Shop.id)
        .filter(
            Listing.is_active.is_(True),
            proximity._buyable_or_post(),        # a sold-out product is not a buyable search hit
            proximity._not_expired_story(now),   # an expired story post is gone from discovery too
            text_match,
        )
        .order_by(cast(distance, Float).asc())
        .limit(min(candidates, settings.search_max_candidates))
        .all()
    )
    return [
        SearchHit(
            listing=row[0], shop_name=row[1], shop_category=row[2], distance_m=float(row[3]),
        )
        for row in rows
    ]


def to_image_url(listing) -> str | None:
    """The listing's lead (non-video) image for the result card, via the shared helper so the
    video-skip rule lives in one place (quick_buys.first_image_url)."""
    return first_image_url(listing.media_urls)
