"""Staff/admin review queue for recorded structural flags.

When a certifier flags a building (`structural_flag`), one `FlagReview` row is created
in the SAME transaction (see services.structural_flag_service.record_flag). It is the
shared, group-addressed alert: every staff/admin sees it, the badge keeps nagging until
ANY ONE of them marks it seen, and the acknowledger's identity is recorded.

Why a separate table (not the per-user `notifications` inbox): this is a SINGLE shared
record per flag with a first-wins group acknowledgement and a distinct-viewer count —
none of which the one-row-per-user inbox can express. It references `structural_flag`
rather than copying the flagger/note/building, so there is one source of truth.

Two tables:
  - FlagReview         one row per flag; holds the open/seen state + acknowledger.
  - FlagReviewView     one row per (review, viewer); UNIQUE makes "views" = distinct
                       people (not raw opens) an indexed insert-or-ignore, and lets us
                       show WHO viewed, not just a number.

Indexing matches the two hot reads:
  - open-count badge → (seen_at)          partial-ish indexed count of unseen rows
  - newest-first list → (created_at)       bounded keyset scan
  - distinct views   → UNIQUE(review_id, user_id) + (review_id) for the per-review count
"""
from sqlalchemy import (
    Column, String, DateTime, ForeignKey, Index, UniqueConstraint,
)
from sqlalchemy.sql import func
import uuid

from PE.weespas.core.database import Base


class FlagReview(Base):
    """One staff/admin review record for a single structural flag."""
    __tablename__ = "flag_review"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    # One review per flag. UNIQUE makes create_for_flag idempotent at the DB level —
    # a double-submit/re-run can never spawn a second alert for the same flag.
    flag_id = Column(
        String, ForeignKey("structural_flag.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True,
    )
    # NULL = open/unseen (the badge counts these). Set to the FIRST staff/admin who
    # acknowledged it; SET NULL on user delete keeps the review row + its history.
    seen_by_id = Column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    seen_at = Column(DateTime(timezone=True), nullable=True)
    # NB: no column-level index=True — the named __table_args__ index below is the
    # single index on created_at (avoids a redundant duplicate index).
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # Open-count badge: WHERE seen_at IS NULL — covered by this index.
        Index("idx_flag_review_seen_at", "seen_at"),
        # Newest-first queue list: ORDER BY created_at DESC.
        Index("idx_flag_review_created", "created_at"),
    )


class FlagReviewView(Base):
    """A distinct viewer of one review. UNIQUE(review_id, user_id) ⇒ 'views' counts
    PEOPLE, not opens; the row also answers 'who looked at this'."""
    __tablename__ = "flag_review_view"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    # No column-level index=True on review_id: the composite UNIQUE below has
    # review_id as its leftmost column, so WHERE review_id=? (the per-review distinct
    # count) is already served by it — a separate index would be redundant.
    review_id = Column(
        String, ForeignKey("flag_review.id", ondelete="CASCADE"), nullable=False,
    )
    user_id = Column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    viewed_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # One view row per person per review — the distinct-people guarantee. Its
        # leftmost column (review_id) also covers the per-review count query.
        UniqueConstraint("review_id", "user_id", name="uq_flag_review_view_review_user"),
    )
