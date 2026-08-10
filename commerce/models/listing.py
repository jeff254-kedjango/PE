"""Listing — a product post.

In the social-commerce model a sale IS a post (architecture doc §8): media + price +
inline actions, surfaced by a proximity feed rather than a follower graph. A Listing
denormalizes ``seller_id`` and its own location from the parent Shop so the feed's radius
query and ranking read one table (no per-row join). Location is dual-path (geography +
lat/lng) for the same reason as Shop.

Money is an integer count of the minor unit (cents) — never a float (S9). ``media_urls``
holds a JSON array of EXISTING /uploads URLs (the weespas media pipeline is reused, not
rebuilt). ``intent_weight`` and ``created_at`` are the non-spatial ranking signals
(seller intent + freshness).
"""
from geoalchemy2 import Geography
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid

from PE.commerce.core.database import Base


# Promotion modes (§8 ephemerality). On expiry: EVERGREEN fades the boost but the listing stays
# a normal always-on item; STORY removes the post from the feed entirely (the listing + stock are
# never deleted — only the *post* disappears). The legal set is enforced at the API boundary.
PROMO_EVERGREEN = "evergreen"
PROMO_STORY = "story"
PROMO_MODES = (PROMO_EVERGREEN, PROMO_STORY)

# Post kind (§8 social timeline). A listing is either a PRODUCT (the original sellable item —
# price, stock, POS, orders) or a plain social POST (text + media, no commerce: price 0, stock 0).
# A post is still a Listing row so it reuses the proximity feed, comments, saves and likes
# wholesale — the ONLY behavioural difference is feed visibility: a product is hidden when out of
# stock, whereas a post (which has no inventory) is always visible while active. The legal set is
# enforced at the API boundary; server_default 'product' backfills every pre-existing row.
POST_KIND_PRODUCT = "product"
POST_KIND_POST = "post"
POST_KINDS = (POST_KIND_PRODUCT, POST_KIND_POST)


