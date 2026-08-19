"""Seller (profile = storefront) and Shop models.

The social-commerce moat: a Seller's profile IS their storefront, and a Shop is a
physical point on the map. Shops carry BOTH a PostGIS geography point (the prod source of
truth, GiST-indexed for ST_DWithin radius search) AND plain lat/lng floats. The floats are
not test-only scaffolding — they back the SQLite Haversine test path, ride along in the
client-stitch payload, and aid debugging. A single setter keeps the two representations
from drifting (see services.proximity.set_location).

No cross-DB foreign keys: ``user_uuid`` and ``property_uuid`` are synchronized UUIDs into
the weespas/InSAR databases (architecture doc §3) — indexed string columns, stitched
client-side, never SQL-joined.
"""
from geoalchemy2 import Geography
from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid

from PE.commerce.core.database import Base, utcnow


class Seller(Base):
    __tablename__ = "sellers"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    # == the weespas user id (the token `sub`). NOT a FK — separate database (doc §3).
    user_uuid = Column(String, nullable=False, index=True)
    display_name = Column(String(120), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # WeesStock market consent (§WeesStock F4 — the investor-facing discovery surface). The
    # seller's credit profile is exposed to investors ONLY after this explicit opt-in; the market
    # endpoints enforce it (an unlisted seller's id is a uniform 404 — no existence leak).
    # Reversible, default-off, and the sole gate between a private score and a public listing.
    weesstock_listed = Column(Boolean, nullable=False, default=False, server_default="false")

    shops = relationship("Shop", back_populates="seller", cascade="all, delete-orphan")


class Shop(Base):
    __tablename__ = "shops"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    seller_id = Column(String, ForeignKey("sellers.id"), nullable=False, index=True)
    name = Column(String(160), nullable=False)
    # Seller-PUBLISHED business card (§8 shop profile hovercard). These are OPT-IN business
    # details the seller chooses to show buyers — NOT account PII (commerce owns no identity,
    # S6). Both nullable: an auto-provisioned personal-timeline shop, or a seller who hasn't
    # filled them in, simply has none and the profile card omits the row.
    #   * description — a short "about this shop" blurb, bounded to ~200 words at the API edge.
    #   * contact     — a single public contact line the seller elects to publish (e.g. a
    #                   WhatsApp/phone the seller is happy to show). The buyer still reaches the
    #                   seller via the existing per-listing Ask inquiry regardless.
    description = Column(Text, nullable=True)
    contact = Column(String(255), nullable=True)
    #   * avatar_url — the shop's profile picture / logo. A media URL in the SAME shape as a
    #                  listing's media_urls (an absolute http(s) URL or a relative /uploads/...
    #                  path served by the weespas media pipeline); the frontend resolves it via
    #                  resolveMediaUrl. NULL ⇒ the client falls back to the initials avatar. Not
    #                  PII (S6): a seller-published storefront image, same trust class as the blurb.
    avatar_url = Column(String(512), nullable=True)
    #   * banner_url — the shop's wide cover/hero image (§8 shop profile). Same media-URL shape as
    #                  avatar_url (absolute or /uploads/... relative, resolved via resolveMediaUrl).
    #                  NULL ⇒ the profile shows a plain header (no banner). Distinct from avatar_url:
    #                  the avatar is the square logo used on cards + promotions, the banner is the
    #                  wide backdrop shown only on the shop's own profile. Not PII (S6).
    banner_url = Column(String(512), nullable=True)
    #   * category   — the shop's trade category (§8 trending rail color + feed signal). A slug from
    #                  core.categories.SHOP_CATEGORIES (validated at the API edge); NULL ⇒ an
    #                  un-categorised / personal-timeline shop (the rail uses a neutral treatment).
    #                  The slug→color map is a frontend presentation concern, never stored here.
    category = Column(String(40), nullable=True, index=True)
    #   * handle     — the shop's shareable URL slug (§8 storefront: /shop/<handle>). One-shot claim
    #                  at shop creation (or later, until first set); case-insensitive-unique via the
    #                  functional index below (Postgres) with API-edge normalization to lowercase
    #                  (both paths). Bounded 3–30 chars, alphanumeric + hyphens, no leading/trailing
    #                  hyphen, no double-hyphen; a small reserved-word deny-list (mine, admin, api,
    #                  new, shop, sellers, ...) is enforced at the API edge, not in the DB. NULL ⇒
    #                  the shop has no handle yet and its storefront URL falls back to the seller_id
    #                  form (/shop/<sellerId>), so shareable links exist for every shop from day one.
    handle = Column(String(40), nullable=True)
    # Stitch key to an InSAR/weespas building footprint (Confirmed safety badge). Optional:
    # a shop may not sit on a monitored footprint.
    property_uuid = Column(String, nullable=True, index=True)
    # Dual-path location: floats (fallback + stitch payload) + PostGIS geography (prod).
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    # spatial_index=False: GeoAlchemy2's auto spatial-index DDL assumes SpatiaLite on
    # SQLite (CreateSpatialIndex) and would break the test path. The GiST index is declared
    # explicitly below instead — one source of truth, skipped cleanly on SQLite.
    geog = Column(Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    seller = relationship("Seller", back_populates="shops")
    listings = relationship("Listing", back_populates="shop", cascade="all, delete-orphan")

    __table_args__ = (
        # GiST in prod (PostGIS only — skipped on SQLite); btree (lat,lng) backs the
        # SQLite bbox prefilter and is harmless in prod.
        Index("ix_shops_geog_gist", "geog", postgresql_using="gist"),
        Index("ix_shops_latlng", "lat", "lng"),
        # Handle uniqueness is case-insensitive: a functional UNIQUE index on lower(handle) enforces
        # that "MamaMboga" and "mamamboga" can't both exist. Postgres-only expression — SQLite tests
        # rely on API-edge lowercase normalization + a plain unique guard (the service catches the
        # IntegrityError on collision and 409s). NULL handles are allowed to coexist (a partial
        # WHERE clause skips them explicitly so the index doesn't reject multiple un-claimed shops).
        Index(
            "uq_shops_handle_lower",
            func.lower(handle),
            unique=True,
            postgresql_where=(handle.isnot(None)),
        ),
    )


class ShopSponsoredCapOverride(Base):
    """A shop's request for — and staff decision on — a per-shop override of the sponsored-lane cap
    (§8.3 fairness cap, ``settings.feed_sponsored_max_per_shop``). One row per shop (UNIQUE), so a
    re-application UPSERTS the same row rather than piling duplicates.

    Semantics (deliberately narrow to avoid foot-guns): the override affects the feed ONLY when
    ``status == 'approved'`` AND ``approved_cap`` is a positive int — then that shop is allowed
    ``approved_cap`` sponsored slots instead of the global default. ``pending``/``rejected`` (or a
    non-positive approved_cap) fall back to the default, so a not-yet-decided or rejected request is
    simply inert. ``requested_cap`` is what the seller asked for (audit); ``approved_cap`` is what
    staff granted (may differ). ``decided_by`` snapshots the deciding staffer's token ``sub`` (NOT a
    FK — weespas owns identity, doc §3). Bounded by ``settings.boost_cap_override_max`` at the edge.
    """
    __tablename__ = "shop_sponsored_cap_overrides"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    shop_id = Column(String, ForeignKey("shops.id"), nullable=False, index=True)
    requested_cap = Column(Integer, nullable=False)
    # pending | approved | rejected. Python + server default so a fresh row is inert until decided.
    status = Column(String(16), nullable=False, default="pending", server_default="pending")
    # The staff-granted absolute cap. NULL until approved; cleared on reject. Only a positive value
    # (with status='approved') ever reaches the feed hot path.
    approved_cap = Column(Integer, nullable=True)
    # Deciding staffer's token sub (audit). NOT a FK — separate identity DB (doc §3).
    decided_by = Column(String, nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), onupdate=utcnow,
    )

    __table_args__ = (
        # One override record per shop → makes apply_for_override an idempotent UPSERT.
        UniqueConstraint("shop_id", name="uq_shop_cap_override_shop"),
        # The admin pending-queue read filters on status.
        Index("ix_shop_cap_override_status", "status"),
    )


class ShopSubscription(Base):
    """A user "following" a shop to get its updates (§8 "Notify"). Mirrors SavedListing exactly:
    a UNIQUE (user_uuid, shop_id) makes the follow idempotent (a double-follow is a no-op, never a
    duplicate row) and a concurrent follow-race resolves to "already following" (the caught
    IntegrityError, no 500). The follower COUNT for a shop is a single COUNT; the caller's own
    follow-state for a set of shops is one batched membership query (no N+1).

    This persists the subscription only — actual push / in-app delivery of a followed shop's stock
    changes is a deliberate downstream seam (no notification store yet); the row is the durable
    intent a delivery path will later read. ``user_uuid`` is the weespas user id (token sub) — NOT
    a FK (separate database, doc §3)."""
    __tablename__ = "shop_subscriptions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    # The follower (weespas user id). NOT a FK — separate database (doc §3).
    user_uuid = Column(String, nullable=False, index=True)
    shop_id = Column(String, ForeignKey("shops.id"), nullable=False, index=True)
    # Python-side default so a future "my follows" keyset cursor round-trips on SQLite (see
    # core.database.utcnow); server_default is the DB-side fallback.
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now())

    __table_args__ = (
        # One follow per (user, shop): makes toggle_follow idempotent + powers the count/membership.
        UniqueConstraint("user_uuid", "shop_id", name="uq_shop_sub_user_shop"),
        # "my follows" keyset, newest-first, scoped to the caller.
        Index("ix_shop_sub_user_created", "user_uuid", "created_at"),
    )
