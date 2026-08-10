"""Proximity feed endpoint — the vertical slice that proves the moat.

GET /api/v1/feed?lat&lng&radius_m&cursor&limit → listings near the caller, ranked by
proximity × freshness × intent. Requires a valid commerce_trade token (fails closed).
``radius_m`` and ``limit`` are server-capped (anti-O(n) — S8).
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from PE.commerce.core.auth import CommercePrincipal, get_current_principal
from PE.commerce.core.config import settings
from PE.commerce.core.database import get_db
from PE.commerce.schemas.feed import FeedResponse, to_feed_item
from PE.commerce.services import (
    catalog, engagement, feed as feed_service, proximity, reviews,
)

router = APIRouter(prefix="/feed", tags=["feed"])


@router.get("", response_model=FeedResponse)
def get_feed(
    lat: float = Query(..., ge=-90.0, le=90.0),
    lng: float = Query(..., ge=-180.0, le=180.0),
    radius_m: float | None = Query(default=None, gt=0.0),
    cursor: str | None = Query(default=None),
    limit: int | None = Query(default=None, gt=0),
    kind: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(get_current_principal),
) -> FeedResponse:
    # Clamp to server bounds — a caller cannot force a full-table scan with a huge radius
    # or page (S8).
    radius = min(radius_m or settings.feed_default_radius_m, settings.feed_max_radius_m)
    page_size = min(limit or settings.feed_page_size, settings.feed_max_page_size)
    # The §8 toggle: validate against the known kinds (reject a bogus value rather than silently
    # returning the unfiltered feed, which would mislead the client about what it's showing).
    if kind is not None and kind not in proximity.FEED_KINDS:
        raise HTTPException(status_code=422, detail="invalid kind")

    result = feed_service.build_feed(
        db, lat, lng, radius, cursor=cursor, limit=page_size, kind=kind
    )
    # Display-only social proof, all as single batch GROUP BYs over the whole page (no N+1):
    # save counts + comment counts (per listing) and the seller's proof-of-purchase rating (per
    # seller). None touches ranking — the page is already ordered by build_feed; surfacing these
    # must not re-rank, or an established/noisy seller would bury a closer newcomer (the cold-start
    # we avoid).
    items = result["items"]
    listing_ids = [str(s.listing.id) for s in items]
    counts = engagement.save_counts(db, listing_ids)
    comment_counts = engagement.comment_counts(db, listing_ids)
    ratings = reviews.seller_ratings(db, [str(s.listing.seller_id) for s in items])
    # Shop display-meta (name + avatar) for the social header — one batch query, no N+1.
    shops = catalog.shop_meta(db, [str(s.listing.shop_id) for s in items])
    # Which of this page's listings THIS caller has already saved — one membership query so each
    # card's heart reflects prior saves (not defaulted to un-saved on mount). Display-only.
    saved = engagement.saved_listing_ids(db, principal.sub, listing_ids)
    return FeedResponse(
        items=[
            to_feed_item(
                s,
                counts.get(str(s.listing.id), 0),
                ratings.get(str(s.listing.seller_id)),
                comment_counts.get(str(s.listing.id), 0),
                shops.get(str(s.listing.shop_id)),
                saved_by_me=str(s.listing.id) in saved,
            )
            for s in items
        ],
        next_cursor=result["next_cursor"],
        widened=result["widened"],
        nearest_distance_m=result["nearest_distance_m"],
        immediate_count=result["immediate_count"],
    )
