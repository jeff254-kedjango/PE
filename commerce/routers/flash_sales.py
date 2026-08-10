"""Flash Sales grid endpoint — the §8 nationwide "crazy offer" grid under Quick Buys.

GET /api/v1/flash-sales[?lat&lng] → every active-window flash sale on the platform, ranked by
craziness (a precomputed margin score), NATIONWIDE (a Kisumu sale shows in Nairobi). Requires a
valid commerce_trade token (same read audience as the feed; fails closed at the auth layer).

``lat``/``lng`` are OPTIONAL and, when given (bounded WGS84), only add a display-only buyer-relative
distance — they never filter or re-rank (the nationwide contract). Not cached: it's a small,
fast-changing slate and the same for everyone, but windows turn over each hour so a stale cache would
show expired offers; the read is already a bounded indexed ORDER BY, so caching buys little.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from PE.commerce.core.auth import CommercePrincipal, get_current_principal
from PE.commerce.core.config import settings
from PE.commerce.core.database import get_db
from PE.commerce.models.seller import Shop
from PE.commerce.schemas.flash_sales import FlashSaleItem, FlashSalesResponse
from PE.commerce.services import flash_sales, quick_buys

router = APIRouter(prefix="/flash-sales", tags=["flash-sales"])


def _discount_percent(flash_price_cents: int, reference_cents: int) -> int:
    """Whole-percent discount vs the reference, clamped [0, 100]. Display-only ("90% off"). A
    non-positive reference (shouldn't happen — launch rejects it) degrades to 0, never divides."""
    if reference_cents <= 0:
        return 0
    pct = round((reference_cents - flash_price_cents) * 100 / reference_cents)
    return max(0, min(100, pct))


@router.get("", response_model=FlashSalesResponse)
def get_flash_sales(
    lat: float | None = Query(default=None, ge=-90.0, le=90.0),
    lng: float | None = Query(default=None, ge=-180.0, le=180.0),
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(get_current_principal),
) -> FlashSalesResponse:
    # Both or neither coordinate — a lone lat/lng is meaningless (distance needs a full point). A
    # partial pair simply yields no distance rather than erroring the grid.
    has_loc = lat is not None and lng is not None
    rows = flash_sales.build_flash_sales(
        db, lat=lat if has_loc else None, lng=lng if has_loc else None,
    )

    # Batch the owning shops' (name, category) in ONE query (no N+1) — display fields only.
    shop_ids = {str(r.listing.shop_id) for r in rows}
    shop_meta: dict[str, tuple[str | None, str | None]] = {}
    if shop_ids:
        for sid, name, category in (
            db.query(Shop.id, Shop.name, Shop.category).filter(Shop.id.in_(list(shop_ids))).all()
        ):
            shop_meta[str(sid)] = (name, category)

    items = [
        FlashSaleItem(
            id=str(r.listing.id),
            shop_id=str(r.listing.shop_id),
            seller_id=str(r.listing.seller_id),
            shop_name=shop_meta.get(str(r.listing.shop_id), (None, None))[0],
            shop_category=shop_meta.get(str(r.listing.shop_id), (None, None))[1],
            title=r.listing.title,
            flash_price_cents=int(r.listing.flash_price_cents),
            reference_cents=int(r.listing.flash_reference_cents or r.listing.price_cents),
            discount_percent=_discount_percent(
                int(r.listing.flash_price_cents),
                int(r.listing.flash_reference_cents or r.listing.price_cents),
            ),
            currency=r.listing.currency,
            thumbnail_url=quick_buys.thumbnail_of(r.listing),
            expires_at=r.listing.flash_expires_at,
            distance_m=round(r.distance_m, 2) if r.distance_m is not None else None,
            pricing_mode=r.listing.pricing_mode,
        )
        for r in rows
    ]
    return FlashSalesResponse(items=items, page_size=settings.flash_sales_page_size)
