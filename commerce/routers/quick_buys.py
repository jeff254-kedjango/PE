"""Quick Buys grid endpoint — the §8 Trade right-rail 3×3 discovery grid.

GET /api/v1/quick-buys?lat&lng[&min_price_cents&max_price_cents&categories&radius_m] → a composed
near/interest MIX of buyable listings (see services.quick_buys). Requires a valid commerce_trade
token (same read audience as the feed; fails closed at the auth layer). lat/lng are bounded WGS84.

All filters are validated + clamped HERE at the edge (S-input): price bounds are non-negative ints,
``categories`` is a CSV intersected with the backend allow-list (unknown slugs are silently dropped,
never a 422 — a stale client filter must degrade, not break), and ``radius_m`` is hard-clamped to
feed_max_radius_m (anti-O(n)). Unlike the trending rail this is NOT cached: the grid is
buyer-personal (it reads the caller's own engagement history for affinity), so a shared per-bucket
cache would leak one buyer's personalization to another.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from PE.commerce.core.auth import CommercePrincipal, get_current_principal
from PE.commerce.core.categories import is_valid_category
from PE.commerce.core.config import settings
from PE.commerce.core.database import get_db
from PE.commerce.schemas.quick_buys import QuickBuyItem, QuickBuysResponse
from PE.commerce.services import quick_buys

router = APIRouter(prefix="/quick-buys", tags=["quick-buys"])


def _parse_categories(raw: str | None) -> tuple[str, ...]:
    """CSV → the subset of valid, known category slugs (order-preserving, de-duplicated). Unknown
    slugs are dropped silently: a category filter is a discovery hint, so a stale/garbled value
    should narrow nothing rather than 422 the whole grid."""
    if not raw:
        return ()
    seen: dict[str, None] = {}
    for part in raw.split(","):
        slug = part.strip()
        if slug and is_valid_category(slug) and slug not in seen:
            seen[slug] = None
    return tuple(seen.keys())


@router.get("", response_model=QuickBuysResponse)
def get_quick_buys(
    lat: float = Query(..., ge=-90.0, le=90.0),
    lng: float = Query(..., ge=-180.0, le=180.0),
    min_price_cents: int | None = Query(default=None, ge=0),
    max_price_cents: int | None = Query(default=None, ge=0),
    categories: str | None = Query(default=None, max_length=512),
    radius_m: float | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(get_current_principal),
) -> QuickBuysResponse:
    # Clamp the caller radius to the server cap (anti-O(n)); None ⇒ the service uses its default.
    clamped_radius = min(radius_m, settings.feed_max_radius_m) if radius_m is not None else None
    filters = quick_buys.QuickBuyFilters(
        min_price_cents=min_price_cents,
        max_price_cents=max_price_cents,
        categories=_parse_categories(categories),
        radius_m=clamped_radius,
    )

    rows, near_radius_m = quick_buys.build_quick_buys(
        db, lat, lng, user_uuid=principal.sub, filters=filters,
    )

    # Batch the owning shops' (name, category) in ONE query (no N+1) — display fields only.
    shop_ids = {str(r.listing.shop_id) for r in rows}
    shop_meta: dict[str, tuple[str | None, str | None]] = {}
    if shop_ids:
        from PE.commerce.models.seller import Shop
        for sid, name, category in (
            db.query(Shop.id, Shop.name, Shop.category).filter(Shop.id.in_(list(shop_ids))).all()
        ):
            shop_meta[str(sid)] = (name, category)

    items = [
        QuickBuyItem(
            id=str(r.listing.id),
            shop_id=str(r.listing.shop_id),
            seller_id=str(r.listing.seller_id),
            shop_name=shop_meta.get(str(r.listing.shop_id), (None, None))[0],
            shop_category=shop_meta.get(str(r.listing.shop_id), (None, None))[1],
            title=r.listing.title,
            price_cents=r.listing.price_cents,
            currency=r.listing.currency,
            thumbnail_url=quick_buys.thumbnail_of(r.listing),
            distance_m=round(r.distance_m, 2),
            pricing_mode=r.listing.pricing_mode,
            bucket=r.bucket,
        )
        for r in rows
    ]
    return QuickBuysResponse(
        items=items,
        near_radius_m=near_radius_m,
        page_size=settings.quick_buys_page_size,
    )
