"""Storefront view tracking (§8, Chunk C) — the Viewing Card's data source.

A row per (shop_id, session_id) visit — NOT a row per heartbeat. First heartbeat inserts;
subsequent heartbeats in the same visit UPDATE ``last_heartbeat_at`` on the existing row. This
keeps storage O(unique visits per shop), keeps history queries free of GROUP BY, and makes the
"who is live right now" probe a range scan on ``(shop_id, last_heartbeat_at DESC)``.

The ``session_id`` is generated client-side (a short random id persisted in localStorage), so
an anonymous visitor is stably tracked across page reloads within a browser but never linked
to their weespas identity. When the visitor IS signed in, ``viewer_uuid`` is filled from the
token; otherwise it's NULL and the frontend labels them "guest" in the history list.

Not a cross-DB FK (``viewer_uuid`` is the token ``sub``, matching every other commerce table).
"""
from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, String
from sqlalchemy.sql import func

from PE.commerce.core.database import Base, utcnow


# Sources a view can originate from. Today only the storefront page emits heartbeats, but we
# capture the source so a later PDP heartbeat (e.g. product detail dwell time) or a map-hover
# heartbeat can co-exist without collapsing everything into one bucket.
VIEW_SOURCE_STOREFRONT = "storefront_page"
VIEW_SOURCES = (VIEW_SOURCE_STOREFRONT,)


class ShopViewEvent(Base):
    """A single visit to a shop's storefront. ``last_heartbeat_at`` is refreshed on every ping
    from the same (shop_id, session_id); a fresh session_id starts a new row."""
    __tablename__ = "shop_view_events"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    # The shop being viewed. FK is inside the commerce DB so we CAN enforce it — a view of a
    # deleted shop would be dead data.
    shop_id = Column(String, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False)
    # The weespas token ``sub`` of the viewer if signed in; NULL for anonymous visitors. Not a
    # cross-DB FK.
    viewer_uuid = Column(String, nullable=True, index=True)
    # Opaque client-generated string, stable across reloads within a browser (localStorage). The
    # server treats it as an opaque bucketing token — it is NOT identifying, and the client can
    # rotate it any time. Bounded to 64 chars so a rogue client can't pad a huge string.
    session_id = Column(String(64), nullable=False)
    # First time we saw this (shop_id, session_id). Never updated after insert.
    viewed_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now())
    # Most recent heartbeat from this session. A viewer is "live" iff last_heartbeat_at > now - LIVE_WINDOW.
    last_heartbeat_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now())
    # 'storefront_page' today; see VIEW_SOURCES for future values.
    source = Column(String(24), nullable=False, default=VIEW_SOURCE_STOREFRONT, server_default=VIEW_SOURCE_STOREFRONT)
    # §8 Chunk C+: the listing the visitor is looking at RIGHT NOW ("latest wins" — each
    # heartbeat overwrites this). NULL when the visitor is on the storefront index (not on a
    # specific product's PDP). Not a FK to listings on purpose: a viewer looking at a listing
    # that's since been deleted still has a valid heartbeat row; the FE dereferences the id
    # to a real listing at read time and drops the "viewing X" fragment if the listing is gone.
    viewing_listing_id = Column(String, nullable=True)
    # §8 Chunk C+: the visitor's coarse coord at heartbeat time. Optional — a visitor who
    # denied the Geolocation permission (or a browser that lacks it) sends null. Also "latest
    # wins": overwritten every ping. Used only for the seller-facing reverse-geocode label
    # (Kilimani / Karen / etc.) — never stored more precisely than a browser's coarse
    # accuracy grant provides, and never surfaced to any other viewer.
    last_lat = Column(Float, nullable=True)
    last_lng = Column(Float, nullable=True)

    __table_args__ = (
        # (shop_id, session_id) is the natural upsert key: a heartbeat from the same session
        # UPDATE-in-places instead of inserting a duplicate. Uniqueness lets the service use a
        # single SELECT + UPDATE-or-INSERT without a race window (a concurrent insert on the
        # same key fails, is retried as an update — see services/shop_views.py).
        Index("ux_shop_view_events_shop_session", "shop_id", "session_id", unique=True),
        # Live-count probe: "rows in this shop where last_heartbeat_at > now - LIVE_WINDOW". The
        # DESC index means the fresh rows are at the front — an index range scan bounded by the
        # freshness cutoff. Cost is O(live) not O(all-time visits).
        Index("ix_shop_view_events_shop_hb", "shop_id", last_heartbeat_at.desc()),
        # History query: "recent visits, newest first, optionally within a date range". Keyset
        # cursor pagination is (viewed_at, id) DESC — an index on (shop_id, viewed_at DESC)
        # is enough; id is the tie-breaker only when two visits share a microsecond.
        Index("ix_shop_view_events_shop_viewed", "shop_id", viewed_at.desc()),
    )
