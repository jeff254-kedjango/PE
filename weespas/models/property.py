from sqlalchemy import (
    Column, String, Float, DateTime, Boolean, Integer, Text, Enum, 
    ForeignKey, Index, UniqueConstraint, DECIMAL, Numeric
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import UUID
import uuid
import enum
from PE.weespas.core.database import Base


class PropertyListingType(enum.Enum):
    """Listing type: rent or sale"""
    RENT = "rent"
    SALE = "sale"


# InSAR footprint-verification state of a listing. The non-pending values are the
# SAME strings as services.insar_resolver.COVERAGE_* on purpose: coverage→status is
# an identity map (no translation table to drift). "pending" is the only extra state
# — it means the background verify task hasn't run/finished yet.
#   pending           : just uploaded; verification queued/running
#   monitored         : sits on an InSAR-monitored footprint (carries a risk tier)
#   needs_confirmation: the pin landed in a cluster of plausible footprints we can't
#                       safely auto-pick — the owner taps the right one. Until then a
#                       conservative (worst-case) provisional tier is shown, never a
#                       silently-wrong building.
#   monitored_land    : a `land` listing — no building footprint; ground movement is
#                       estimated from nearby monitored buildings (never a building tier)
#   not_monitored     : outside our footprints / no match — no risk reading, never "safe"
#   unavailable       : InSAR DB was offline at verify time; re-verifiable, never final
VERIFICATION_PENDING = "pending"
VERIFICATION_MONITORED = "monitored"
VERIFICATION_NEEDS_CONFIRMATION = "needs_confirmation"
VERIFICATION_MONITORED_LAND = "monitored_land"
VERIFICATION_NOT_MONITORED = "not_monitored"
VERIFICATION_UNAVAILABLE = "unavailable"


class PropertyCategoryEnum(enum.Enum):
    """Property categories for controlled dropdown"""
    HOUSE = "house"
    APARTMENT = "apartment"
    VILLA = "villa"
    STUDIO = "studio"
    OFFICE = "office"
    LAND = "land"
    WAREHOUSE = "warehouse"
    SHOP = "shop"
    KIOSK = "kiosk"
    CONTAINER = "container"
    STALL = "stall"
    COMMERCIAL_SPACE = "commercial_space"
    OTHER = "other"


class PropertyCategory(Base):
    """Extensible property categories table for future flexibility"""
    __tablename__ = "property_categories"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    name = Column(String(100), nullable=False, unique=True, index=True)
    slug = Column(String(100), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)
    icon_url = Column(String(500), nullable=True)
    display_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    properties = relationship("Property", back_populates="category")


class Agent(Base):
    """Agent model for property listings"""
    __tablename__ = "agents"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    agent_name = Column(String(255), nullable=False, index=True)
    agent_phone_number = Column(String(20), nullable=False)
    agent_profile_picture = Column(String(500), nullable=True)  # URL to CDN
    email = Column(String(255), nullable=True)
    bio = Column(Text, nullable=True)
    is_verified = Column(Boolean, default=False, index=True)
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    properties = relationship("Property", back_populates="agent")

    __table_args__ = (
        UniqueConstraint('agent_phone_number', name='uq_agent_phone_number'),
    )


class Address(Base):
    """Structured address for properties"""
    __tablename__ = "addresses"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    property_id = Column(String, ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True)
    location_name = Column(String(255), nullable=False, index=True)
    street_address = Column(String(255), nullable=True)
    city = Column(String(100), nullable=True, index=True)
    county = Column(String(100), nullable=True, index=True)
    postal_code = Column(String(20), nullable=True)
    country = Column(String(100), default="Kenya", index=True)
    latitude = Column(Numeric(precision=9, scale=6), nullable=False, index=True)
    longitude = Column(Numeric(precision=9, scale=6), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Index for geo-spatial queries
    __table_args__ = (
        Index('idx_latitude_longitude', 'latitude', 'longitude'),
    )

    # Relationships
    property = relationship("Property", back_populates="address")


class PropertyImage(Base):
    """Media management: Property images with metadata"""
    __tablename__ = "property_images"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    property_id = Column(String, ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True)
    url = Column(String(500), nullable=False)
    thumbnail_url = Column(String(500), nullable=True)  # For lazy loading
    original_filename = Column(String(255), nullable=True)
    file_size = Column(Integer, nullable=True)  # in bytes
    mime_type = Column(String(50), nullable=True)
    alt_text = Column(String(255), nullable=True)  # for accessibility
    order = Column(Integer, default=0, index=True)  # for sorting
    is_main = Column(Boolean, default=False, index=True)  # primary image
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    property = relationship("Property", back_populates="images")


class PropertyVideo(Base):
    """Media management: Property videos with streaming support"""
    __tablename__ = "property_videos"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    property_id = Column(String, ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True)
    url = Column(String(500), nullable=False)
    thumbnail_url = Column(String(500), nullable=True)  # Preview image
    streaming_url = Column(String(500), nullable=True)  # HLS or adaptive streaming URL
    title = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    duration = Column(Integer, nullable=True)  # in seconds
    file_size = Column(Integer, nullable=True)  # in bytes
    mime_type = Column(String(50), nullable=True)
    order = Column(Integer, default=0, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    property = relationship("Property", back_populates="videos")


class Property(Base):
    """Core property model: highly scalable for millions of concurrent users"""
    __tablename__ = "properties"

    # Primary Key: UUID for distributed systems and scalability
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    
    # Basic Details
    title = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)
    
    # Pricing & Currency
    price = Column(DECIMAL(15, 2), nullable=False, index=True)  # Indexed for sorting/filtering
    currency = Column(String(3), default="KES", index=True)
    
    # Property Type & Status
    listing_type = Column(Enum(PropertyListingType), nullable=False, index=True)
    category_id = Column(String, ForeignKey("property_categories.id", ondelete="RESTRICT"), nullable=False, index=True)
    
    # Property Attributes
    is_engineer_certified = Column(Boolean, default=False, index=True)  # Trust signal & ranking
    is_featured = Column(Boolean, default=False, index=True)
    is_active = Column(Boolean, default=True, index=True)
    
    # Agent Information
    agent_id = Column(String, ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True)
    
    # Additional Details
    bedrooms = Column(Integer, nullable=True, index=True)
    bathrooms = Column(Integer, nullable=True, index=True)
    size = Column(String(50), nullable=True)  # e.g., "500 sqft"
    size_numeric = Column(Float, nullable=True, index=True)  # for sorting
    parking_spaces = Column(Integer, nullable=True)
    year_built = Column(Integer, nullable=True)
    
    # InSAR footprint verification (resolved in the background after upload).
    # String (not a PG enum) to match BuildingLink.match_method and avoid an
    # enum-type migration on the live DB. Defaults to 'pending' so a freshly
    # inserted row is honestly "not yet checked", never an implied "safe".
    # String(32), not (16): the coverage strings are written here verbatim by
    # insar_verify_tasks (identity map), and 'needs_confirmation' is 18 chars — it
    # overflowed the original 16 and silently rolled back the whole verify task on
    # Postgres. 32 leaves headroom for any future coverage tier. See migration
    # b8c9d0e1f2a3 (widen) + the matching idempotent ALTER on the live DB.
    verification_status = Column(
        String(32), nullable=False, server_default=VERIFICATION_PENDING, index=True
    )
    verified_at = Column(DateTime(timezone=True), nullable=True)

    # Metadata
    view_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), index=True)
    expires_at = Column(DateTime(timezone=True), nullable=True, index=True)  # for listing expiration
    # When a featured promotion expires. NULL means no expiry / not currently promoted.
    featured_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    
    # Relationships
    category = relationship("PropertyCategory", back_populates="properties")
    agent = relationship("Agent", back_populates="properties")
    address = relationship("Address", back_populates="property", uselist=False, cascade="all, delete-orphan")
    images = relationship("PropertyImage", back_populates="property", cascade="all, delete-orphan", lazy="joined")
    videos = relationship("PropertyVideo", back_populates="property", cascade="all, delete-orphan", lazy="joined")
    
    # Optimization: Composite indexes for common queries
    __table_args__ = (
        Index('idx_listing_type_price', 'listing_type', 'price'),  # for rent/sale + price filter
        Index('idx_category_is_active', 'category_id', 'is_active'),  # for category browsing
        Index('idx_created_at_is_active', 'created_at', 'is_active'),  # for recent listings
        Index('idx_engineer_certified_active', 'is_engineer_certified', 'is_active'),  # trust signal
        Index('idx_agent_id_active', 'agent_id', 'is_active'),  # agent listings
        Index('idx_search', 'title', 'is_active'),  # for text search (basic)
    )