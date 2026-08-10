"""Quick Buys — the §8 Trade right-rail discovery grid (a near/interest MIX).

The grid is a deliberate blend, per page (3×3 = 9 items):
  * ``near_per_page`` (default 4) from the buyer's IMMEDIATE radius (default 5 km) — "what's right
    next to me", ranked exactly like the proximity feed (proximity × freshness × intent) so the
    near lane is consistent with the main feed;
  * the remaining slots (default 5) from BEYOND that radius, matched to the buyer's own historical
    interest (the shop-categories they've saved / bought / asked about / commented on), then
    BACKFILLED by the boosted-trending set and finally by recency — so a brand-new buyer with zero
    history still gets a full, non-empty grid (no dead UI).

Every bucket is BOUNDED (recent-N / LIMIT) → O(k), never a table scan (S8). The whole thing reuses
existing primitives — ``proximity.search_listings`` / ``proximity.within_clause`` /
``proximity.visible_listings_by_ids`` / ``ranking.score`` / ``boost.eligible_grants`` — so the
radius maths, stock/story gating and ranking can never drift from the feed.

The service returns a single composed, de-duplicated list of ``QuickBuyRow`` (up to
``quick_buys_max``); the client pages over it locally. Money stays integer cents (S9); the DTO the
router builds from these rows carries no POS internals and no PII (see schemas.quick_buys).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import Float, cast, not_
from sqlalchemy.orm import Session

from PE.commerce.core.config import settings
from PE.commerce.models.engagement import ListingComment, ListingInquiry, SavedListing
from PE.commerce.models.listing import Listing
from PE.commerce.models.order import Order
from PE.commerce.models.seller import Shop
from PE.commerce.schemas.quick_buys import BUCKET_INTEREST, BUCKET_NEAR, BUCKET_TRENDING
from PE.commerce.services import boost, proximity, ranking

# Media path segment marking a trade video (mirrors the frontend utils/media.isVideoUrl). A video's
# URL is never used as the grid thumbnail — the grid is an image surface (videos live in the
# ShopVideoStrip), so a video-only listing simply has no thumbnail and the client shows initials.
_VIDEO_SEGMENT = "/trade/videos/"


@dataclass(frozen=True)
class QuickBuyRow:
    """One composed grid item: the listing ORM row + its buyer-relative distance + provenance
    bucket. The router maps this to the lean QuickBuyItem DTO (no POS/PII)."""
    listing: object  # Listing ORM row
    distance_m: float
    bucket: str


@dataclass(frozen=True)
class QuickBuyFilters:
    """Optional, already-validated buyer filters. ``radius_m`` (when set) OVERRIDES the near-radius
    boundary; the router clamps it to feed_max_radius_m before we see it. ``categories`` is the
    intersection with the backend allow-list (unknown slugs already dropped upstream). Price bounds
    are integer cents (>= 0)."""
    min_price_cents: int | None = None
    max_price_cents: int | None = None
    categories: tuple[str, ...] = ()
    radius_m: float | None = None


def first_image_url(media_urls_raw: str | None) -> str | None:
    """First NON-video media URL from a listing's raw ``media_urls`` JSON string → an image to show
    as a card thumbnail, else None. Decodes defensively (a malformed value yields no thumbnail,
    never an error). Shared by every surface that needs a listing's lead image from a column-select
    (trending) or an ORM row (quick_buys/flash_sales via ``thumbnail_of``), so the video-skip rule
    lives in exactly one place."""
    if not media_urls_raw:
        return None
    try:
        decoded = json.loads(media_urls_raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(decoded, list):
        return None
    for raw in decoded:
        url = str(raw)
        if _VIDEO_SEGMENT not in url.lower():
            return url
    return None


def thumbnail_of(listing) -> str | None:
    """First NON-video media URL of a listing ORM row → the grid card thumbnail, else None."""
    return first_image_url(listing.media_urls)


def _price_filter(filters: QuickBuyFilters):
    """SQL predicates for the price band (applied inside every bucket query so paging stays
    bounded). Empty when no price bound is set."""
    clauses = []
    if filters.min_price_cents is not None:
        clauses.append(Listing.price_cents >= filters.min_price_cents)
    if filters.max_price_cents is not None:
        clauses.append(Listing.price_cents <= filters.max_price_cents)
    return clauses


def _passes_price(listing, filters: QuickBuyFilters) -> bool:
    """Python-side PRICE-band guard only. Used by the near lane, where category is already enforced
    by an indexed ``shop_id IN`` (so re-checking category here — with no shop→category map — would
    wrongly drop every in-category row). Empty (always True) when no price bound is set."""
    if filters.min_price_cents is not None and listing.price_cents < filters.min_price_cents:
        return False
    if filters.max_price_cents is not None and listing.price_cents > filters.max_price_cents:
        return False
    return True


def _passes_filters(listing, filters: QuickBuyFilters, category_by_shop: dict[str, str | None]) -> bool:
    """Python-side guard mirroring the SQL predicates — used for rows that arrive via helpers which
    don't take the filter (the trending backfill resolves ids without the price/category WHERE). A
    category filter with a shop whose category is unknown/None is treated as NOT matching (an
    explicit category filter means the buyer wants those categories)."""
    if filters.min_price_cents is not None and listing.price_cents < filters.min_price_cents:
        return False
    if filters.max_price_cents is not None and listing.price_cents > filters.max_price_cents:
        return False
    if filters.categories:
        cat = category_by_shop.get(str(listing.shop_id))
        if cat is None or cat not in filters.categories:
            return False
    return True


def _category_filtered_shop_ids(db: Session, categories: tuple[str, ...]) -> list[str] | None:
    """Resolve the bounded set of shop ids whose category is in ``categories`` (a buyer filter).
    Returns None when no category filter is set (⇒ no shop restriction). The list is bounded by the
    number of shops in the requested categories; it feeds an indexed ``shop_id IN`` filter."""
    if not categories:
        return None
    rows = db.query(Shop.id).filter(Shop.category.in_(list(categories))).all()
    return [str(r[0]) for r in rows]


def _affinity_categories(db: Session, user_uuid: str, now: datetime) -> set[str]:
    """The buyer's category affinity = the distinct set of shop-categories behind the listings they
    have engaged with (saved, ordered, asked about, commented on). Each signal is a BOUNDED recent-N
    pull on an existing index, so affinity is O(bounded) — never a full-history scan.

    Category lives on the SHOP (Listing has no category), so we resolve engaged listing ids → their
    shops' categories in one indexed join. A brand-new buyer yields an empty set (the caller then
    falls straight through to the trending/recency backfill — a full grid, never empty)."""
    lookback = settings.quick_buys_affinity_lookback
    listing_ids: set[str] = set()

    # Saves — newest-first, bounded (ix_saved_user_created).
    for (lid,) in (
        db.query(SavedListing.listing_id)
        .filter(SavedListing.user_uuid == user_uuid)
        .order_by(SavedListing.created_at.desc())
        .limit(lookback)
        .all()
    ):
        listing_ids.add(str(lid))
    # Orders — the strongest signal (money), bounded (ix_orders_buyer_status/created).
    for (lid,) in (
        db.query(Order.listing_id)
        .filter(Order.buyer_uuid == user_uuid)
        .order_by(Order.created_at.desc())
        .limit(lookback)
        .all()
    ):
        listing_ids.add(str(lid))
    # Inquiries — "is this available?" intent, bounded.
    for (lid,) in (
        db.query(ListingInquiry.listing_id)
        .filter(ListingInquiry.from_user_uuid == user_uuid)
        .order_by(ListingInquiry.created_at.desc())
        .limit(lookback)
        .all()
    ):
        listing_ids.add(str(lid))
    # Public comments — weaker, but a genuine engagement, bounded.
    for (lid,) in (
        db.query(ListingComment.listing_id)
        .filter(ListingComment.author_uuid == user_uuid)
        .order_by(ListingComment.created_at.desc())
        .limit(lookback)
        .all()
    ):
        listing_ids.add(str(lid))

    if not listing_ids:
        return set()

    # Engaged listings → their shops' categories (one indexed join). Bounded by |listing_ids|.
    rows = (
        db.query(Shop.category)
        .join(Listing, Listing.shop_id == Shop.id)
        .filter(Listing.id.in_(list(listing_ids)), Shop.category.isnot(None))
        .distinct()
        .all()
    )
    return {str(c) for (c,) in rows if c is not None}


def _near_rows(db, lat, lng, near_radius_m, filters, *, limit, now) -> list[QuickBuyRow]:
    """The near bucket: buyable listings within ``near_radius_m``, ranked like the feed, honoring
    the price/category filters. Bounded by ``limit``."""
    shop_ids = _category_filtered_shop_ids(db, filters.categories)
    if shop_ids is not None and not shop_ids:
        return []  # a category filter that matches no shop ⇒ no near items (correct, not an error)

    candidates = proximity.search_listings(
        db, lat, lng, near_radius_m, limit=settings.feed_max_candidates, now=now,
    )
    price_clauses = _price_filter(filters)
    rows: list[QuickBuyRow] = []
    for listing, distance_m in candidates:
        if price_clauses and not _passes_price(listing, filters):
            continue
        if shop_ids is not None and str(listing.shop_id) not in shop_ids:
            continue
        rows.append(QuickBuyRow(listing=listing, distance_m=distance_m, bucket=BUCKET_NEAR))
    # Rank exactly like the feed so the near lane is consistent, then take the bounded head.
    rows.sort(
        key=lambda r: ranking.score(
            distance_m=r.distance_m, created_at=r.listing.created_at,
            intent_weight=r.listing.intent_weight, now=now,
            w_distance=settings.feed_w_distance, w_freshness=settings.feed_w_freshness,
            w_intent=settings.feed_w_intent, radius_m=near_radius_m,
            halflife_h=settings.feed_freshness_halflife_h,
            promo_started_at=r.listing.promo_started_at,
            promo_expires_at=r.listing.promo_expires_at, w_promo=settings.feed_w_promo,
        ),
        reverse=True,
    )
    return rows[:limit]


def _outer_query(db, lat, lng, near_radius_m, outer_cap_m, filters, *, exclude_ids, now, shop_ids):
    """Shared bounded query for outer-bucket listings: active + buyable, OUTSIDE near_radius_m and
    within outer_cap_m, price/category filtered, excluding ids already used, nearest-first. Returns
    ``[(Listing, distance_m)]``. ``shop_ids`` (category ∩ allow-list, or an affinity set) restricts
    the shops; None ⇒ no shop restriction (the recency backfill)."""
    outer_pred, distance = proximity.within_clause(db, lat, lng, outer_cap_m)
    _, near_distance = proximity.within_clause(db, lat, lng, near_radius_m)
    q = (
        db.query(Listing, distance.label("distance_m"))
        .filter(
            Listing.is_active.is_(True),
            proximity._buyable_or_post(),
            proximity._not_expired_story(now),
            outer_pred,                       # within the outer cap
            cast(distance, Float) > near_radius_m,  # strictly BEYOND the near radius
        )
    )
    if exclude_ids:
        q = q.filter(not_(Listing.id.in_(list(exclude_ids))))
    if shop_ids is not None:
        if not shop_ids:
            return []
        q = q.filter(Listing.shop_id.in_(list(shop_ids)))
    for clause in _price_filter(filters):
        q = q.filter(clause)
    rows = q.order_by(cast(distance, Float).asc()).limit(settings.feed_max_candidates).all()
    return [(row[0], float(row[1])) for row in rows]


def _shop_ids_for_categories(db: Session, categories) -> list[str]:
    """Shop ids whose category is in ``categories`` (a set/tuple of slugs). Bounded by the shops in
    those categories; feeds an indexed shop_id IN."""
    if not categories:
        return []
    rows = db.query(Shop.id).filter(Shop.category.in_(list(categories))).all()
    return [str(r[0]) for r in rows]


def build_quick_buys(
    db: Session, lat: float, lng: float, *, user_uuid: str,
    filters: QuickBuyFilters | None = None, now: datetime | None = None,
) -> tuple[list[QuickBuyRow], float]:
    """Compose the Quick Buys grid rows for one buyer. Returns ``(rows, near_radius_m)`` where rows
    is the de-duplicated composed list (≤ quick_buys_max) and near_radius_m is the boundary actually
    used (a caller radius filter overrides the default; the router clamps it).

    Ordering discipline: near items (4/page) interleave with outer items (5/page) in repeated blocks
    so each client page keeps the mix. Outer items are affinity-matched first, then trending, then
    recency — a cold buyer still fills the grid."""
    now = now or datetime.now(timezone.utc)
    filters = filters or QuickBuyFilters()
    near_radius_m = filters.radius_m if filters.radius_m is not None else settings.quick_buys_near_radius_m
    outer_cap_m = settings.feed_max_radius_m

    page_size = settings.quick_buys_page_size
    near_per_page = settings.quick_buys_near_per_page
    outer_per_page = page_size - near_per_page
    max_items = settings.quick_buys_max
    pages = max(1, max_items // page_size)
    near_target = near_per_page * pages
    outer_target = outer_per_page * pages

    seen: set[str] = set()

    # --- Near bucket ---
    near_rows: list[QuickBuyRow] = []
    for row in _near_rows(db, lat, lng, near_radius_m, filters, limit=near_target, now=now):
        lid = str(row.listing.id)
        if lid in seen:
            continue
        seen.add(lid)
        near_rows.append(row)

    # --- Outer bucket: 1) affinity, 2) trending backfill, 3) recency backfill ---
    outer_rows: list[QuickBuyRow] = []

    # Category set that outer items must belong to. If the buyer supplied a category filter, that IS
    # the set (their explicit intent overrides derived affinity). Otherwise use derived affinity.
    if filters.categories:
        affinity = set(filters.categories)
    else:
        affinity = _affinity_categories(db, user_uuid, now)

    if affinity:
        shop_ids = _shop_ids_for_categories(db, affinity)
        for listing, distance_m in _outer_query(
            db, lat, lng, near_radius_m, outer_cap_m, filters,
            exclude_ids=seen, now=now, shop_ids=shop_ids,
        ):
            lid = str(listing.id)
            if lid in seen:
                continue
            seen.add(lid)
            outer_rows.append(QuickBuyRow(listing=listing, distance_m=distance_m, bucket=BUCKET_INTEREST))
            if len(outer_rows) >= outer_target:
                break

    # 2) Trending backfill — boosted listings reaching the buyer, resolved to buyable rows. These
    #    are the same candidates the feed's sponsored lane uses. Only used if affinity under-fills.
    if len(outer_rows) < outer_target:
        category_by_shop: dict[str, str | None] = {}
        grants = boost.eligible_grants(db, lat, lng, now=now, target_type="listing")
        grant_ids = [str(g.target_id) for g in grants]
        if grant_ids:
            resolved = proximity.visible_listings_by_ids(db, grant_ids, lat, lng)
            # Need shop categories to honour a category filter on this path.
            if filters.categories:
                shop_id_set = {str(li.shop_id) for li, _ in resolved}
                if shop_id_set:
                    for sid, cat in (
                        db.query(Shop.id, Shop.category).filter(Shop.id.in_(list(shop_id_set))).all()
                    ):
                        category_by_shop[str(sid)] = cat
            # Nearest-first among the trending backfill for a stable order.
            resolved.sort(key=lambda pair: pair[1])
            for listing, distance_m in resolved:
                lid = str(listing.id)
                if lid in seen:
                    continue
                if distance_m <= near_radius_m:
                    continue  # keep the outer lane strictly beyond the near radius
                if not _passes_filters(listing, filters, category_by_shop):
                    continue
                seen.add(lid)
                outer_rows.append(QuickBuyRow(listing=listing, distance_m=distance_m, bucket=BUCKET_TRENDING))
                if len(outer_rows) >= outer_target:
                    break

    # 3) Recency backfill — remaining outer listings, newest-first, still within the outer cap and
    #    beyond the near radius. Guarantees a full grid for a cold buyer. CRITICAL: an EXPLICIT
    #    category filter is a hard constraint the buyer set — the backfill must honour it and NOT
    #    relax to all shops (that would leak off-category items into a category-filtered grid). A
    #    DERIVED affinity, by contrast, is only a soft preference, so the backfill drops the shop
    #    restriction there to still fill the grid. So: restrict to the category shops iff the filter
    #    is explicit; otherwise no restriction (the _outer_query already applies the price filter).
    backfill_shop_ids = _shop_ids_for_categories(db, filters.categories) if filters.categories else None
    if len(outer_rows) < outer_target and not (filters.categories and not backfill_shop_ids):
        for listing, distance_m in _outer_query(
            db, lat, lng, near_radius_m, outer_cap_m, filters,
            exclude_ids=seen, now=now, shop_ids=backfill_shop_ids,
        ):
            lid = str(listing.id)
            if lid in seen:
                continue
            seen.add(lid)
            outer_rows.append(QuickBuyRow(listing=listing, distance_m=distance_m, bucket=BUCKET_TRENDING))
            if len(outer_rows) >= outer_target:
                break

    # --- Compose: interleave near/outer in per-page blocks so every page keeps the 4:5 mix. ---
    composed: list[QuickBuyRow] = []
    ni = oi = 0
    while len(composed) < max_items and (ni < len(near_rows) or oi < len(outer_rows)):
        for _ in range(near_per_page):
            if ni < len(near_rows):
                composed.append(near_rows[ni]); ni += 1
        for _ in range(outer_per_page):
            if oi < len(outer_rows):
                composed.append(outer_rows[oi]); oi += 1
        # If BOTH lanes are exhausted mid-block, stop (avoid an infinite loop on a short slate).
        if ni >= len(near_rows) and oi >= len(outer_rows):
            break

    return composed[:max_items], near_radius_m
