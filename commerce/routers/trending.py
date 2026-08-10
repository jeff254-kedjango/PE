"""Trending rail endpoint — the §8 queue of boosted PRODUCTS near the buyer.

GET /api/v1/trending?lat&lng → the boosted LISTINGS reaching the buyer's locality, as a queue the
client renders as a per-slot decay board (product cards: title + price + category icon). Requires a
valid commerce_trade token (same audience gate as the feed; fails closed at the auth layer). lat/lng
are bounded WGS84 (S-input).

The queue is a PURE function of the locality bucket, so it is read through a per-bucket Redis cache
(TTL = poll_seconds): every viewer in a locality shares one compute per poll window. The decay
animation is client-local, so the cache need not track sub-slot windows. The cache fails OPEN — a
Redis blip degrades to a direct DB recompute, never an error (the rail is a discovery surface, not a
security boundary).
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from PE.commerce.core.auth import CommercePrincipal, get_current_principal
from PE.commerce.core.database import get_db
from PE.commerce.schemas.trending import TrendingSlate, to_trending_slate
from PE.commerce.services import trending, trending_cache

router = APIRouter(prefix="/trending", tags=["trending"])


@router.get("", response_model=TrendingSlate)
def get_trending(
    lat: float = Query(..., ge=-90.0, le=90.0),
    lng: float = Query(..., ge=-180.0, le=180.0),
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(get_current_principal),
) -> TrendingSlate:
    # Compute the locality bucket up front so we can probe the shared cache BEFORE touching the DB.
    _blat, _blng, bucket = trending.bucket_for(lat, lng)
    cached = trending_cache.get(bucket)
    if cached is not None:
        # Stored as the serialised response JSON — return it verbatim (no recompute, no DB hit).
        return TrendingSlate.model_validate_json(cached)

    slate = trending.build_slate(db, lat, lng)
    out = to_trending_slate(slate)
    # Cache the queue for one poll window (queue membership changes slowly; the per-slot decay is
    # client-local, so we don't track sub-slot windows here). Worst-case a new boost is invisible to
    # a cached bucket for up to poll_seconds — acceptable for a discovery surface. Fail-open: a Redis
    # miss/blip just recomputes.
    trending_cache.set(bucket, out.model_dump_json(), slate.poll_seconds)
    return out
