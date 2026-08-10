"""SavedSearch — server-side persistence of filter presets.

Each row is a single user's named filter snapshot. The `filters` JSON
column carries the exact shape produced by the frontend's
`useFilterParams` hook, which means apply-search is a pure URL-param
write on the client and the server never has to interpret the contents.

Performance notes:
- Indexed on (user_id, last_used_at DESC) so the list query is a single
  range scan capped at <= 25 rows; no Redis cache needed.
- UNIQUE on (user_id, name) lets the rename UI use a simple PATCH and
  surface conflicts directly instead of doing a list-then-merge.
"""
from sqlalchemy import (
    Column, String, DateTime, ForeignKey, Index, UniqueConstraint, Text,
)
from sqlalchemy.sql import func
import uuid

from PE.weespas.core.database import Base


class SavedSearch(Base):
    __tablename__ = "saved_searches"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(80), nullable=False)
    # JSON serialised as TEXT keeps the model portable to SQLite (dev) and
    # Postgres (prod). The shape is opaque to the server; the frontend
    # round-trips it through `useFilterParams`. If we ever want JSONB
    # filtering server-side, we migrate this to a JSONB column then.
    filters = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    last_used_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_saved_search_user_name"),
        Index("idx_saved_search_user_last_used", "user_id", "last_used_at"),
    )
