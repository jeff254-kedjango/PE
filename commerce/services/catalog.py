"""Seller write path — shop/listing creation, POS stock, and the seller's own storefront.

This is the only module that WRITES the catalog. It enforces the two security invariants the
router relies on:

  * **Identity from the token, never the client.** The Seller is resolved/created from the
    verified ``user_uuid`` (the token ``sub``) — a caller can never write under another seller's
    id by supplying one (there is no seller_id input anywhere).
  * **Ownership on every mutation.** A shop/listing is only reachable for write when its owning
    seller's ``user_uuid`` equals the caller's. A cross-owner target is reported as **not found**
    (the router raises 404, not 403) so the API never confirms the existence of another seller's
    rows (S6 — no information leak).

All money/counts are integers (S9); locations go through ``proximity.set_location`` so the
PostGIS geography and the lat/lng floats can never drift (reuse, not reinvent).
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session, selectinload

from PE.commerce.core.config import settings
from PE.commerce.models.listing import POST_KIND_POST, POST_KIND_PRODUCT, PROMO_MODES, Listing
from PE.commerce.models.seller import Seller, Shop
from PE.commerce.schemas import catalog as schemas
from PE.commerce.services import proximity


# ----------------------------- seller bootstrap -----------------------------

def get_or_create_seller(db: Session, user_uuid: str, display_name: str) -> Seller:
    """Return the caller's Seller row, creating it on first write. Identity is the verified
    token sub — there is no separate signup. An existing seller's display_name is left as-is
    (a later profile-edit endpoint owns renames); only a brand-new seller takes the supplied
    name. O(1): a single indexed lookup on user_uuid."""
    seller = db.query(Seller).filter(Seller.user_uuid == user_uuid).one_or_none()
    if seller is None:
        seller = Seller(user_uuid=user_uuid, display_name=display_name)
        db.add(seller)
        db.flush()  # assign PK for the FK below
    return seller


# ----------------------------- ownership-scoped fetch -----------------------------

def _owned_shop(db: Session, shop_id: str, user_uuid: str) -> Shop | None:
    """The shop, only if owned by user_uuid; else None (router → 404, no existence leak).
    Single indexed join on the caller's seller — O(log n)."""
    return (
        db.query(Shop)
        .join(Seller, Shop.seller_id == Seller.id)
        .filter(Shop.id == shop_id, Seller.user_uuid == user_uuid)
        .one_or_none()
    )


def _owned_listing(db: Session, listing_id: str, user_uuid: str) -> Listing | None:
    """The listing, only if owned by user_uuid; else None."""
    return (
        db.query(Listing)
        .join(Seller, Listing.seller_id == Seller.id)
        .filter(Listing.id == listing_id, Seller.user_uuid == user_uuid)
        .one_or_none()
    )


# ----------------------------- writes -----------------------------

def create_shop(db: Session, user_uuid: str, body: schemas.ShopCreate) -> Shop:
    seller = get_or_create_seller(db, user_uuid, body.display_name)
    # Trim the seller-published business card; store None for blank so the profile card omits it
    # (mirrors the listing-description discipline). The word cap is already enforced by the schema.
    description = (body.description or "").strip() or None
    contact = (body.contact or "").strip() or None
    # Media URLs (logo + banner) from the weespas upload pipeline; blank ⇒ None (no image).
    avatar_url = (body.avatar_url or "").strip() or None
    banner_url = (body.banner_url or "").strip() or None
    shop = Shop(
        seller_id=seller.id, name=body.name,
        description=description, contact=contact,
        avatar_url=avatar_url, banner_url=banner_url,
        # category is allow-list-validated by the schema (unknown ⇒ 422 before we get here);
        # None stays None (un-categorised).
        category=body.category,
        property_uuid=body.property_uuid,
    )
    proximity.set_location(shop, body.lat, body.lng)
    db.add(shop)
    db.commit()
    db.refresh(shop)
    return shop


