"""Metering & company-detection tables (billing_architecture.md §8).

Two tables, both Alembic-managed (in MANAGED_TABLES, NOT created by
`create_tables()` — see migrations/). They form the *second* revenue line's spine:
§2's entitlement/reveal monetises individual house-hunting; §8 governs InSAR
COMMERCIAL use by companies. One telemetry spine, two lines.

  - MeteringEvent      : an append-style behavioural log of the actions that signal
                         commercial-scale use — reveal, map_open, directions_open,
                         checkout_initiated/paid, and the InSAR-side
                         insar_building_view / insar_export. Session-anchored (reuses
                         user_sessions) so an anonymous-then-signed-in user is one
                         behavioural thread. Written off the request path via
                         safe_delay so a metering hiccup can never fail a reveal.
  - UserUsageProfile   : the per-user commercial-likelihood SCORE + its signal
                         breakdown, recomputed by a Celery beat job over the events.
                         The policy engine reads this O(1) (one PK lookup) to decide
                         free / metered / blocked. Soft gate — high score is an
                         upsell, never an accusation (commercial_model.md §7.2).

DPA-2019 note: this is usage-metering for billing (a disclosed, legitimate purpose).
We store the minimum — an action label + ids + timestamp — never message content.
"""
from sqlalchemy import (
    Column, String, Integer, Float, SmallInteger, DateTime, ForeignKey, Index,
)
from sqlalchemy.sql import func
import uuid

from PE.weespas.core.database import Base

# The metered action vocabulary. Kept as plain strings (not a PG enum) so adding a
# new action later is a code change, never a migration / ALTER TYPE.
EVENT_REVEAL = "reveal"
EVENT_MAP_OPEN = "map_open"
EVENT_DIRECTIONS_OPEN = "directions_open"
EVENT_CHECKOUT_INITIATED = "checkout_initiated"
EVENT_CHECKOUT_PAID = "checkout_paid"
EVENT_INSAR_BUILDING_VIEW = "insar_building_view"
EVENT_INSAR_EXPORT = "insar_export"
# Server-side signal: the InSAR data API itself reports a full bundle pull (one whole AOI's
# risk dataset). Emitted by the read app's bundle endpoint, NOT the browser — so a direct
# curl-the-data scraper that emits no frontend telemetry is still visible to the scorer.
EVENT_INSAR_BUNDLE_FETCH = "insar_bundle_fetch"

VALID_EVENT_ACTIONS = (
    EVENT_REVEAL, EVENT_MAP_OPEN, EVENT_DIRECTIONS_OPEN,
    EVENT_CHECKOUT_INITIATED, EVENT_CHECKOUT_PAID,
    EVENT_INSAR_BUILDING_VIEW, EVENT_INSAR_EXPORT, EVENT_INSAR_BUNDLE_FETCH,
)

# The actions that carry commercial weight (used by the scorer). A reveal is a
# normal house-hunt; bulk InSAR views / exports / bundle pulls are the company tell.
# A bundle fetch carries aoi_code, so it feeds BREADTH (distinct AOIs swept) too — exactly
# the signal a portfolio sweep trips.
COMMERCIAL_EVENT_ACTIONS = (
    EVENT_INSAR_BUILDING_VIEW, EVENT_INSAR_EXPORT, EVENT_INSAR_BUNDLE_FETCH, EVENT_REVEAL,
)


class MeteringEvent(Base):
    """One behavioural event. Append-style — we never UPDATE a metering row.

    `target_ref` is the listing_id / building_id the action touched (nullable —
    map_open has none); `aoi_code` is set for InSAR-side events so the scorer can
    measure *breadth* (distinct AOIs swept). `meta` is a tiny free-text slot for a
    count or flag (e.g. export row-count) — NOT message content.
    """
    __tablename__ = "metering_event"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"),
                     nullable=True, index=True)
    session_id = Column(String, ForeignKey("user_sessions.id", ondelete="SET NULL"),
                        nullable=True, index=True)
    action = Column(String(32), nullable=False, index=True)
    target_ref = Column(String(64), nullable=True)   # listing_id | building_id | None
    aoi_code = Column(String(64), nullable=True)      # set for InSAR-side events
    meta = Column(String(64), nullable=True)          # small int/flag (e.g. export count)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    __table_args__ = (
        # The scorer scans (user_id, created_at) windows; this index serves it.
        Index("idx_metering_user_action_time", "user_id", "action", "created_at"),
    )


class UserUsageProfile(Base):
    """Per-user commercial-likelihood score + signal breakdown (one row per user).

    Recomputed by the `policy.recompute_usage_profiles` beat job. The policy engine
    reads exactly one row (PK lookup) → its gate stays O(1) on the request path.
    `score` is in [0,1]; `is_metered` is the precomputed boolean the gate trusts so
    the threshold lives in ONE place (the job), not scattered at call sites.
    """
    __tablename__ = "user_usage_profile"

    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"),
                     primary_key=True)
    score = Column(Float, nullable=False, default=0.0)          # [0,1] commercial likelihood
    is_metered = Column(SmallInteger, nullable=False, default=0)  # 1 ⇒ soft-gate to business plan
    # Signal breakdown (for transparency + the soft-gate copy "you swept N AOIs…").
    volume = Column(Integer, nullable=False, default=0)          # commercial actions in window
    breadth = Column(Integer, nullable=False, default=0)         # distinct AOIs swept
    export_count = Column(Integer, nullable=False, default=0)    # CSV/report exports
    automation = Column(Float, nullable=False, default=0.0)      # request-regularity proxy [0,1]
    corporate_domain = Column(SmallInteger, nullable=False, default=0)  # 1 ⇒ known corp email
    computed_at = Column(DateTime(timezone=True), server_default=func.now(),
                         onupdate=func.now())

    __table_args__ = (
        Index("idx_usage_profile_metered", "is_metered"),
    )
