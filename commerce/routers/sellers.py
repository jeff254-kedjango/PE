"""Seller write path — shop/listing creation, POS stock, and the seller's own storefront.

Every endpoint here MUTATES or exposes catalog the caller owns, so all of them require the
granular ``create:trades`` scope (not just the audience scope the read feed needs). The scope
is checked by ``require_scope`` — a read-only ``read:feed`` token is 403 here; a missing token
401; an unconfigured key 503 (auth fails closed, see core/auth.py).

Ownership is enforced in the service layer off the verified token ``sub``; a cross-owner target
returns 404 (not 403) so the API never confirms another seller's rows exist (S6).
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from PE.commerce.core.auth import (
    CommercePrincipal, get_current_principal, require_scope, require_staff,
)
from PE.commerce.core.config import settings
from PE.commerce.core.database import get_db
from PE.commerce.models.boost import BOOST_TIERS
from PE.commerce.models.seller import Seller
from PE.commerce.schemas import boost as boost_schemas
from PE.commerce.schemas import catalog as schemas
from PE.commerce.schemas import weesstock as weesstock_schemas
from PE.commerce.services import (
    boost, boost_cap, catalog, credit_score, flash_sales, reviews, shops,
)

router = APIRouter(tags=["sellers"])

# All seller actions require this granular write permission (minted by the weespas bridge).
_require_write = require_scope("create:trades")

_NOT_FOUND = "Shop or listing not found"  # uniform message — never reveals cross-owner existence


@router.post("/shops", response_model=schemas.ShopOut, status_code=201)
def create_shop(
    body: schemas.ShopCreate,
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(_require_write),
) -> schemas.ShopOut:
    shop = catalog.create_shop(db, principal.sub, body)
    return schemas.to_shop_out(shop)


@router.post(
    "/shops/{shop_id}/listings", response_model=schemas.ListingOut, status_code=201
)
def create_listing(
    shop_id: str,
    body: schemas.ListingCreate,
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(_require_write),
) -> schemas.ListingOut:
    listing = catalog.create_listing(db, principal.sub, shop_id, body)
    if listing is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return schemas.to_listing_out(listing)


@router.post("/posts", response_model=schemas.ListingOut, status_code=201)
def create_post(
    body: schemas.PostCreate,
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(_require_write),
) -> schemas.ListingOut:
    """Publish a plain social POST to the caller's timeline (§8). Unlike a product, a post needs no
    pre-existing shop — the service auto-provisions a minimal personal shop on first post — so this
    always succeeds for an authenticated, create:trades-scoped caller. The post carries no price or
    stock (forced 0 server-side); it flows through the same feed/comments/saves/likes as a product.
    The display-name snapshot rides the token's name claim (S6 — commerce owns no identity)."""
    post = catalog.create_post(
        db, principal.sub,
        body.model_copy(update={"author_name": body.author_name or principal.name or None}),
        body.lat, body.lng,
    )
    return schemas.to_listing_out(post)


@router.patch("/listings/{listing_id}/stock", response_model=schemas.ListingOut)
def adjust_stock(
    listing_id: str,
    body: schemas.StockAdjust,
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(_require_write),
) -> schemas.ListingOut:
    listing = catalog.adjust_stock(db, principal.sub, listing_id, body)
    if listing is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return schemas.to_listing_out(listing)


@router.patch("/listings/{listing_id}", response_model=schemas.ListingOut)
def update_listing(
    listing_id: str,
    body: schemas.ListingUpdate,
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(_require_write),
) -> schemas.ListingOut:
    """Edit the caller's listing (title/description/price/media/pricing/threshold/video flag). 404
    if not owned (no cross-owner existence leak); 422 on an empty patch (schema). A partial update —
    only supplied fields change. Stock is edited via the dedicated POS endpoint, not here."""
    listing = catalog.update_listing(db, principal.sub, listing_id, body)
    if listing is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return schemas.to_listing_out(listing)


@router.delete("/listings/{listing_id}", status_code=204)
def delete_listing(
    listing_id: str,
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(_require_write),
) -> None:
    """Soft-delete the caller's listing (removes it from every buyer-facing lane). 404 if not owned.
    Idempotent — deleting an already-removed listing is a clean no-op. The row is retained (inactive)
    so immutable order/receipt/review history is never orphaned."""
    if not catalog.soft_delete_listing(db, principal.sub, listing_id):
        raise HTTPException(status_code=404, detail=_NOT_FOUND)