def create_listing(
    db: Session, user_uuid: str, shop_id: str, body: schemas.ListingCreate
) -> Listing | None:
    """Create a listing under the caller's shop. Returns None if the shop isn't owned by the
    caller (router → 404). The listing denormalizes seller id + location from the shop so the
    feed reads listings alone (the model's design)."""
    shop = _owned_shop(db, shop_id, user_uuid)
    if shop is None:
        return None
    # Trim surrounding whitespace; store None for an empty/blank description (the card omits it).
    # Internal blank lines are preserved — the seller's paragraphs survive to the feed.
    description = (body.description or "").strip() or None
    # A plain social POST carries no commerce: force price/stock to 0 and pricing to fixed on the
    # server (never trust the client to zero them) so a post can never be ordered or hold stock.
    is_post = body.post_kind == POST_KIND_POST
    listing = Listing(
        shop_id=shop.id,
        seller_id=shop.seller_id,
        # Listing's own stitch key, falling back to the shop's footprint.
        property_uuid=body.property_uuid or shop.property_uuid,
        title=body.title,
        description=description,
        price_cents=0 if is_post else body.price_cents,
        currency=body.currency,
        media_urls=json.dumps(body.media_urls) if body.media_urls else None,
        intent_weight=body.intent_weight,
        stock_qty=0 if is_post else body.stock_qty,
        low_stock_threshold=0 if is_post else body.low_stock_threshold,
        pricing_mode="fixed" if is_post else body.pricing_mode,
        is_short_video=body.is_short_video,
        post_kind=body.post_kind,
        is_active=True,
    )
    # Inherit the shop's position (buyers see "selling from that building").
    proximity.set_location(listing, shop.lat, shop.lng)
    db.add(listing)
    db.commit()
    db.refresh(listing)
    return listing


_PERSONAL_SHOP_NAME = "My timeline"
# A post's derived title — the feed card + avatar still key off `title`, so a post (whose real
# content lives in `description`) gets a short snippet as its title to keep one render path.
_POST_TITLE_MAX = 80


def get_or_create_personal_shop(
    db: Session, user_uuid: str, display_name: str, lat: float, lng: float
) -> Shop:
    """Return the caller's first shop, creating a minimal personal one at (lat,lng) if they have
    none. This is the home for plain POSTS: a post must hang off a Shop (Listing.shop_id is a
    NOT-NULL FK), but a user shouldn't need to "open a shop" to post socially. It does NOT weaken
    the product gate — products are still created under an explicit, user-named shop via the seller
    console; this auto-shop just anchors posts. O(1): one indexed lookup on the caller's seller +
    its shops, then an insert only on the first post."""
    seller = get_or_create_seller(db, user_uuid, display_name)
    existing = (
        db.query(Shop)
        .filter(Shop.seller_id == seller.id)
        .order_by(Shop.created_at.asc())
        .first()
    )
    if existing is not None:
        return existing
    shop = Shop(seller_id=seller.id, name=_PERSONAL_SHOP_NAME, property_uuid=None)
    proximity.set_location(shop, lat, lng)
    db.add(shop)
    db.flush()  # assign PK for the listing FK
    return shop


def create_post(
    db: Session, user_uuid: str, body: schemas.PostCreate, lat: float, lng: float
) -> Listing:
    """Publish a plain social POST (text + optional media, no price/stock) to the caller's timeline.
    Resolves/creates the personal shop at the post's location, then writes a post-kind Listing so it
    flows through the same feed/comments/saves/likes as a product. The post's text lives in
    ``description`` (paragraphs preserved); ``title`` is a short derived snippet so the existing
    title-keyed card/avatar still render. Always succeeds for an authenticated caller (a post needs
    no pre-existing shop)."""
    display_name = (body.author_name or "").strip() or _PERSONAL_SHOP_NAME
    shop = get_or_create_personal_shop(db, user_uuid, display_name, lat, lng)
    text = body.body.strip()
    snippet = text.replace("\n", " ").strip()[:_POST_TITLE_MAX] or "Post"
    listing = Listing(
        shop_id=shop.id,
        seller_id=shop.seller_id,
        property_uuid=shop.property_uuid,
        title=snippet,
        description=text,
        price_cents=0,
        currency="KES",
        media_urls=json.dumps(body.media_urls) if body.media_urls else None,
        intent_weight=1.0,
        stock_qty=0,
        low_stock_threshold=0,
        pricing_mode="fixed",
        is_short_video=body.is_short_video,
        post_kind=POST_KIND_POST,
        is_active=True,
    )
    proximity.set_location(listing, shop.lat, shop.lng)
    db.add(listing)
    db.commit()
    db.refresh(listing)
    return listing


