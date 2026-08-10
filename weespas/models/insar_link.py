"""Weespas ↔ InSAR integration tables (Phase 4a + disambiguating resolver).

Alembic-managed (NOT created by `create_tables()` — see migrations/). They form
the spine of the integration:

  - BuildingLink     : the auditable mapping from a Weespas listing to an InSAR
                       building footprint (point→polygon resolve). One listing may
                       link to one building; the resolve method + confidence are
                       stored so a bad geocode is correctable, not a silent guess.
                       `confirmed_by_agent` marks a human-confirmed (authoritative)
                       link the auto-resolver must never overwrite.
  - BuildingLinkCandidate : the top-N plausible footprints a pin could be, frozen at
                       resolve time, so the "confirm your building" step can offer
                       them and an ambiguous listing's worst-case provisional tier
                       can be read from exactly these buildings. Audit/candidate
                       store only — never an authoritative mapping.
  - StructuralFlag   : the external "second sensor" — an engineer/authority judgement
                       of a building's construction quality (UNSAFE / AUTH_UNSAFE /
                       CLEARED). InSAR is blind to construction quality, so this is
                       what lets the risk score see the dominant Nairobi collapse
                       driver. Manual entry now; an auto-feed lands on the same table.
  - NotificationAudit: the immutable, hash-chained record of every risk notification
                       (who was told what, when). Append-only at the DB level
                       (REVOKE UPDATE/DELETE + trigger, added in the migration) — it
                       is legal-grade evidence designed to defeat "no one told us"
                       and "the official was never informed". Population is a later
                       phase; the table + its integrity guarantees are created now.

Flag-state codes MIRROR the InSAR side EXACTLY (scripts/postprocess.py STRUCT_*):
0=NONE/uninspected, 1=CLEARED, 2=UNSAFE, 3=AUTH_UNSAFE. Kept in sync intentionally
so the value a professional writes here is the value the scorer fuses.
"""
from sqlalchemy import (
    Column, String, Integer, SmallInteger, BigInteger, Float, Boolean, Date,
    DateTime, ForeignKey, Index, UniqueConstraint, Text,
)
from sqlalchemy.sql import func, text
import uuid

from PE.weespas.core.database import Base

# Structural-flag states — MUST match scripts/postprocess.py STRUCT_* on the InSAR side.
FLAG_NONE = 0          # uninspected / unknown (the default; never lowers risk)
FLAG_CLEARED = 1       # engineer-certified safe (decaying, motion-overridable damp)
FLAG_UNSAFE = 2        # engineer flagged structurally unsafe (raises a floor)
FLAG_AUTH_UNSAFE = 3   # authority condemnation / enforcement notice (highest floor)

VALID_FLAG_STATES = (FLAG_NONE, FLAG_CLEARED, FLAG_UNSAFE, FLAG_AUTH_UNSAFE)
VALID_FLAG_SOURCES = ("engineer", "authority")


