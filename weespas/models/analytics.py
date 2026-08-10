from sqlalchemy import (
    Column, String, Float, Integer, DateTime, ForeignKey, Index, UniqueConstraint, Numeric
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from PE.weespas.core.database import Base


class UserSession(Base):
    """Visitor session — one row per (cookie token), upserted on each request."""
    __tablename__ = "user_sessions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    session_token = Column(String(64), nullable=False, unique=True, index=True)
    ip_address = Column(String(64), nullable=True)
    geo_lat = Column(Numeric(precision=9, scale=6), nullable=True, index=True)
    geo_lng = Column(Numeric(precision=9, scale=6), nullable=True, index=True)
    geo_city = Column(String(100), nullable=True)
    geo_county = Column(String(100), nullable=True)
    geo_source = Column(String(16), nullable=True)  # 'browser' | 'ip' | None
    user_agent = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_session_county_city", "geo_county", "geo_city"),
        Index("idx_session_user_created", "user_id", "created_at"),
    )


class PropertyViewEvent(Base):
    """Per-view event log. Complements (does not replace) Property.view_count counter."""
    __tablename__ = "property_view_events"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    property_id = Column(String, ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    session_id = Column(String, ForeignKey("user_sessions.id", ondelete="SET NULL"), nullable=True, index=True)
    viewed_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    __table_args__ = (
        Index("idx_view_property_time", "property_id", "viewed_at"),
    )


class SearchLog(Base):
    """One row per search request (text / filter / nearby)."""
    __tablename__ = "search_logs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    session_id = Column(String, ForeignKey("user_sessions.id", ondelete="SET NULL"), nullable=True, index=True)
    query_text = Column(String(255), nullable=True)
    latitude = Column(Numeric(precision=9, scale=6), nullable=True)
    longitude = Column(Numeric(precision=9, scale=6), nullable=True)
    radius_km = Column(Float, nullable=True)
    category_id = Column(String, ForeignKey("property_categories.id", ondelete="SET NULL"), nullable=True)
    listing_type = Column(String(16), nullable=True)
    min_price = Column(Float, nullable=True)
    max_price = Column(Float, nullable=True)
    result_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class Favorite(Base):
    """User-saved property. Server-side store (replaces frontend localStorage-only model)."""
    __tablename__ = "favorites"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    property_id = Column(String, ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "property_id", name="uq_favorite_user_property"),
    )


class PropertyDismissal(Base):
    """Explicit 'not interested' signal — strong negative for the personal feed."""
    __tablename__ = "property_dismissals"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    property_id = Column(String, ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "property_id", name="uq_dismissal_user_property"),
    )