def adjust_stock(
    db: Session, user_uuid: str, listing_id: str, body: schemas.StockAdjust
) -> Listing | None:
    """POS stock change on the caller's listing. Returns None if not owned (router → 404).
    ``stock_qty`` sets an absolute count; ``delta`` applies a relative change clamped at 0
    (selling the last unit lands on exactly 0 — which hides it from the buyer feed)."""
    listing = _owned_listing(db, listing_id, user_uuid)
    if listing is None:
        return None
    if body.stock_qty is not None:
        listing.stock_qty = body.stock_qty
    else:
        # delta path — schema guarantees exactly one of the two is set.
        listing.stock_qty = max(0, listing.stock_qty + body.delta)
    db.commit()
    db.refresh(listing)
    return listing


# A post carries no commerce — these fields are meaningless on it and are silently ignored on an
# edit (never let a PATCH turn a social post into something orderable). See update_listing.
_PRODUCT_ONLY_EDIT_FIELDS = frozenset({"price_cents", "pricing_mode", "low_stock_threshold"})


def update_listing(
    db: Session, user_uuid: str, listing_id: str, body: schemas.ListingUpdate
) -> Listing | None:
    """Partial edit of the caller's listing. Returns None if not owned (router → 404). Only the
    fields the client actually supplied (``model_fields_set``) are written — an omitted field is
    left untouched, while an explicit null clears a nullable one (title can't be nulled — the schema
    forbids it). Commerce-only fields (price/pricing/threshold) are ignored on a POST so an edit can
    never make a social post orderable. Location, ownership, post_kind and stock are immutable here
    (stock has its own POS endpoint). O(1): one ownership lookup + an in-place update."""
    listing = _owned_listing(db, listing_id, user_uuid)
    if listing is None:
        return None

    fields = set(body.model_fields_set)
    is_post = listing.post_kind == POST_KIND_POST
    if is_post:
        fields -= _PRODUCT_ONLY_EDIT_FIELDS  # a post has no price/pricing/threshold to edit

    if "title" in fields:
        listing.title = body.title
    if "description" in fields:
        # Trim; an explicit blank clears it (the card omits an empty description). Internal blank
        # lines are preserved — the seller's paragraphs survive (mirrors create_listing).
        listing.description = (body.description or "").strip() or None
    if "price_cents" in fields:
        listing.price_cents = body.price_cents
    if "media_urls" in fields:
        # None-or-empty ⇒ NULL (no media), matching create_listing's storage shape.
        listing.media_urls = json.dumps(body.media_urls) if body.media_urls else None
    if "intent_weight" in fields:
        listing.intent_weight = body.intent_weight
    if "low_stock_threshold" in fields:
        listing.low_stock_threshold = body.low_stock_threshold
    if "pricing_mode" in fields:
        listing.pricing_mode = body.pricing_mode
    if "is_short_video" in fields:
        listing.is_short_video = body.is_short_video

    db.commit()
    db.refresh(listing)
    return listing


def soft_delete_listing(db: Session, user_uuid: str, listing_id: str) -> bool:
    """Soft-delete the caller's listing: flip ``is_active`` false so it leaves the buyer feed, the
    public storefront and the sponsored/trending lanes at once (every read path already filters on
    ``is_active``). Returns True on success, False if not owned (router → 404).

    Soft, never hard: a hard delete would orphan the immutable order/receipt/review history that
    references this listing (settlement §6/§7 relies on the row surviving so a past sale can't be
    rewritten). Idempotent — deleting an already-inactive listing is a clean no-op that still
    reports success (the end state the caller asked for holds)."""
    listing = _owned_listing(db, listing_id, user_uuid)
    if listing is None:
        return False
    if listing.is_active:
        listing.is_active = False
        db.commit()
    return True


# ----------------------------- promotion ("selling now", §8) -----------------------------

class PromotionError(ValueError):
    """Bad promotion input (unknown mode / out-of-bounds duration). Router → 422."""


