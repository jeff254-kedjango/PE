"""Global trade-search endpoint — the trade half of the navbar's unified search.

GET /api/v1/search?q&lat&lng&limit → trade listings whose title / description / shop name match
``q``, ranked nearest-first (nationwide reach). Requires a valid commerce_trade token (same audience
gate as the feed; fails closed at the auth layer). lat/lng are bounded WGS84 (S-input); ``limit`` is
server-capped (anti-O(n) — S8). The weespas frontend fires this CONCURRENTLY with the existing
property search and merges the two into one navbar results panel — commerce never cross-DB-joins.
"""
import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from PE.commerce.core.auth import CommercePrincipal, get_current_principal
from PE.commerce.core.config import settings
from PE.commerce.core.database import get_db
from PE.commerce.schemas.search import TradeSearchResponse, to_search_response
from PE.commerce.services import search

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/search", tags=["search"])


@router.get("", response_model=TradeSearchResponse)
def search_trade(
    q: str = Query(..., max_length=200),
    lat: float = Query(..., ge=-90.0, le=90.0),
    lng: float = Query(..., ge=-180.0, le=180.0),
    limit: int | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(get_current_principal),
) -> TradeSearchResponse:
    # Clamp the page to the server max — a caller cannot request a huge page (S8). A too-short query
    # returns [] inside the service (its own backstop), so no special-casing here.
    page_size = min(limit or settings.search_max_results, settings.search_max_results)
    hits = search.search_trade(db, q, lat, lng, limit=page_size)

    # Saturation tripwire (mirrors feed.py): if the bounded candidate pull filled its ceiling there
    # may be nearer matches beyond it that were never returned — the far tail is silently dropped.
    # Today the catalogue keeps k well under the cap; log it (WARNING) so a densifying catalogue
    # surfaces as an ops signal rather than a silent correctness gap. The remedy when it recurs is a
    # dedicated search index tier, not a bigger cap.
    if len(hits) >= settings.search_max_candidates:
        logger.warning(
            "trade search saturated the candidate cap (%d) for q=%r at (%.5f, %.5f) — nearer "
            "matches beyond the cap may be dropped; consider a dedicated search index if this recurs.",
            settings.search_max_candidates, q, lat, lng,
        )

    return to_search_response(hits, q)