class Listing(Base):
    __tablename__ = "listings"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    shop_id = Column(String, ForeignKey("shops.id"), nullable=False, index=True)
    # Denormalized from Shop so the feed reads listings alone.
    seller_id = Column(String, ForeignKey("sellers.id"), nullable=False, index=True)
    property_uuid = Column(String, nullable=True, index=True)  # InSAR Confirmed-badge stitch

    title = Column(String(200), nullable=False)
    # Free-text product description (§8 social feed display). Newlines are preserved — the seller
    # writes paragraphs and the feed renders them. Bounded (service trims + caps at DESCRIPTION_MAX_LEN)
    # so one listing can't dump unbounded text into every feed payload. Nullable: pre-existing rows
    # have none and the card simply omits it.
    description = Column(Text, nullable=True)
    price_cents = Column(Integer, nullable=False)  # integer money — never float (S9)
    currency = Column(String(3), nullable=False, default="KES")
    media_urls = Column(Text, nullable=True)  # JSON array of existing /uploads URLs
    intent_weight = Column(Float, nullable=False, default=1.0)  # ranking signal: "selling now"
    is_active = Column(Boolean, nullable=False, default=True, index=True)

    # POS / inventory (increment 2). Units on hand; 0 ⇒ out of stock, which HIDES the listing
    # from the buyer proximity feed (services.proximity.search_listings) while the seller still
    # sees it in their own storefront. low_stock_threshold is the reorder signal: the seller
    # view flags "low" when stock_qty <= low_stock_threshold (0 disables alerting). Both are
    # integer counts, server-default 0 so the additive column migration backfills existing rows.
    stock_qty = Column(Integer, nullable=False, default=0, server_default="0")
    low_stock_threshold = Column(Integer, nullable=False, default=0, server_default="0")

    # Settlement (increment 4): how an order on this listing prices. "fixed" ⇒ order locks at
    # price_cents immediately; "bargain" ⇒ order opens a negotiation (§7 state machine) around
    # price_cents as the reference. server_default keeps existing rows valid through the
    # additive migration. The set of legal values is enforced at the API boundary (schema).
    pricing_mode = Column(String(16), nullable=False, default="fixed", server_default="fixed")

    # Ephemeral "selling now / fresh stock today" promotion (§8 Stories/ephemerality). A time-
    # boxed window that boosts the listing in the proximity feed and decays as it runs down — for
    # perishable / SME inventory. ALL THREE are NULL for an ordinary, always-on listing (the
    # common case), so a listing is "promoted" iff promo_expires_at is set and still in the
    # future. Evaluated PURELY from these columns vs now() — no sweep/materialization (a boost
    # that is a function of time needs no write to decay; see ranking.promo_boost).
    #   * promo_mode: "evergreen" (on expiry the boost just fades, the listing stays visible as a
    #     normal item) or "story" (on expiry the post DISAPPEARS from the feed, like an IG/TikTok
    #     story — but the listing + stock are untouched, never deleted). NULL ⇒ not promoted.
    #   * promo_started_at / promo_expires_at: the window; the boost is full at start, 0 at expiry.
    promo_mode = Column(String(16), nullable=True)
    promo_started_at = Column(DateTime(timezone=True), nullable=True)
    promo_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)

    # Flash Sale (§8) — a nationwide, one-hour-max "crazy offer" window ("Bread for 10 KES"). Like
    # the promotion window above, ALL FIVE columns are NULL for an ordinary listing and the whole
    # thing is evaluated PURELY from these columns vs now() — no sweep, no materialization. The flash
    # price is a TEMPORARY OVERRIDE: it never touches price_cents, so the normal price reverts by
    # itself the instant the window closes (see services.flash_sales.active_flash_price). The
    # "craziness" score is a MARGIN vs comparable shops, computed ONCE at launch and stored, so the
    # nationwide read is a pure indexed ORDER BY flash_score — never a comparison scan (perf is the
    # edge). A listing is "on flash" iff flash_started_at <= now < flash_expires_at.
    #   * flash_price_cents: the crazy price the buyer pays while the window is open (integer, S9).
    #   * flash_started_at / flash_expires_at: the window (max 1h); nothing shows once it passes.
    #   * flash_score: precomputed margin in [0,1] — higher = crazier — the sole ranking key.
    #   * flash_reference_cents: the comparable-shop average at launch, for the "was X / now Y" card.
    flash_price_cents = Column(Integer, nullable=True)
    flash_started_at = Column(DateTime(timezone=True), nullable=True)
    flash_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    flash_score = Column(Float, nullable=True, index=True)
    flash_reference_cents = Column(Integer, nullable=True)

    # Social feed post type (§8). A listing post may always carry image OR video media; this flag
    # marks a post the seller published specifically as a SHORT VIDEO (the dedicated reel-style
    # post), so the feed's "Listings | Videos" toggle can filter to them. It does NOT mean "has a
    # video" — an ordinary listing can include a video clip too; this is the seller's declared post
    # kind. The 250 MB short-video size cap is enforced on the upload/post path (seller console),
    # not here. server_default false ⇒ existing rows are ordinary listing posts.
    is_short_video = Column(Boolean, nullable=False, default=False, server_default="false", index=True)

    # Social timeline post kind (§8): "product" (sellable — price/stock/orders) or "post" (plain
    # social content — no price, no inventory, never hidden by the stock gate). server_default
    # 'product' ⇒ every existing listing is a product. Indexed so feed visibility (post OR
    # in-stock) stays index-backed.
    post_kind = Column(String(16), nullable=False, default=POST_KIND_PRODUCT,
                       server_default=POST_KIND_PRODUCT, index=True)

    # Dual-path location (denormalized from Shop at write time).
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    # spatial_index=False — GiST is declared explicitly below; avoids GeoAlchemy2's
    # SpatiaLite-flavoured auto index breaking the SQLite test path (see Shop.geog).
    geog = Column(Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)  # freshness

    shop = relationship("Shop", back_populates="listings")

    __table_args__ = (
        Index("ix_listings_geog_gist", "geog", postgresql_using="gist"),
        Index("ix_listings_latlng", "lat", "lng"),
        # Feed-eligibility composite: the buyer feed filters on (is_active AND stock_qty > 0)
        # and orders by recency, so all three ride one index — no table scan when stock-gating
        # the proximity query. Supersedes the old (is_active, created_at) index.
        Index("ix_listings_feed", "is_active", "stock_qty", "created_at"),
    )