def promote_listing(
    db: Session, user_uuid: str, listing_id: str, *, mode: str, duration_seconds: int,
    now: datetime | None = None,
) -> Listing | None:
    """Open (or replace) a "selling now" promotion window on the caller's listing. Returns the
    updated Listing, None if not owned (router → 404), or raises PromotionError on bad input
    (router → 422). The window is [now, now + duration]; ``mode`` decides expiry behaviour
    (evergreen fades, story disappears — see models.listing).

    Duration is server-bounded (anti-abuse): a window can't be a 0-length blip nor an
    indefinite ad. Setting a new promotion overwrites any existing one (re-promote = extend)."""
    if mode not in PROMO_MODES:
        raise PromotionError(f"mode must be one of {PROMO_MODES}")
    if not (settings.promo_min_duration_seconds <= duration_seconds <= settings.promo_max_duration_seconds):
        raise PromotionError(
            f"duration_seconds must be between {settings.promo_min_duration_seconds} and "
            f"{settings.promo_max_duration_seconds}"
        )
    listing = _owned_listing(db, listing_id, user_uuid)
    if listing is None:
        return None
    now = now or datetime.now(timezone.utc)
    listing.promo_mode = mode
    listing.promo_started_at = now
    listing.promo_expires_at = now + timedelta(seconds=duration_seconds)
    db.commit()
    db.refresh(listing)
    return listing


def clear_promotion(db: Session, user_uuid: str, listing_id: str) -> Listing | None:
    """Remove any promotion from the caller's listing (back to an ordinary always-on listing).
    Returns the updated Listing, or None if not owned (router → 404). Idempotent: clearing an
    un-promoted listing is a clean no-op."""
    listing = _owned_listing(db, listing_id, user_uuid)
    if listing is None:
        return None
    listing.promo_mode = None
    listing.promo_started_at = None
    listing.promo_expires_at = None
    db.commit()
    db.refresh(listing)
    return listing


# ----------------------------- storefront read (seller's own, unfiltered) -----------------------------

def get_my_storefront(db: Session, user_uuid: str) -> Seller | None:
    """The caller's Seller with shops + ALL listings (in- and out-of-stock — the seller sees
    everything, unlike the buyer feed). Returns None if the caller has never sold (no Seller
    row yet).

    Eager-loads shops→listings with ``selectinload`` so the whole storefront costs a fixed 3
    queries (seller, its shops, their listings) regardless of catalogue size — not the N+1 a
    lazy walk in to_storefront_out would otherwise trigger."""
    return (
        db.query(Seller)
        .options(selectinload(Seller.shops).selectinload(Shop.listings))
        .filter(Seller.user_uuid == user_uuid)
        .one_or_none()
    )


# ----------------------------- public storefront read (any seller, buyer-visible only) -----------------------------

def get_public_storefront(db: Session, seller_id: str) -> Seller | None:
    """A seller's PUBLIC storefront by seller id — what any buyer may see of another seller's
    profile. Returns None if the seller doesn't exist (router → 404).

    Differs from get_my_storefront in two security-relevant ways:
      * keyed by ``seller_id`` (a public identifier), not the caller's token sub — anyone may
        view any seller, but only their OWN /shops/mine exposes the internal view;
      * shows ONLY active, in-stock listings (the buyer-feed visibility rule) — an out-of-stock
        or deactivated listing is hidden, exactly as in the proximity feed. The buyer never sees
        the seller's POS internals (stock counts, thresholds, inactive items); the public schema
        also omits those fields (S6).

    Eager-loads shops→listings with ``selectinload`` so the whole storefront is a fixed 3
    queries regardless of catalogue size (no N+1); the in-stock filter is applied in Python over
    the already-bounded, seller-scoped rows (a seller's own catalogue, never a global scan)."""
    return (
        db.query(Seller)
        .options(selectinload(Seller.shops).selectinload(Shop.listings))
        .filter(Seller.id == seller_id)
        .one_or_none()
    )


class BulkStockError(ValueError):
    """CSV parse / row-shape error for POST /sellers/me/stock/bulk-csv (§8 Chunk E3).
    Router → 422 with the human-readable message so the seller sees exactly what to fix."""


