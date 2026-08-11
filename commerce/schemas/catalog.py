"""Seller write-path schemas — shop/listing creation, POS stock adjustment, storefront view.

Validation rules enforced here (the API boundary, before anything touches the DB):
  * money + counts are NON-NEGATIVE INTEGERS (``ge=0``) — integer cents only, never float (S9);
  * lat/lng are bounded to valid WGS84 ranges (same bounds the feed query enforces);
  * a stock adjustment carries EXACTLY ONE of an absolute ``stock_qty`` or a relative ``delta``
    (a POS sale sends ``delta:-1``) — both-or-neither is a 422, resolved by a model validator.

Responses surface no PII (S6): only opaque ids, the seller's own ``display_name`` (self-chosen),
and the synchronized ``property_uuid`` for the client-side InSAR Confirmed-badge stitch (§3).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from PE.commerce.core.categories import is_valid_category
from PE.commerce.services import flash_sales, ranking

# A listing prices either at a fixed sticker (order locks immediately) or via bargain (order
# opens a §7 negotiation). Single source of truth for the legal values, used by create + out.
PricingMode = Literal["fixed", "bargain"]

# §8 ephemerality: a "selling now" promotion is either evergreen (boost fades on expiry but the
# listing stays in the feed) or story (the post disappears from the feed on expiry; listing +
# stock untouched). Mirrors models.listing.PROMO_MODES — kept as a Literal so an unknown mode is
# a 422 at the API boundary before the service runs.
PromoMode = Literal["evergreen", "story"]

# §8 social timeline: a listing is either a sellable "product" or a plain social "post" (text +
# media, no price/stock). Mirrors models.listing.POST_KINDS — a Literal so an unknown kind is a 422
# at the API boundary.
PostKind = Literal["product", "post"]

# Free-text description cap — generous for a few paragraphs, bounded so a single listing can't
# dump unbounded text into every feed payload (mirrors the comment cap). The service trims first,
# then enforces; the schema cap here is the API-boundary guard (422 before the service runs).
DESCRIPTION_MAX_LEN = 2000

# A shop's profile "about" blurb is bounded by WORD count (the product spec: ≤200 words) — and a
# generous char cap as a hard backstop so word-counting never sees an unbounded string (a 200-word
# blurb is well under this). Both are enforced at the API edge (422) before the service runs.
SHOP_DESCRIPTION_MAX_WORDS = 200
SHOP_DESCRIPTION_MAX_CHARS = 4000
SHOP_CONTACT_MAX_LEN = 255


def _word_count(text: str) -> int:
    """Whitespace-delimited word count — the unit the 200-word shop-blurb cap is expressed in."""
    return len(text.split())


# ----------------------------- requests -----------------------------

class ShopCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    lat: float = Field(ge=-90.0, le=90.0)
    lng: float = Field(ge=-180.0, le=180.0)
    # Optional self-chosen storefront name; first write auto-provisions the Seller from the
    # token sub, so this is how a brand-new seller names themselves.
    display_name: str = Field(min_length=1, max_length=120)
    # Seller-PUBLISHED business card (opt-in, NOT account PII — S6). The blurb is word-bounded by a
    # validator below (≤200 words); the contact is a single public line the seller elects to show.
    description: str | None = Field(default=None, max_length=SHOP_DESCRIPTION_MAX_CHARS)
    contact: str | None = Field(default=None, max_length=SHOP_CONTACT_MAX_LEN)
    # Shop profile picture / LOGO and wide banner (§8) — media URLs from the weespas upload pipeline
    # (/uploads/... relative or absolute), same shape as a listing's media_urls. Both opt-in and
    # nullable. The avatar doubles as the business logo shown on promoted cards (that's surfaced to
    # the seller at upload time). Not PII (S6) — seller-published storefront imagery.
    avatar_url: str | None = Field(default=None, max_length=512)
    banner_url: str | None = Field(default=None, max_length=512)
    # Optional trade category (§8 trending rail color). A slug from core.categories — validated
    # against the allow-list below (422 on an unknown value); None ⇒ un-categorised.
    category: str | None = Field(default=None, max_length=40)
    # Optional synchronized UUID into weespas/InSAR (the building this shop sits on). Never a FK.
    property_uuid: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _description_within_word_cap(self) -> "ShopCreate":
        if self.description is not None and _word_count(self.description) > SHOP_DESCRIPTION_MAX_WORDS:
            raise ValueError(f"description must be at most {SHOP_DESCRIPTION_MAX_WORDS} words")
        return self

    @model_validator(mode="after")
    def _category_is_known(self) -> "ShopCreate":
        # An explicitly supplied category MUST be a recognised slug (no free-text into the rail).
        # None stays legal (unset); only a non-None unknown value is rejected.
        if self.category is not None and not is_valid_category(self.category):
            raise ValueError("category must be a known shop category")
        return self


class HandleClaim(BaseModel):
    """PATCH /shops/{shop_id}/handle body — the seller's one-shot claim on a shareable URL slug.
    The service enforces the full grammar (services.shops.normalize_and_validate_handle); the
    schema only bounds the raw length so an attacker can't POST 1 MB of text and force the
    validator to work harder than necessary. Case-folding + trimming + reserved-word deny-list
    all happen server-side — the client never carries authority over the final value."""
    handle: str = Field(min_length=1, max_length=64)


class HandleAvailability(BaseModel):
    """GET /shops/handle-available?handle=X response — a tiny probe the frontend uses for the
    live "is this name free?" check as the seller types. `available` is true iff the handle is
    syntactically legal AND not currently held by another shop; `reason` is set when false, using
    the same detail slugs the PATCH endpoint returns (handle-syntax / handle-reserved /
    handle-length / handle-taken) so the frontend has one error-message map for both paths."""
    handle: str
    available: bool
    reason: str | None = None


class ListingCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    # Optional free-text description (paragraphs preserved). Capped at the API boundary; the
    # service trims surrounding whitespace and stores None when empty.
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX_LEN)
    price_cents: int = Field(ge=0)  # integer money — never float (S9)
    currency: str = Field(default="KES", min_length=3, max_length=3)
    media_urls: list[str] = Field(default_factory=list)
    intent_weight: float = Field(default=1.0, ge=0.0, le=1.0)
    # Default 1, not 0: the buyer feed hides out-of-stock listings (stock_qty <= 0), so a create that
    # OMITS stock would otherwise publish a listing invisible to buyers — a "I published but nobody
    # can see it" footgun on the direct API (the seller console's form always sends an explicit
    # stock). A seller who genuinely wants a 0-stock draft must now opt in explicitly. Posts
    # (post_kind='post') have stock forced to 0 server-side regardless, so this default never
    # publishes a stocked "product" by mistake for them.
    stock_qty: int = Field(default=1, ge=0)
    low_stock_threshold: int = Field(default=0, ge=0)
    pricing_mode: PricingMode = "fixed"  # "fixed" | "bargain" — how orders on this listing price
    # §8 social feed: the seller declares this post as a dedicated short video (the reel-style
    # post the feed's Videos toggle filters to). Default false ⇒ an ordinary listing post. The
    # 250 MB short-video media size cap is enforced on the seller upload path (FE-2), not here.
    is_short_video: bool = False
    # §8 timeline: 'product' (the default, sellable) or 'post'. When 'post', the service forces
    # price/stock to 0 server-side — a post can't be ordered. The seller console only ever creates
    # products through this schema; posts use PostCreate via POST /posts.
    post_kind: PostKind = "product"
    # A listing may carry its own stitch key; if omitted it inherits the shop's at create time.
    property_uuid: str | None = Field(default=None, max_length=64)


class PostCreate(BaseModel):
    """Publish a plain social POST to the caller's timeline (§8). Text is required; media optional.
    No price/stock/pricing — a post carries no commerce. ``author_name`` is the display-name
    snapshot from the token (names the auto-provisioned personal shop on first post); ``lat``/``lng``
    anchor the post in the proximity feed (the client sends the buyer's current location, same as
    the feed query)."""
    body: str = Field(min_length=1, max_length=DESCRIPTION_MAX_LEN)
    media_urls: list[str] = Field(default_factory=list)
    is_short_video: bool = False
    lat: float = Field(ge=-90.0, le=90.0)
    lng: float = Field(ge=-180.0, le=180.0)
    author_name: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _body_not_blank(self) -> "PostCreate":
        # min_length=1 lets whitespace-only through; a post with no real text is meaningless.
        if not self.body.strip():
            raise ValueError("post body must not be empty")
        return self


class ListingUpdate(BaseModel):
    """PATCH a listing's seller-editable fields. Every field is OPTIONAL — only the ones actually
    supplied are changed (a true partial update; the service reads ``model_fields_set`` so passing
    ``description: null`` explicitly clears it, while omitting it leaves it untouched). At least one
    editable field must be present (an empty patch is a 422 — nothing to do).

    Stock is NOT edited here — it has its own POS endpoint (``PATCH …/stock``) with absolute/delta
    semantics. Identity, ownership, location and post-kind are immutable through this path (S6):
    a post can't be turned into a priced product, and price/pricing edits on a POST are ignored by
    the service (a post carries no commerce)."""
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX_LEN)
    price_cents: int | None = Field(default=None, ge=0)  # integer money — never float (S9)
    media_urls: list[str] | None = None
    intent_weight: float | None = Field(default=None, ge=0.0, le=1.0)
    low_stock_threshold: int | None = Field(default=None, ge=0)
    pricing_mode: PricingMode | None = None
    is_short_video: bool | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "ListingUpdate":
        # An empty PATCH is a client error, not a silent no-op — surface it as 422.
        if not self.model_fields_set:
            raise ValueError("provide at least one field to update")
        return self


class StockAdjust(BaseModel):
    """POS stock change. Provide EXACTLY ONE of:
      * ``stock_qty`` — set the absolute on-hand count (a stock-take), or
      * ``delta``     — apply a relative change (``-1`` per unit sold; result clamps at 0).
    """
    stock_qty: int | None = Field(default=None, ge=0)
    delta: int | None = Field(default=None)

    @model_validator(mode="after")
    def _exactly_one(self) -> "StockAdjust":
        provided = [v is not None for v in (self.stock_qty, self.delta)]
        if sum(provided) != 1:
            raise ValueError("provide exactly one of 'stock_qty' (absolute) or 'delta' (relative)")
        return self


class PromoteRequest(BaseModel):
    """Open a "selling now" window on a listing (§8). ``mode`` chooses expiry behaviour
    (evergreen vs story); ``duration_seconds`` is the window length. The duration is bounded at
    the API edge by a generous Field range, then re-checked against the configured min/max in the
    service (the service is the authority — config can tighten the window without a schema change,
    and the service guard also covers the standalone callers)."""
    mode: PromoMode
    duration_seconds: int = Field(ge=1, le=604_800)  # ≤ 7 days; service applies the real bounds


class FlashSaleRequest(BaseModel):
    """Launch a §8 flash sale on a listing. ``flash_price_cents`` is the crazy price the buyer pays
    while the window is open (a temporary override — the listing's normal price is untouched);
    ``duration_seconds`` is the window length. The 1-hour hard cap + the min are re-checked in the
    service (the authority) so config can tighten them without a schema change, and the service also
    rejects a non-discount / a bargain listing."""
    flash_price_cents: int = Field(gt=0)                # positive integer money (S9)
    duration_seconds: int = Field(ge=1, le=3600)        # ≤ 1 h; service applies the real bounds


# ----------------------------- responses -----------------------------

class ShopOut(BaseModel):
    id: str
    seller_id: str
    name: str
    # Seller-published business card (both None when unset). Surfaced on the owner views + the
    # public profile hovercard.
    description: str | None = None
    contact: str | None = None
    # Shop logo (avatar) + wide banner — media URLs, None when unset. The avatar is the logo shown
    # on cards/promotions; the banner is the profile backdrop.
    avatar_url: str | None = None
    banner_url: str | None = None
    # Trade category slug (§8) — None when the shop is un-categorised. The frontend maps it to a color.
    category: str | None = None
    # Shareable URL slug (§8 storefront: /shop/<handle>) — None when un-claimed. Once set it is
    # PERMANENT (one-shot policy: a rename would break every previously-shared link). The frontend
    # falls back to /shop/<sellerId> for un-handled shops so every shop has a shareable URL from
    # day one. Case-insensitive-unique in the DB (functional index on lower(handle)).
    handle: str | None = None
    property_uuid: str | None = None
    lat: float
    lng: float
    created_at: datetime


class ListingOut(BaseModel):
    id: str
    shop_id: str
    seller_id: str
    property_uuid: str | None = None
    title: str
    description: str | None = None
    price_cents: int
    currency: str
    media_urls: list[str] = Field(default_factory=list)
    intent_weight: float
    is_active: bool
    stock_qty: int
    low_stock_threshold: int
    pricing_mode: PricingMode
    # §8 social feed post kind — true ⇒ dedicated short-video post (the Videos toggle).
    is_short_video: bool = False
    # §8 timeline: 'product' (sellable) or 'post' (plain social content). The client suppresses
    # price/POS chrome for a post.
    post_kind: PostKind = "product"
    # Derived POS flags — computed once at serialization, not stored.
    is_out_of_stock: bool
    is_low_stock: bool
    # §8 ephemerality — the seller's own view of a "selling now" window. ``promo_mode`` is None
    # when un-promoted; the timestamps describe the window. ``is_promoted`` is the live state
    # (window open at serialization time) — the same single source of truth the feed uses
    # (services.ranking.promo_boost), so the owner sees exactly what buyers do.
    promo_mode: PromoMode | None = None
    promo_started_at: datetime | None = None
    promo_expires_at: datetime | None = None
    is_promoted: bool = False
    # §8 flash sale — the seller's own view of a live "crazy offer" window. All None when no flash
    # sale is set; ``is_flash_active`` is the live state (window open at serialization time), the
    # same single source of truth (services.flash_sales.active_flash_price) the buyer read + the
    # order path use, so the owner sees exactly what buyers pay. flash_reference_cents is the
    # comparable-shop average captured at launch (for the "was X / now Y" display).
    flash_price_cents: int | None = None
    flash_started_at: datetime | None = None
    flash_expires_at: datetime | None = None
    flash_reference_cents: int | None = None
    is_flash_active: bool = False
    created_at: datetime


class StorefrontShop(BaseModel):
    """A shop plus all its listings (in- AND out-of-stock — the seller sees everything)."""
    shop: ShopOut
    listings: list[ListingOut]


class StorefrontOut(BaseModel):
    seller_id: str
    display_name: str
    # The seller's own proof-of-purchase rating (increment 6 §8). ``rating`` is None when the
    # seller has no reviews yet (unrated, distinct from a low score); ``review_count`` is the
    # number of settled-order reviews behind it.
    rating: float | None = None
    review_count: int = 0
    shops: list[StorefrontShop]


class BulkStockIn(BaseModel):
    """Body of POST /sellers/me/stock/bulk-csv (§8 Chunk E3). The seller uploads a CSV as a
    plain string in `csv` (not a multipart file — one endpoint, one contract; the FE can send
    fetch text easily and there's no benefit to multipart for a body this small)."""
    csv: str = Field(min_length=1, max_length=512 * 1024)


class BulkStockOut(BaseModel):
    """Summary of a completed bulk stock upload. `updated_count == len(updated_ids)`; the ids
    are echoed so the FE can invalidate their per-listing caches surgically. `skipped_count`
    counts submitted listing_ids the caller did not own — never their ids (privacy)."""
    updated_count: int
    skipped_count: int
    updated_ids: list[str]


class LowStockGroup(BaseModel):
    """One shop's slice of the low-stock list. A seller with several shops gets one group per
    shop so the card can render a header per shop instead of one undifferentiated list.

    ``shop_name`` is display-only and always the caller's OWN shop (the query is scoped to the
    caller's seller row), so there is no cross-tenant exposure here.
    """
    shop_id: str
    shop_name: str
    items: list[ListingOut]


class LowStockOut(BaseModel):
    """Response of GET /sellers/me/low-stock — §8 Chunk E2. Lists the caller's active product
    listings whose ``stock_qty <= floor``, grouped by shop, most-urgent-first within each group.

    ``floor`` echoes the applied threshold so the UI can say "listings at or below 5". The rule
    is absolute — ``stock_qty <= floor`` and nothing else. A listing's own
    ``low_stock_threshold`` drives its ``is_low_stock`` badge but does NOT filter this list;
    letting it do so meant raising the threshold could never surface a listing that had its own
    threshold set, which read as "the filter is broken".

    ``groups`` is the ONLY carrier of listings. An earlier revision also returned a flattened
    ``items`` mirror of the same rows; it was dropped because it doubled a 30s-polled payload
    (a ListingOut is ~30 fields) and gave the response two sources of truth that could drift.
    The card's "(N)" counter sums the group lengths instead.
    """
    floor: int
    groups: list[LowStockGroup]


# ----------------------------- PUBLIC storefront (any buyer's view of any seller) -----------------------------
#
# Deliberately leaner than the owner views above: a buyer must NOT see another seller's POS
# internals (S6). Omitted vs ListingOut: stock_qty, low_stock_threshold, is_low_stock,
# is_out_of_stock, intent_weight, is_active — these are seller-private inventory/ranking
# signals. Kept: what a buyer needs to decide + the property_uuid for the InSAR Confirmed-badge
# stitch (§3). Only active, in-stock listings are ever included (services.catalog).

class PublicListingOut(BaseModel):
    id: str
    shop_id: str
    seller_id: str
    property_uuid: str | None = None
    title: str
    # Free-text body — a post's actual content lives here; a product's optional description too.
    description: str | None = None
    price_cents: int
    currency: str
    media_urls: list[str] = Field(default_factory=list)
    pricing_mode: PricingMode
    # §8 timeline kind so the public storefront can render a post vs a product correctly.
    post_kind: PostKind = "product"
    created_at: datetime


class PublicStorefrontShop(BaseModel):
    shop: ShopOut
    listings: list[PublicListingOut]


class PublicStorefrontOut(BaseModel):
    seller_id: str
    display_name: str
    rating: float | None = None
    review_count: int = 0
    shops: list[PublicStorefrontShop]


# ----------------------------- shop profile hovercard + follow (§8) -----------------------------
#
# The hovercard a buyer sees over a post's shop avatar: the shop's published business card
# (name/description/contact), its follower count + this viewer's follow state, the seller's
# proof-of-purchase rating, and the seller_id so the card's "Profile" button can deep-link to the
# public storefront. Only seller-published fields + opaque ids — no PII (S6).

class ShopProfileOut(BaseModel):
    shop_id: str
    seller_id: str
    name: str
    # Shop profile picture / logo (§8) — a media URL (absolute or /uploads/... relative) resolved
    # client-side; None ⇒ the hovercard shows the initials fallback.
    avatar_url: str | None = None
    # Wide banner / cover image for the shop profile — None ⇒ a plain header.
    banner_url: str | None = None
    description: str | None = None
    contact: str | None = None
    # Trade category slug (§8) — None when un-categorised; the frontend maps it to a color.
    category: str | None = None
    property_uuid: str | None = None
    # §8 "Notify": how many users follow this shop + whether THIS viewer does (drives Follow/
    # Following). Display-only social proof — never a ranking signal.
    follower_count: int = 0
    following: bool = False
    # The owning seller's proof-of-purchase rating (None ⇒ unrated), so the card matches the
    # storefront header without a second request.
    rating: float | None = None
    review_count: int = 0


class FollowToggleOut(BaseModel):
    """The new follow state after a toggle — lets the client flip Follow/Following + the count
    without a refetch. Mirrors SaveToggleOut."""
    shop_id: str
    following: bool
    follower_count: int


# ----------------------------- shops-by-property batch read (§8.1a — shops on the InSAR map) -------
#
# The weespas map aggregator asks "which of these building footprints (property_uuids) are shops?"
# and gets back the display meta it needs to pin them. Deliberately LEAN — and it carries NO
# lat/lng (S6): the footprint is already public on the InSAR map, so a shop's raw coordinates never
# need to leave commerce. Only seller-published, non-PII fields (name/category) ride along — no
# avatar: a glyph pin / text-only tooltip never renders a logo, so shipping one would be dead data.
#
# The request batch is bounded (mirrors weespas' _CONFIRMED_BATCH_MAX) so a caller can't force an
# unbounded IN (anti-O(n), S8). property_uuid is NOT unique — two shops may sit on one footprint —
# so the response is a FLAT LIST of entries (one per matching shop), never a dict keyed by uuid,
# which would silently drop the second shop on a shared building.

SHOPS_BY_PROPERTY_BATCH_MAX = 200


class ShopsByPropertyRequest(BaseModel):
    property_uuids: list[str] = Field(default_factory=list, max_length=SHOPS_BY_PROPERTY_BATCH_MAX)


class ShopByPropertyOut(BaseModel):
    property_uuid: str
    shop_id: str
    name: str
    # Trade category slug (§8) — None when un-categorised; the map maps it to a pin color.
    category: str | None = None


class ShopsByPropertyResponse(BaseModel):
    shops: list[ShopByPropertyOut] = Field(default_factory=list)


# ----------------------------- shop → owning-seller lookup (§8.1b — pair-radiate) -----------------
#
# The weespas contact uplink (POST /insar/contact) knows the shop a buyer just opened and needs the
# OWNING seller's per-user channel key to publish the anonymized "a viewer is looking" pulse over the
# SSE bus. It asks commerce for exactly one field: the seller's ``user_uuid`` (== the seller's
# weespas identity / token sub — already synchronized, NOT new PII, S6). No shop meta, no buyer data,
# no coordinates cross here. A shop_id that matches nothing yields ``seller_uuid: null`` (the uplink
# then simply skips the publish — no error, the buyer still glows locally).

class ShopSellerOut(BaseModel):
    shop_id: str
    seller_uuid: str | None = None


def to_shop_profile_out(
    shop, *, follower_count: int, following: bool,
    rating: float | None, review_count: int,
) -> ShopProfileOut:
    return ShopProfileOut(
        shop_id=str(shop.id),
        seller_id=str(shop.seller_id),
        name=shop.name,
        avatar_url=shop.avatar_url,
        banner_url=shop.banner_url,
        description=shop.description,
        contact=shop.contact,
        category=shop.category,
        property_uuid=shop.property_uuid,
        follower_count=follower_count,
        following=following,
        rating=round(rating, 2) if rating is not None else None,
        review_count=review_count,
    )


# ----------------------------- mappers (ORM → response) -----------------------------

def _decode_media(raw: str | None) -> list[str]:
    """media_urls is stored as a JSON-array string (or None). Decode defensively to a list —
    same tolerance as schemas.feed.to_feed_item (a malformed value yields [], never raises)."""
    if not raw:
        return []
    try:
        decoded = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return [str(u) for u in decoded] if isinstance(decoded, list) else []


def to_shop_out(shop) -> ShopOut:
    return ShopOut(
        id=str(shop.id),
        seller_id=str(shop.seller_id),
        name=shop.name,
        description=shop.description,
        contact=shop.contact,
        avatar_url=shop.avatar_url,
        banner_url=shop.banner_url,
        category=shop.category,
        handle=shop.handle,
        property_uuid=shop.property_uuid,
        lat=shop.lat,
        lng=shop.lng,
        created_at=shop.created_at,
    )


def to_listing_out(listing, now: datetime | None = None) -> ListingOut:
    """Map a Listing ORM row → ListingOut, computing the derived POS + promo flags. ``is_low_stock``
    is only meaningful for an in-stock listing whose count has fallen to/under a configured
    threshold (threshold 0 disables it); a 0-stock listing is reported as out-of-stock, not low.
    ``is_promoted`` reuses services.ranking.promo_boost so the owner view and the buyer feed agree
    on the live window (one source of truth, no duplicated expiry logic)."""
    out_of_stock = listing.stock_qty <= 0
    low_stock = (
        not out_of_stock
        and listing.low_stock_threshold > 0
        and listing.stock_qty <= listing.low_stock_threshold
    )
    now = now or datetime.now(timezone.utc)
    is_promoted = ranking.promo_boost(
        listing.promo_started_at, listing.promo_expires_at, now
    ) > 0.0
    is_flash_active = flash_sales.active_flash_price(listing, now) is not None
    return ListingOut(
        id=str(listing.id),
        shop_id=str(listing.shop_id),
        seller_id=str(listing.seller_id),
        property_uuid=listing.property_uuid,
        title=listing.title,
        description=listing.description,
        price_cents=listing.price_cents,
        currency=listing.currency,
        media_urls=_decode_media(listing.media_urls),
        intent_weight=listing.intent_weight,
        is_active=listing.is_active,
        stock_qty=listing.stock_qty,
        low_stock_threshold=listing.low_stock_threshold,
        pricing_mode=listing.pricing_mode,
        is_short_video=bool(listing.is_short_video),
        post_kind=listing.post_kind,
        is_out_of_stock=out_of_stock,
        is_low_stock=low_stock,
        promo_mode=listing.promo_mode,
        promo_started_at=listing.promo_started_at,
        promo_expires_at=listing.promo_expires_at,
        is_promoted=is_promoted,
        flash_price_cents=listing.flash_price_cents,
        flash_started_at=listing.flash_started_at,
        flash_expires_at=listing.flash_expires_at,
        flash_reference_cents=listing.flash_reference_cents,
        is_flash_active=is_flash_active,
        created_at=listing.created_at,
    )


def to_public_listing_out(listing) -> PublicListingOut:
    """Map a Listing → the lean PUBLIC view — no POS-internal fields (stock/threshold/intent/
    is_active). Caller guarantees the listing is already active + in stock."""
    return PublicListingOut(
        id=str(listing.id),
        shop_id=str(listing.shop_id),
        seller_id=str(listing.seller_id),
        property_uuid=listing.property_uuid,
        title=listing.title,
        description=listing.description,
        price_cents=listing.price_cents,
        currency=listing.currency,
        media_urls=_decode_media(listing.media_urls),
        pricing_mode=listing.pricing_mode,
        post_kind=listing.post_kind,
        created_at=listing.created_at,
    )


def to_public_storefront_out(seller, visible, rating: float | None = None,
                             review_count: int = 0) -> PublicStorefrontOut:
    """Map a Seller → the public storefront. ``visible`` is a callable ``shop -> [Listing]``
    that returns only the buyer-visible (active, in-stock) listings for a shop, so the filtering
    rule lives in one place (services.catalog.public_visible_listings). A shop with no visible
    listings is omitted entirely — a buyer sees only shops that currently have something to sell."""
    shops = []
    for shop in seller.shops:
        listings = visible(shop)
        if not listings:
            continue
        shops.append(PublicStorefrontShop(
            shop=to_shop_out(shop),
            listings=[to_public_listing_out(li) for li in listings],
        ))
    return PublicStorefrontOut(
        seller_id=str(seller.id),
        display_name=seller.display_name,
        rating=round(rating, 2) if rating is not None else None,
        review_count=review_count,
        shops=shops,
    )


def to_storefront_out(seller, rating: float | None = None, review_count: int = 0) -> StorefrontOut:
    """Map a Seller (with eager/lazy shops→listings) → the full storefront view. Iterates only
    the caller's own catalog — bounded by their data, never a global scan. ``rating`` /
    ``review_count`` are the seller's aggregate (one O(1) AVG+COUNT supplied by the caller;
    default unrated)."""
    return StorefrontOut(
        seller_id=str(seller.id),
        display_name=seller.display_name,
        rating=round(rating, 2) if rating is not None else None,
        review_count=review_count,
        shops=[
            StorefrontShop(
                shop=to_shop_out(shop),
                listings=[to_listing_out(li) for li in shop.listings],
            )
            for shop in seller.shops
        ],
    )
