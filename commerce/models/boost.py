"""Boost grants + allowance ledger — the §8.3 reach economy.

A **Boost** buys *reach in the sponsored lane*, never rank in the organic lane (the cardinal
rule of §8.3). The organic proximity×freshness×intent feed is left pure; a Boost only makes a
listing/shop *eligible* to fill the bounded, labelled sponsored slots of a wider audience's
feed. Three geographic tiers (Swahili brand, locked §8.3):

  * **mtaa**      — a 10 km radius around the target (the neighbourhood);
  * **hustle**    — a 50 km radius (the drawn-polygon variant pends the map UI; see §8.3);
  * **sovereign** — nationwide (no geo predicate).

Two tables:
  * ``BoostGrant`` — one row per (target, tier, business-day): the scope snapshot + the live
    window. Eligibility is a PURE function of the stored scope vs the buyer's point and the
    window vs now() — no sweep (mirrors the ephemerality boost). The unique
    (target_type, target_id, tier, business_date) makes a grant idempotent and caps a target to
    one chance per tier per day.
  * ``BoostAllowance`` — the per-seller, per-tier, per-business-day quota counter (the "chances").
    Consumption is a hard-capped conditional UPDATE (fail-closed; see services.boost) so a seller
    can never spend a chance they do not have, even under a concurrent double-tap. A new business
    day is a new row starting at 0 — that IS the midnight reset, no job required.

No money columns here — a Boost is reach, not a sale. ``source`` (free|paid) is the monetisation
seam (§8.3): the free path enforces the daily quota; a paid advert (later, once billing lands)
records ``source='paid'`` and bypasses it. The column is forward-compatible schema, not dead code.
"""
from datetime import date

from geoalchemy2 import Geography
from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.sql import func
import uuid

from PE.commerce.core.database import Base

# Tier names (locked §8.3). Kept here as the single source of truth, mirrored by the schema
# Literal so an unknown tier is a 422 at the API edge before the service runs.
BOOST_MTAA = "mtaa"
BOOST_HUSTLE = "hustle"
BOOST_SOVEREIGN = "sovereign"
BOOST_TIERS = (BOOST_MTAA, BOOST_HUSTLE, BOOST_SOVEREIGN)

# Scope kinds. "radius" carries a centre + radius_m; "nation" carries neither (matches everyone).
# "polygon" is reserved for the Hustle drawn-area variant (pends the map UI, §8.3) — declared so
# the column domain is stable, but not yet emitted by the grant service.
SCOPE_RADIUS = "radius"
SCOPE_NATION = "nation"
SCOPE_POLYGON = "polygon"

# Target kinds — a grant promotes one listing, or a whole shop (its visible listings).
TARGET_LISTING = "listing"
TARGET_SHOP = "shop"
BOOST_TARGETS = (TARGET_LISTING, TARGET_SHOP)

# Ordering/lottery weight by tier (wider reach ranks first when filling a sponsored slot).
TIER_WEIGHT = {BOOST_MTAA: 1, BOOST_HUSTLE: 2, BOOST_SOVEREIGN: 3}

SOURCE_FREE = "free"
SOURCE_PAID = "paid"


class BoostGrant(Base):
    __tablename__ = "boost_grants"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    seller_id = Column(String, ForeignKey("sellers.id"), nullable=False, index=True)

    target_type = Column(String(16), nullable=False)   # listing | shop
    target_id = Column(String, nullable=False, index=True)

    tier = Column(String(16), nullable=False)           # mtaa | hustle | sovereign
    scope_kind = Column(String(16), nullable=False)     # radius | nation (| polygon, reserved)

    # Scope snapshot for a radius tier (NULL for nation): the centre is the target's location at
    # grant time, decoupled from later edits to the target. Dual-path (geog for PostGIS ST_DWithin,
    # lat/lng for the SQLite Haversine test path) — same discipline as Shop/Listing.
    center_lat = Column(Float, nullable=True)
    center_lng = Column(Float, nullable=True)
    center_geog = Column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=True
    )
    radius_m = Column(Float, nullable=True)

    # The live window: eligible iff started_at <= now < expires_at. Decays with NO write.
    started_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)

    # The business day (Africa/Nairobi) this grant was opened on — the quota bucket key.
    business_date = Column(Date, nullable=False, index=True)

    source = Column(String(8), nullable=False, default=SOURCE_FREE, server_default=SOURCE_FREE)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # One chance per (target, tier) per business day → makes a grant idempotent (a retry
        # replays the existing row) AND prevents spamming the same target/tier repeatedly in a day.
        UniqueConstraint("target_type", "target_id", "tier", "business_date",
                         name="uq_boost_target_tier_day"),
        Index("ix_boost_center_gist", "center_geog", postgresql_using="gist"),
        Index("ix_boost_center_latlng", "center_lat", "center_lng"),
        # Sponsored-candidate pull filters on the live window + scope kind.
        Index("ix_boost_live", "expires_at", "scope_kind"),
    )


class BoostAllowance(Base):
    __tablename__ = "boost_allowances"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    seller_id = Column(String, ForeignKey("sellers.id"), nullable=False, index=True)
    tier = Column(String(16), nullable=False)
    # The business day (Africa/Nairobi). A new day = a new row at used=0 (the midnight reset).
    usage_date = Column(Date, nullable=False, default=date.today)
    # Chances consumed today for this tier. The hard cap is enforced in the consume UPDATE
    # (fail-closed), not by trusting this value on read.
    used = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("seller_id", "tier", "usage_date", name="uq_allowance_seller_tier_day"),
    )