@dataclass(frozen=True)
class BulkStockResult:
    """Summary of a completed bulk stock upload. All-or-nothing on VALIDATION (see
    ``bulk_update_stock``) — any malformed row raises BulkStockError before a single listing
    is mutated, so the caller sees either this summary or a 422 naming the offending line.

    Ownership is NOT a validation failure: listing_ids the caller doesn't own are skipped, not
    raised, because raising would confirm "this id exists under another seller". They're
    reported only as ``skipped_count`` — an anonymised tally, never the ids themselves.
    So ``updated_count`` equals the CSV row count minus ``skipped_count``, not the row count."""
    updated_count: int
    skipped_count: int
    updated_ids: list[str]


# Hard caps on a single bulk call — enough headroom for a real POS shop-take (few hundred SKUs)
# without letting one call become a DoS vector against the DB. A 4000-row CSV would already be
# ~40kb and generally means the seller wants a different tool.
_BULK_MAX_ROWS = 1000
# Hardware bound on the CSV body size we're willing to parse — 512kb is generous headroom above
# the 40-byte-per-row worst case at 1000 rows. Prevents a rogue upload from ballooning memory.
_BULK_MAX_BYTES = 512 * 1024


def bulk_update_stock(db: Session, user_uuid: str, csv_text: str) -> BulkStockResult:
    """Parse a `listing_id,stock_qty` CSV and apply the new stock counts to the caller's
    listings in ONE transaction. All-or-nothing on validation: any parse error, out-of-range
    stock, or duplicate listing_id raises BulkStockError BEFORE any listing is mutated.

    CSV shape:
      * Optional header row (first row skipped when it looks like `listing_id,stock_qty` or a
        header-shaped variant). If the first row parses as data, we treat it as data.
      * Two columns: listing_id (string), stock_qty (integer ≥ 0).
      * Extra columns REJECTED with BulkStockError — a seller's spreadsheet with a stray
        "notes" column would silently drop those cells, which is worse than a clear error.

    Ownership discipline (matches every other write path):
      * Listings NOT owned by the caller are SKIPPED, not raised — we never confirm cross-owner
        existence (S6). skipped_count is returned so the seller can see the discrepancy.
      * Duplicate listing_id in the same CSV IS a hard error (BulkStockError) — the seller almost
        certainly made a copy-paste mistake and would be surprised which value "wins".
    """
    if not csv_text:
        raise BulkStockError("CSV body must not be empty")
    if len(csv_text) > _BULK_MAX_BYTES:
        raise BulkStockError(f"CSV too large (max {_BULK_MAX_BYTES} bytes)")

    reader = csv.reader(io.StringIO(csv_text))
    rows: list[tuple[int, str, int]] = []   # (line_no, listing_id, stock_qty)
    seen_ids: set[str] = set()
    for line_no, raw in enumerate(reader, start=1):
        if not raw:
            continue                          # blank line — silently skip
        # Skip a plausible header (line 1 only, and only if the second cell isn't numeric).
        if line_no == 1 and len(raw) == 2 and not raw[1].strip().isdigit():
            continue
        if len(raw) != 2:
            raise BulkStockError(
                f"line {line_no}: expected 2 columns (listing_id,stock_qty), got {len(raw)}"
            )
        listing_id = raw[0].strip()
        qty_str = raw[1].strip()
        if not listing_id:
            raise BulkStockError(f"line {line_no}: listing_id may not be empty")
        try:
            qty = int(qty_str)
        except ValueError:
            raise BulkStockError(f"line {line_no}: stock_qty must be an integer, got {qty_str!r}")
        if qty < 0:
            raise BulkStockError(f"line {line_no}: stock_qty must be >= 0, got {qty}")
        if listing_id in seen_ids:
            raise BulkStockError(f"line {line_no}: duplicate listing_id {listing_id!r}")
        seen_ids.add(listing_id)
        rows.append((line_no, listing_id, qty))
        if len(rows) > _BULK_MAX_ROWS:
            raise BulkStockError(f"CSV has more than {_BULK_MAX_ROWS} rows")

    if not rows:
        raise BulkStockError("CSV had no data rows")

    # Resolve caller ownership in ONE indexed query. Anything the caller submitted that's not
    # in this set gets silently skipped (no cross-owner existence leak).
    listing_ids = [lid for _, lid, _ in rows]
    owned = {
        li.id: li
        for li in (
            db.query(Listing)
            .join(Seller, Listing.seller_id == Seller.id)
            .filter(Seller.user_uuid == user_uuid, Listing.id.in_(listing_ids))
            .all()
        )
    }

    updated_ids: list[str] = []
    skipped = 0
    for _line, lid, qty in rows:
        li = owned.get(lid)
        if li is None:
            skipped += 1
            continue
        li.stock_qty = qty
        updated_ids.append(lid)
    db.commit()

    return BulkStockResult(
        updated_count=len(updated_ids),
        skipped_count=skipped,
        updated_ids=updated_ids,
    )