@router.post("/listings/{listing_id}/promote", response_model=schemas.ListingOut)
def promote_listing(
    listing_id: str,
    body: schemas.PromoteRequest,
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(_require_write),
) -> schemas.ListingOut:
    """Open a "selling now" window on the caller's listing (§8 ephemerality). 404 if not owned
    (no cross-owner existence leak); 422 if the mode/duration is out of bounds (anti-abuse).
    Re-promoting overwrites the existing window (extend = promote again)."""
    try:
        listing = catalog.promote_listing(
            db, principal.sub, listing_id,
            mode=body.mode, duration_seconds=body.duration_seconds,
        )
    except catalog.PromotionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if listing is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return schemas.to_listing_out(listing)


@router.delete("/listings/{listing_id}/promotion", response_model=schemas.ListingOut)
def clear_promotion(
    listing_id: str,
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(_require_write),
) -> schemas.ListingOut:
    """Remove any "selling now" window from the caller's listing (back to an ordinary always-on
    listing). 404 if not owned. Idempotent — clearing an un-promoted listing is a clean no-op."""
    listing = catalog.clear_promotion(db, principal.sub, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return schemas.to_listing_out(listing)


@router.post("/listings/{listing_id}/flash-sale", response_model=schemas.ListingOut)
def launch_flash_sale(
    listing_id: str,
    body: schemas.FlashSaleRequest,
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(_require_write),
) -> schemas.ListingOut:
    """Launch a §8 flash sale (a nationwide, ≤1-hour "crazy offer") on the caller's listing. 404 if
    not owned (no cross-owner existence leak); 422 if the duration is out of bounds, the price is
    non-positive, the listing is bargain-priced, or the price isn't actually below the comparable
    market (a non-discount isn't a flash sale). Re-launching overwrites the window + recomputes the
    craziness score (extend = launch again). The listing's normal price is left untouched — the
    flash price is a temporary override that reverts when the window closes."""
    try:
        listing = flash_sales.launch_flash_sale(
            db, principal.sub, listing_id,
            flash_price_cents=body.flash_price_cents, duration_seconds=body.duration_seconds,
        )
    except flash_sales.FlashSaleError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if listing is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return schemas.to_listing_out(listing)


@router.delete("/listings/{listing_id}/flash-sale", response_model=schemas.ListingOut)
def clear_flash_sale(
    listing_id: str,
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(_require_write),
) -> schemas.ListingOut:
    """Remove any flash sale from the caller's listing (back to its ordinary price). 404 if not
    owned. Idempotent — clearing a listing with no flash sale is a clean no-op."""
    listing = flash_sales.clear_flash_sale(db, principal.sub, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return schemas.to_listing_out(listing)


@router.post("/boosts", response_model=boost_schemas.BoostGrantOut, status_code=201)
def create_boost(
    body: boost_schemas.BoostRequest,
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(_require_write),
) -> boost_schemas.BoostGrantOut:
    """Open a §8.3 Boost (paid-style reach in the sponsored lane) on the caller's listing or shop.
    Spends one of the day's free chances for the chosen tier. 404 if the target isn't owned (no
    existence leak); 422 on a bad tier/duration; 429 when the day's free chances for that tier are
    spent. Idempotent per (target, tier, business-day): re-requesting the same target/tier today
    REPLAYS the existing grant and does not spend a second chance."""
    try:
        grant = boost.grant_boost(
            db, principal.sub,
            target_type=body.target_type, target_id=body.target_id, tier=body.tier,
            duration_seconds=body.duration_seconds,
        )
    except boost.BoostError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except boost.QuotaExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    if grant is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return boost_schemas.to_boost_grant_out(grant)


@router.get("/boosts/allowances", response_model=boost_schemas.BoostAllowancesOut)
def my_boost_allowances(
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(_require_write),
) -> boost_schemas.BoostAllowancesOut:
    """The caller's remaining free Boost chances per tier for the current business day. A caller
    who has never sold (no Seller row) sees the full daily caps (nothing spent yet)."""
    seller = catalog.get_my_storefront(db, principal.sub)
    tiers = []
    for tier in BOOST_TIERS:
        cap = boost.tier_daily_cap(tier)
        remaining = cap if seller is None else boost.remaining_allowance(db, seller.id, tier)
        tiers.append(boost_schemas.TierAllowanceOut(tier=tier, daily_cap=cap, remaining=remaining))
    return boost_schemas.BoostAllowancesOut(
        business_date=boost.business_date(datetime.now(timezone.utc)),
        tiers=tiers,
    )


@router.delete("/boosts/{grant_id}", status_code=204)
def revoke_boost(
    grant_id: str,
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(_require_write),
) -> None:
    """End a Boost early (owner-only). 404 if not owned. The spent chance is NOT refunded (reach
    began the moment the grant opened)."""
    if not boost.revoke_boost(db, principal.sub, grant_id):
        raise HTTPException(status_code=404, detail=_NOT_FOUND)


@router.get("/boosts/tiers", response_model=boost_schemas.BoostTiersOut)
def boost_tiers(
    _principal: CommercePrincipal = Depends(_require_write),
) -> boost_schemas.BoostTiersOut:
    """Server-authoritative Boost tier catalogue — reach radius, daily free cap, default window, and
    the NOMINAL per-tier price (display-only; §8.3 real charging is gated on the §6 Daraja rail, so
    price_kes is informational until then). The FE chooser reads THIS instead of hard-coding reach
    copy/caps, so the two can never drift. Every value is derived from config at request time."""
    price_map = settings.boost_tier_price_kes_map
    tiers = []
    for tier in BOOST_TIERS:
        scope_kind, radius_m = boost.tier_scope(tier)
        tiers.append(boost_schemas.BoostTierMetaOut(
            tier=tier,
            scope_kind=scope_kind,
            radius_m=radius_m,
            daily_free_cap=boost.tier_daily_cap(tier),
            duration_default_seconds=settings.boost_default_duration_seconds,
            price_kes=price_map.get(tier, 0),
        ))
    return boost_schemas.BoostTiersOut(tiers=tiers)


# ----------------------------- per-shop sponsored-cap override (§8.3 item 1) -----------------------------

@router.get("/shops/{shop_id}/sponsored-cap", response_model=boost_schemas.CapOverrideStatusOut)
def get_sponsored_cap(
    shop_id: str,
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(_require_write),
) -> boost_schemas.CapOverrideStatusOut:
    """The current per-shop sponsored-cap override status for the caller's OWN shop — a
    NON-DESTRUCTIVE read so opening the seller control can't reset an approved override (contrast
    the POST below, which re-opens it as pending). 404 if the shop isn't owned (no existence leak).
    Exposes the server-authoritative ceiling + global default so the FE never hard-codes them."""
    owned, row = boost_cap.get_override(db, principal.sub, shop_id)
    if not owned:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return boost_schemas.CapOverrideStatusOut(
        override=boost_schemas.to_cap_override_out(row) if row is not None else None,
        max_cap=settings.boost_cap_override_max,
        default_cap=settings.feed_sponsored_max_per_shop,
    )


@router.post("/shops/{shop_id}/sponsored-cap", response_model=boost_schemas.CapOverrideOut)
def apply_sponsored_cap(
    shop_id: str,
    body: boost_schemas.CapOverrideRequest,
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(_require_write),
) -> boost_schemas.CapOverrideOut:
    """A seller applies for a per-shop OVERRIDE of the sponsored-lane cap on their OWN shop. Staff
    must approve it before it affects the feed (§8.3 item 1). 404 if the shop isn't owned (no
    existence leak); 422 on a bad cap (schema-bounded, service re-clamps to boost_cap_override_max).
    Idempotent: re-applying updates the request and re-opens it as pending."""
    row = boost_cap.apply_for_override(db, principal.sub, shop_id, requested_cap=body.requested_cap)
    if row is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return boost_schemas.to_cap_override_out(row)


@router.get("/admin/sponsored-caps", response_model=boost_schemas.PendingCapListOut)
def list_pending_sponsored_caps(
    limit: int = 100,
    db: Session = Depends(get_db),
    _staff: CommercePrincipal = Depends(require_staff),
) -> boost_schemas.PendingCapListOut:
    """Staff-only: the pending per-shop cap applications awaiting a decision (FIFO, bounded)."""
    rows = boost_cap.list_pending(db, limit=limit)
    return boost_schemas.PendingCapListOut(
        overrides=[boost_schemas.to_cap_override_out(r) for r in rows],
        max_cap=settings.boost_cap_override_max,
    )


@router.post("/admin/sponsored-caps/{override_id}/decide", response_model=boost_schemas.CapOverrideOut)
def decide_sponsored_cap(
    override_id: str,
    body: boost_schemas.CapOverrideDecision,
    db: Session = Depends(get_db),
    staff: CommercePrincipal = Depends(require_staff),
) -> boost_schemas.CapOverrideOut:
    """Staff-only: approve (with an absolute approved_cap, clamped to boost_cap_override_max) or
    reject a pending per-shop cap application. 404 if the id doesn't exist. Only an approval with a
    positive cap ever reaches the feed hot path."""
    row = boost_cap.decide_override(
        db, staff.sub, override_id, approve=body.approve, approved_cap=body.approved_cap,
    )
    if row is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return boost_schemas.to_cap_override_out(row)


@router.post("/sellers/me/stock/bulk-csv", response_model=schemas.BulkStockOut)
def bulk_update_stock(
    body: schemas.BulkStockIn,
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(_require_write),
) -> schemas.BulkStockOut:
    """Apply a `listing_id,stock_qty` CSV to the caller's listings in one transaction
    (§8 Chunk E3). All-or-nothing on parse; unowned ids are skipped silently. Returns
    the count updated + the ids updated (for surgical FE cache invalidation).

    422 on any CSV parse error, out-of-range stock, duplicate id, oversized body, or
    empty CSV — the error message names the offending line so the seller can fix it.
    """
    try:
        result = catalog.bulk_update_stock(db, principal.sub, body.csv)
    except catalog.BulkStockError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return schemas.BulkStockOut(
        updated_count=result.updated_count,
        skipped_count=result.skipped_count,
        updated_ids=result.updated_ids,
    )


@router.get("/sellers/me/low-stock", response_model=schemas.LowStockOut)
def my_low_stock(
    floor: int = 5,
    limit: int = 50,
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(_require_write),
) -> schemas.LowStockOut:
    """The caller's active PRODUCT listings at or below the threshold (§8 Chunk E2).

    ``floor`` (default 5) is absolute: a listing appears when ``stock_qty <= floor`` and for
    no other reason. Set it to 5 and you get every product with 5 or fewer in stock.

    Grouped by shop (a seller with several shops gets a header per shop), most-urgent-first
    within each group. Excludes posts (no inventory) and inactive listings.
    """
    if floor < 0:
        floor = 0
    if not (1 <= limit <= 200):
        limit = 50
    items = catalog.low_stock_listings(db, principal.sub, floor=floor, limit=limit)
    out_items = [schemas.to_listing_out(li) for li in items]

    # Shop names in ONE batched query (no N+1), then a single ordered pass to group. The service
    # already ordered by shop_id, so consecutive runs share a shop — grouping is O(n), and the
    # emitted group order follows that ordering deterministically.
    names = catalog.shop_meta(db, sorted({li.shop_id for li in out_items}))
    groups: list[schemas.LowStockGroup] = []
    for li in out_items:
        if not groups or groups[-1].shop_id != li.shop_id:
            meta = names.get(li.shop_id)
            groups.append(schemas.LowStockGroup(
                shop_id=li.shop_id,
                # A listing always has a live shop; default defensively rather than 500.
                shop_name=meta[0] if meta else "Shop",
                items=[],
            ))
        groups[-1].items.append(li)

    return schemas.LowStockOut(floor=floor, groups=groups)


@router.get("/sellers/me/credit-profile", response_model=weesstock_schemas.CreditProfileOut)
def my_credit_profile(
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(_require_write),
) -> weesstock_schemas.CreditProfileOut:
    """The caller's OWN WeesStock credit profile (§WeesStock F2).

    Self-view only: the profile is computed for the seller row behind the token ``sub``, so
    there is no id parameter to tamper with and no way to request somebody else's numbers.
    The financier-facing view of another shop is a separate, consent-gated endpoint (F4) —
    deliberately NOT reachable from here.

    A caller who has never sold (no seller row) gets a zeroed, unscoreable profile rather than
    a 404: "you have no history yet" is the honest answer for a shop owner who just signed up,
    and a 404 would make the card render an error for a perfectly valid new user.

    Not cached. The underlying aggregates are a fixed handful of indexed queries, and a seller
    refreshing after a sale must see the sale — a stale credit number is worse than a slow one.
    """
    seller = db.query(Seller).filter(Seller.user_uuid == principal.sub).one_or_none()
    now = datetime.now(timezone.utc)
    if seller is None:
        # Synthesise an empty seller for the pure scorer rather than branching the response
        # shape. tenure 0 + no rows ⇒ score None, missing = both gates, every component 0.
        seller = Seller(user_uuid=principal.sub, display_name="", created_at=now)
    profile = credit_score.compute_credit_profile(db, seller, now=now)
    return weesstock_schemas.to_credit_profile_out(profile)


@router.get("/shops/mine", response_model=schemas.StorefrontOut)
def my_storefront(
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(_require_write),
) -> schemas.StorefrontOut:
    """The caller's own storefront — every shop and ALL listings (in- and out-of-stock), with
    derived low/out-of-stock flags. A caller who has never sold gets an empty storefront."""
    seller = catalog.get_my_storefront(db, principal.sub)
    if seller is None:
        return schemas.StorefrontOut(seller_id="", display_name="", shops=[])
    # One O(1) AVG+COUNT for the seller's own proof-of-purchase rating (increment 6 §8).
    avg, count = reviews.seller_rating(db, seller.id)
    return schemas.to_storefront_out(seller, rating=avg, review_count=count)


@router.get("/sellers/{seller_id}/storefront", response_model=schemas.PublicStorefrontOut)
def public_storefront(
    seller_id: str,
    db: Session = Depends(get_db),
    # Any authenticated buyer may view any seller's PUBLIC profile — only the audience scope is
    # required, NOT create:trades (that gates write/owner views). Commerce fails closed (no
    # public, token-less endpoints).
    principal: CommercePrincipal = Depends(get_current_principal),
) -> schemas.PublicStorefrontOut:
    """Any seller's public storefront: their shops with ONLY active, in-stock listings, plus the
    seller's proof-of-purchase rating. No POS internals are exposed (stock/threshold/intent/
    inactive items are hidden — S6). 404 if the seller doesn't exist."""
    seller = catalog.get_public_storefront(db, seller_id)
    if seller is None:
        raise HTTPException(status_code=404, detail="Seller not found")
    avg, count = reviews.seller_rating(db, seller.id)
    return schemas.to_public_storefront_out(
        seller, catalog.public_visible_listings, rating=avg, review_count=count,
    )


@router.get("/shops/{shop_id}/profile", response_model=schemas.ShopProfileOut)
def shop_profile(
    shop_id: str,
    db: Session = Depends(get_db),
    # A buyer action — any authenticated user may view any shop's public profile card; only the
    # audience scope is required (NOT create:trades). Commerce fails closed (no token-less reads).
    principal: CommercePrincipal = Depends(get_current_principal),
) -> schemas.ShopProfileOut:
    """The §8 hovercard for a shop: its seller-published business card (name/description/contact),
    follower count + whether THIS viewer follows it, and the owning seller's proof-of-purchase
    rating. 404 if the shop doesn't exist (no fabricated card). Shop ids are public (they ride in
    the feed), so there is no ownership gate — only seller-published fields + opaque ids are
    returned (no PII, S6)."""
    shop = shops.get_shop(db, shop_id)
    if shop is None:
        raise HTTPException(status_code=404, detail="Shop not found")
    avg, count = reviews.seller_rating(db, shop.seller_id)
    return schemas.to_shop_profile_out(
        shop,
        follower_count=shops.follower_count(db, shop_id),
        following=shops.is_following(db, principal.sub, shop_id),
        rating=avg,
        review_count=count,
    )


@router.post("/shops/by-property", response_model=schemas.ShopsByPropertyResponse)
def shops_by_property(
    body: schemas.ShopsByPropertyRequest,
    db: Session = Depends(get_db),
    # A read: any authenticated caller with the audience scope may ask which footprints are shops
    # (NOT create:trades — this is not a write/owner view). Commerce fails closed (no token-less
    # reads). This is the S2S entry the weespas map aggregator calls with a minted read:feed token.
    principal: CommercePrincipal = Depends(get_current_principal),
) -> schemas.ShopsByPropertyResponse:
    """Batch "which of these building footprints (property_uuids) are shops?" — §8.1a, the
    weespas-aggregated shops-on-the-InSAR-map path. POST (not GET) because the batch of up to
    ``SHOPS_BY_PROPERTY_BATCH_MAX`` uuids is unsafe in a query string; the request has no side
    effects. The batch is bounded at the schema edge (anti-O(n), S8) and the query is a single
    indexed IN.

    The response carries NO lat/lng (S6): the footprint is already public on the map, so a shop's
    raw coordinates never leave commerce. Only seller-published, non-PII display fields ride along
    (name/category). A uuid that matches no shop is simply absent from the list; a footprint
    shared by two shops yields two entries (property_uuid is not unique)."""
    rows = shops.shops_by_property(db, body.property_uuids)
    return schemas.ShopsByPropertyResponse(
        shops=[
            schemas.ShopByPropertyOut(
                property_uuid=r.property_uuid,
                shop_id=str(r.id),
                name=r.name,
                category=r.category,
            )
            for r in rows
        ]
    )


@router.get("/shops/{shop_id}/seller", response_model=schemas.ShopSellerOut)
def shop_seller(
    shop_id: str,
    db: Session = Depends(get_db),
    # A read (same trust class as /shops/by-property): the audience scope suffices, NOT
    # create:trades. This is the S2S entry the weespas contact uplink calls with a minted
    # read:feed token to learn the shop owner's channel. Commerce fails closed (no token-less read).
    principal: CommercePrincipal = Depends(get_current_principal),
) -> schemas.ShopSellerOut:
    """Resolve a shop to its owning seller's weespas ``user_uuid`` — §8.1b pair-radiate.

    The weespas contact uplink knows the shop the buyer opened and needs the seller's per-user
    SSE channel key to publish the anonymized "a viewer is looking" pulse. Exactly one field
    crosses: the seller's already-synchronized weespas identity (S6 — no shop meta, no buyer
    data, no coordinates). One indexed join, O(1). An unknown shop_id yields ``seller_uuid:
    null`` (200, not 404) so the uplink degrades to buyer-local glow without treating a stale
    pin as an error."""
    seller_uuid = shops.seller_uuid_for_shop(db, shop_id)
    return schemas.ShopSellerOut(shop_id=shop_id, seller_uuid=seller_uuid)


@router.get("/shops/@{handle}/storefront", response_model=schemas.PublicStorefrontOut)
def storefront_by_handle(
    handle: str,
    db: Session = Depends(get_db),
    # Same trust class as GET /sellers/{seller_id}/storefront — audience scope only, not
    # create:trades. Commerce fails closed (no token-less reads).
    principal: CommercePrincipal = Depends(get_current_principal),
) -> schemas.PublicStorefrontOut:
    """The public storefront for a shop's HANDLE (the shareable /shop/<handle> URL).

    Resolves the handle → shop → owning seller in ONE indexed lookup, then returns the SAME
    PublicStorefrontOut the seller-id form returns. This is the canonical entry: a shared
    /shop/<handle> URL never round-trips through a seller_id first. An unknown handle is a 404
    (same shape as an unknown seller_id, so the frontend has one error path).

    The handle is validator-normalized before the DB read so a mixed-case URL (/shop/Mama-Mboga)
    resolves to the same lowercased row (/shop/mama-mboga); an INVALID handle (bad grammar) is a
    404, not a 422 — a URL is a URL, and probing for grammar via 422s would be an existence leak
    for the reserved-word deny-list."""
    try:
        normalized = shops.normalize_and_validate_handle(handle)
    except shops.HandleError:
        # An unresolvable handle URL is a 404 (uniform with unknown-seller), NOT a 422.
        raise HTTPException(status_code=404, detail="Storefront not found")
    shop = shops.get_shop_by_handle(db, normalized)
    if shop is None:
        raise HTTPException(status_code=404, detail="Storefront not found")
    seller = catalog.get_public_storefront(db, shop.seller_id)
    if seller is None:
        # Defensive: a shop without a seller row can't exist under the FK, but the read is
        # asymmetric (different session/tx window) so we degrade to 404 rather than 500.
        raise HTTPException(status_code=404, detail="Storefront not found")
    avg, count = reviews.seller_rating(db, seller.id)
    return schemas.to_public_storefront_out(
        seller, catalog.public_visible_listings, rating=avg, review_count=count,
    )


@router.get("/shops/handle-available", response_model=schemas.HandleAvailability)
def handle_available(
    handle: str,
    db: Session = Depends(get_db),
    # Any authenticated caller can probe (a seller composing their handle in the create-shop UI
    # needs this before they commit). Audience scope only. Rate-limiting is a downstream concern
    # (nginx/cloudflare); this endpoint does a single indexed SELECT so a burst is cheap.
    principal: CommercePrincipal = Depends(get_current_principal),
) -> schemas.HandleAvailability:
    """Live availability probe for the create-shop / handle-claim UI. Returns:
      * available=true when the handle is BOTH syntactically legal AND not currently held;
      * available=false with a `reason` slug matching the PATCH error detail (handle-syntax,
        handle-reserved, handle-length, handle-required, handle-taken) so the frontend has ONE
        error-message map for both endpoints.
    Never raises for a validation failure — the whole point of the probe is that the frontend
    calls it on every keystroke. A syntax error is a normal answer here."""
    try:
        normalized = shops.normalize_and_validate_handle(handle)
    except shops.HandleError as exc:
        return schemas.HandleAvailability(
            handle=(handle or "").strip().lower(),
            available=False,
            reason=exc.detail,
        )
    if shops.is_handle_available(db, normalized):
        return schemas.HandleAvailability(handle=normalized, available=True)
    return schemas.HandleAvailability(handle=normalized, available=False, reason="handle-taken")


@router.patch("/shops/{shop_id}/handle", response_model=schemas.ShopOut)
def claim_handle(
    shop_id: str,
    body: schemas.HandleClaim,
    db: Session = Depends(get_db),
    # Owner-only mutation — same scope as every other write on this router.
    principal: CommercePrincipal = Depends(_require_write),
) -> schemas.ShopOut:
    """Claim a shareable handle for one of the caller's shops (§8 storefront: /shop/<handle>).

    ONE-SHOT: once set, permanent (a rename would break every previously-shared link). Same-value
    re-submits are idempotent. See services.shops.claim_handle for the full policy contract;
    HandleError.status_code maps 1:1 to the HTTPException status:
      * 422 handle-required / handle-length / handle-syntax / handle-reserved — syntax failures.
      * 409 handle-taken — collides with another shop's handle (case-insensitive).
      * 409 handle-locked — this shop already has a DIFFERENT handle set (one-shot policy).
    A shop the caller doesn't own is a uniform 404 (no cross-owner existence leak, S6)."""
    try:
        shop = shops.claim_handle(db, principal.sub, shop_id, body.handle)
    except shops.HandleError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    if shop is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return schemas.to_shop_out(shop)


@router.post("/shops/{shop_id}/follow", response_model=schemas.FollowToggleOut)
def toggle_follow(
    shop_id: str,
    db: Session = Depends(get_db),
    # Following is a BUYER action (the §8 "Notify" button), open to any authenticated user — no
    # seller scope. Idempotent; a double-follow stays following.
    principal: CommercePrincipal = Depends(get_current_principal),
) -> schemas.FollowToggleOut:
    """Toggle the caller's follow ("Notify") on a shop — subscribe to its updates. 404 if the shop
    doesn't exist. Persists the subscription only; delivery of a followed shop's stock changes is a
    downstream seam (no notification store yet)."""
    result = shops.toggle_follow(db, principal.sub, shop_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Shop not found")
    following, count = result
    return schemas.FollowToggleOut(shop_id=shop_id, following=following, follower_count=count)
