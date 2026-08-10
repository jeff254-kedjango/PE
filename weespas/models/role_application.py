"""RoleApplication — self-service Become Agent / Become Staff applications.

Mirrors `models/deletion_request.py` deliberately: same column shapes,
same `status` enum string, same nullable `reviewed_*` columns. The two
moderation queues are presented side-by-side on the AdminPage and share
the same review-flow primitives in `services/moderation_enrichment.py`.

Why a dedicated table instead of overloading `deletion_requests` with a
`kind` discriminator: the lifecycle differs (an approved application
GRANTS state, an approved deletion REMOVES state) and the predicate
shape an admin scans by ("show me all pending agent applications") is
exactly the composite index we want to design for here — `(status,
role_requested)`. Bolting `kind` onto the existing table forces every
existing deletion-flow SQL filter to add `WHERE kind='deletion'` and
forfeits a precisely-indexed `role_requested` lookup.
"""
from sqlalchemy import Column, String, DateTime, Text, ForeignKey, Index
from sqlalchemy.sql import func
import uuid
from PE.weespas.core.database import Base


# Status values — kept as plain strings (not Python Enum) to match the
# `deletion_requests.status` precedent and to avoid the Alembic enum-type
# migration friction.
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"

# Role values an applicant may request. Anything else is a 400 at the
# router boundary — the FK to `user_roles.role` is loose-typed (VARCHAR),
# so we gate at the application layer.
ROLE_AGENT = "agent"
ROLE_STAFF = "staff"


class RoleApplication(Base):
    """User-submitted application to be granted the `agent` or `staff` role."""
    __tablename__ = "role_applications"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    applicant_id = Column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role_requested = Column(String(20), nullable=False)
    message = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default=STATUS_PENDING, index=True)
    reviewed_by_id = Column(
        String,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    review_note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # Hot path: admin's "show me all pending agent/staff applications"
        # scan. Composite (status, role_requested) lets a single index seek
        # serve both the tab list and the badge counters.
        Index("ix_role_apps_status_role", "status", "role_requested"),
        # "Does this applicant already have a pending one?" guard before
        # every INSERT — used by the duplicate-suppression check in the
        # router.
        Index("ix_role_apps_applicant_status", "applicant_id", "status"),
    )
