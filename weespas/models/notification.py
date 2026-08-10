"""In-app notification inbox (per-user, read/unread).

This is the GENERAL-PURPOSE user inbox — the bell + dropdown in the Weespas UI.
It is deliberately SEPARATE from `models.insar_link.NotificationAudit`:

  - NotificationAudit is append-only, hash-chained, immutable legal-grade evidence
    of risk alerts to building occupants ("who was told what, when"). It has no
    `user_id` and no read-state, and must never be mutated.
  - Notification (this table) is a mutable per-user inbox row whose `read_at` flips
    when the user opens it. Reusing the audit table for this would corrupt its
    evidentiary contract.

First producer: the InSAR footprint-verification task (kind='listing_verification'),
which tells an agent/owner whether their freshly-uploaded listing landed on our
monitored grid. The `kind` column keeps the table open to future notification types
without a schema change.

Indexes are chosen so the two hot reads are cheap:
  - unread badge count  → (user_id, read_at)      partial-ish indexed count
  - inbox list (newest) → (user_id, created_at)    bounded keyset scan
Both are scoped by user_id; a query never scans another user's rows.
"""
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Index
from sqlalchemy.sql import func
import uuid

from PE.weespas.core.database import Base

# Notification kinds (extensible — add a constant, no migration needed).
KIND_LISTING_VERIFICATION = "listing_verification"


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind = Column(String(32), nullable=False, default=KIND_LISTING_VERIFICATION)
    title = Column(String(160), nullable=False)
    body = Column(Text, nullable=False)
    # Optional in-app deep-link target (e.g. "/properties/{id}"). Relative path only —
    # the frontend routes it; we never store an absolute external URL here.
    link = Column(String(500), nullable=True)
    # NULL = unread. Set to the read timestamp when the user opens it.
    read_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    __table_args__ = (
        # Unread-count: WHERE user_id=? AND read_at IS NULL — covered by this composite.
        Index("idx_notification_user_read", "user_id", "read_at"),
        # Inbox list newest-first: WHERE user_id=? ORDER BY created_at DESC.
        Index("idx_notification_user_created", "user_id", "created_at"),
    )