class BuildingLink(Base):
    """Maps a Weespas listing (Property) to an InSAR building footprint.

    Resolved by point-in-polygon (listing lat/lon → footprint), with a nearest
    fallback; `match_method` + `match_confidence` record how the link was made so
    it is auditable and correctable rather than a silent nearest-neighbour guess.
    """
    __tablename__ = "building_link"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    listing_id = Column(String, ForeignKey("properties.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    aoi_code = Column(String(64), nullable=False, index=True)
    insar_building_id = Column(BigInteger, nullable=False, index=True)
    # How the link was made (all <=16 chars so the column width is unchanged):
    #   'pip'            point-in-polygon containment (authoritative)
    #   'nearest'        single nearest footprint within radius (legacy auto-fallback)
    #   'disambiguated'  attribute-aware winner among several buffer candidates
    #   'agent_confirmed' the listing owner tapped the right building (authoritative)
    #   'none'           outside coverage
    match_method = Column(String(16), nullable=False, default="pip")
    match_confidence = Column(Float, nullable=True)  # 1.0 for pip, <1 for nearest by distance
    # True once a human (the listing owner/agent) confirmed the building. A confirmed
    # link is AUTHORITATIVE: the auto-resolver and the backfill must never overwrite it
    # (see resolve_and_link's early-return guard). Additive column, defaults false.
    confirmed_by_agent = Column(Boolean, nullable=False, server_default=text("false"),
                                default=False)
    # How many plausible candidates the disambiguating resolver saw (audit only).
    candidate_count = Column(SmallInteger, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("listing_id", "insar_building_id",
                         name="uq_building_link_listing_building"),
        Index("idx_building_link_aoi_building", "aoi_code", "insar_building_id"),
    )


class BuildingLinkCandidate(Base):
    """The set of plausible footprints a listing's pin could be, frozen at resolve time.

    When a pin lands in a dense cluster the resolver keeps the top-N scored candidates
    here so the "confirm your building" endpoint can offer them WITHOUT re-running the
    spatial search, and so an ambiguous listing's provisional (worst-case) tier can be
    computed from the live tiers of exactly these buildings.

    This is an audit/candidate store, NOT an authoritative mapping: `danger_level_at_resolve`
    is a snapshot for debugging only — the tier is ALWAYS re-read live (it is re-scored on
    every InSAR rebuild). A `land` listing stores its contributing neighbours here too
    (method 'land_aggregate' on the score-less rows), again never as a real link.
    """
    __tablename__ = "building_link_candidate"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    listing_id = Column(String, ForeignKey("properties.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    aoi_code = Column(String(64), nullable=False)
    insar_building_id = Column(BigInteger, nullable=False)
    rank = Column(SmallInteger, nullable=False)            # 0 = best score
    score = Column(Float, nullable=True)                   # composite score (NULL for land neighbours)
    distance_m = Column(Float, nullable=True)              # metres from the pin to the footprint
    height_m = Column(Float, nullable=True)                # snapshot, for the confirm-UI label
    n_floors = Column(SmallInteger, nullable=True)         # snapshot, for the confirm-UI label
    danger_level_at_resolve = Column(SmallInteger, nullable=True)  # snapshot ONLY — never authoritative
    vetoed = Column(Boolean, nullable=False, server_default=text("false"), default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("listing_id", "insar_building_id",
                         name="uq_building_link_candidate_listing_building"),
        Index("idx_building_link_candidate_listing_rank", "listing_id", "rank"),
    )


class StructuralFlag(Base):
    """An engineer/authority structural judgement of one InSAR building.

    The 'second sensor' InSAR cannot provide. Multiple rows may exist per building
    over time (a history of inspections); the InSAR loader takes the most recent.
    `granted_by` is the user who recorded it (accountability); `source` distinguishes
    an engineer's professional flag from an authority's enforcement action.
    """
    __tablename__ = "structural_flag"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    aoi_code = Column(String(64), nullable=False, index=True)
    insar_building_id = Column(BigInteger, nullable=False, index=True)
    # 0=NONE 1=CLEARED 2=UNSAFE 3=AUTH_UNSAFE (see module docstring / postprocess STRUCT_*).
    state = Column(SmallInteger, nullable=False, default=FLAG_NONE)
    observed_at = Column(Date, nullable=True)        # date of inspection/judgement (drives decay)
    source = Column(String(16), nullable=False)      # 'engineer' | 'authority'
    note = Column(Text, nullable=True)
    granted_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"),
                        nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    __table_args__ = (
        Index("idx_structural_flag_building_created", "insar_building_id", "created_at"),
    )


class NotificationAudit(Base):
    """Immutable, hash-chained record of a risk notification.

    Each row stores the SHA-256 of the previous row (`prev_hash`) and its own content
    hash (`row_hash`), forming a tamper-evident chain: altering or deleting any past
    row breaks every subsequent hash. At the DB level the table is append-only
    (UPDATE/DELETE revoked + a trigger that raises — added in the Alembic migration,
    not inferable from this ORM definition). This is the anti-corruption core: proof
    of *who was told what, when*. Population is a later phase (P4c).
    """
    __tablename__ = "notification_audit"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    aoi_code = Column(String(64), nullable=False, index=True)
    insar_building_id = Column(BigInteger, nullable=False, index=True)
    # Who/what/why — kept as structured-ish text now; tightened in P4c.
    recipient_role = Column(String(32), nullable=False)   # owner | tenant | authority | certifier
    recipient_ref = Column(String(255), nullable=True)    # user id / phone / authority contact
    danger_level = Column(SmallInteger, nullable=False)   # the tier that triggered it (0..4)
    channel = Column(String(16), nullable=False)          # sms | email | push | dry_run
    delivery_state = Column(String(16), nullable=False, default="pending")
    payload = Column(Text, nullable=True)                 # the rendered message + metadata (JSON)
    prev_hash = Column(String(64), nullable=True)         # SHA-256 of the previous row's row_hash
    row_hash = Column(String(64), nullable=False)         # SHA-256 over this row's canonical content
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    __table_args__ = (
        UniqueConstraint("row_hash", name="uq_notification_audit_row_hash"),
        Index("idx_notification_audit_building", "aoi_code", "insar_building_id"),
    )