def low_stock_listings(
    db: Session, user_uuid: str, *, floor: int = 5, limit: int = 50,
) -> list[Listing]:
    """Return the caller's active PRODUCT listings at or below ``floor`` — sorted ascending by
    stock (most-urgent first), then grouped per-shop by the caller.

    The rule is deliberately absolute: **a listing is low when ``stock_qty <= floor``**. Nothing
    else participates. Set the card's Threshold to 5 and you see every product with 5 or fewer
    in stock — no exceptions, no rows above the number, no rows mysteriously missing.

    A previous revision instead treated a per-listing ``low_stock_threshold > 0`` as an
    EXCLUSIVE replacement for the floor, so a listing that had its own threshold was matched
    *only* against that threshold. Raising the card's Threshold could then never surface it
    (stock 8 with own-threshold 2 stayed hidden at floor=10), which is precisely the bug the
    seller hit. ``low_stock_threshold`` still drives the per-listing ``is_low_stock`` badge in
    ``schemas.catalog.to_listing_out`` — it is not dead, it simply no longer filters THIS list.

    Excludes:
      * POSTS — they have no inventory (post_kind='post', stock_qty stays 0), and would
        otherwise dominate the list at stock_qty=0.
      * Inactive listings — nothing to alert about.

    O(indexed range on (seller_id, stock_qty)) — the seller_id-indexed range is bounded by
    ``limit`` (default 50); a seller with hundreds of low-stock items sees the top 50 (most
    urgent). Pagination isn't needed today; the card is a summary, not a catalog.
    """
    if floor < 0:
        floor = 0
    limit = max(1, min(limit, 200))

    # Ownership scope: pull the seller row from the token, then filter listings by it. If the
    # caller has no seller row (never opened a shop) return [] cleanly.
    seller = db.query(Seller).filter(Seller.user_uuid == user_uuid).one_or_none()
    if seller is None:
        return []

    # One indexed range scan, no per-row Python filtering. Ordered by shop first so the caller
    # can group without a second pass or a sort, then most-urgent-first WITHIN each shop.
    return (
        db.query(Listing)
        .filter(
            Listing.seller_id == seller.id,
            Listing.is_active == True,   # noqa: E712 — SQLAlchemy comparison
            Listing.post_kind == POST_KIND_PRODUCT,
            Listing.stock_qty <= floor,
        )
        .order_by(Listing.shop_id.asc(), Listing.stock_qty.asc(), Listing.id.asc())
        .limit(limit)
        .all()
    )


def public_visible_listings(shop: Shop) -> list[Listing]:
    """The buyer-visible listings of a shop: active AND (a post OR in stock) — the same visibility
    rule the proximity feed enforces (services.proximity._buyable_or_post). An out-of-stock product
    or inactive item is hidden; a plain post (no inventory) stays visible while active."""
    return [
        li for li in shop.listings
        if li.is_active and (li.post_kind == POST_KIND_POST or li.stock_qty > 0)
    ]


def shop_meta(db: Session, shop_ids: list[str]) -> dict[str, tuple[str, str | None, str | None]]:
    """Batch shop display-meta for a whole feed page in ONE query (no N+1): ``shop_id → (name,
    avatar_url, category)``. Lets the feed card show the SHOP's name + profile picture (and a
    category tint) instead of deriving an initial from the listing title. Display-only — never
    touches ranking. Shops absent from the dict (shouldn't happen for a live listing) are defaulted
    by the caller."""
    if not shop_ids:
        return {}
    rows = (
        db.query(Shop.id, Shop.name, Shop.avatar_url, Shop.category)
        .filter(Shop.id.in_(shop_ids))
        .all()
    )
    return {sid: (name, avatar, category) for sid, name, avatar, category in rows}
