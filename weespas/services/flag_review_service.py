"""Flag-review queue service — the staff/admin side of the "flag a building" loop.

Creating the review is done INLINE in the flag's own transaction (create_for_flag
flushes, the caller commits) — deliberately NOT via Celery: the alert is a single
shared row, so an inline insert is atomic and cannot be lost to a downed worker. The
two hot reads (open-count badge, newest-first list) are indexed; "views" counts
DISTINCT people via a UNIQUE(review_id, user_id) insert-or-ignore.

Access is staff/admin only — enforced at the router. Acknowledger/viewer identity is
always the authenticated caller (never a request parameter), so it cannot be spoofed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from PE.weespas.models.flag_review import FlagReview, FlagReviewView
from PE.weespas.models.insar_link import StructuralFlag
from PE.weespas.models.user import User

# Review list statuses.
STATUS_OPEN = "open"
STATUS_ALL = "all"


def create_for_flag(db: Session, flag: StructuralFlag) -> FlagReview:
    """Create the review row for a freshly-recorded flag.

    Idempotent: if a review already exists for this flag (UNIQUE flag_id), the existing
    one is returned — a double-submit/re-run never spawns a second alert. Flushes (so
    the id is populated) but leaves the commit to the caller, so the review is atomic
    with the flag write.
    """
    existing = (
        db.query(FlagReview).filter(FlagReview.flag_id == flag.id).first()
    )
    if existing is not None:
        return existing
    row = FlagReview(flag_id=flag.id)
    db.add(row)
    db.flush()
    return row


def open_count(db: Session) -> int:
    """Indexed count of unseen reviews — the staff/admin badge."""
    return (
        db.query(func.count(FlagReview.id))
        .filter(FlagReview.seen_at.is_(None))
        .scalar()
        or 0
    )


class ReviewRecord:
    """Flat, enriched view of one review — exactly the fields the staff queue shows.
    Plain object (not an ORM row) so the router serialises it without lazy loads."""

    __slots__ = (
        "id", "flag_id", "aoi_code", "insar_building_id", "state", "source",
        "note", "observed_at", "flagged_at", "flagged_by_id", "flagged_by_name",
        "seen", "seen_at", "seen_by_id", "seen_by_name", "views",
    )

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


def _enriched_query(db: Session):
    """The shared SINGLE-query join (flag + flagger + acknowledger + distinct-view
    count) used by both the list and the single-record fetch — no per-row N+1."""
    flagger = aliased(User)
    seer = aliased(User)
    # Distinct-view count per review, computed in one grouped subquery so the outer
    # query stays a single indexed scan + joins (no N+1, O(page) work).
    views_sq = (
        db.query(
            FlagReviewView.review_id.label("review_id"),
            func.count(FlagReviewView.id).label("views"),
        )
        .group_by(FlagReviewView.review_id)
        .subquery()
    )
    q = (
        db.query(FlagReview, StructuralFlag, flagger, seer, views_sq.c.views)
        .join(StructuralFlag, StructuralFlag.id == FlagReview.flag_id)
        .outerjoin(flagger, flagger.id == StructuralFlag.granted_by)
        .outerjoin(seer, seer.id == FlagReview.seen_by_id)
        .outerjoin(views_sq, views_sq.c.review_id == FlagReview.id)
    )
    return q


def _to_record(row) -> ReviewRecord:
    review, flag, fl, se, views = row
    return ReviewRecord(
        id=review.id,
        flag_id=flag.id,
        aoi_code=flag.aoi_code,
        insar_building_id=flag.insar_building_id,
        state=flag.state,
        source=flag.source,
        note=flag.note,
        observed_at=flag.observed_at,
        flagged_at=flag.created_at,
        flagged_by_id=flag.granted_by,
        flagged_by_name=fl.name if fl is not None else None,
        seen=review.seen_at is not None,
        seen_at=review.seen_at,
        seen_by_id=review.seen_by_id,
        seen_by_name=se.name if se is not None else None,
        views=int(views or 0),
    )


def get_record(db: Session, review_id: str) -> Optional[ReviewRecord]:
    """One enriched review by id (same single-query join as the list), or None."""
    row = _enriched_query(db).filter(FlagReview.id == review_id).first()
    return _to_record(row) if row is not None else None


def list_reviews(
    db: Session,
    *,
    status: str = STATUS_OPEN,
    limit: int = 20,
    before: Optional[datetime] = None,
) -> List[ReviewRecord]:
    """Newest-first page of reviews, each enriched with the flag + flagger + acknowledger
    + distinct-view count, in a SINGLE query (no per-row N+1).

    Keyset-paginated by `created_at` (pass the oldest you've seen as `before`). `status`
    is 'open' (unseen only) or 'all'. `limit` is hard-capped at 100.
    """
    limit = max(1, min(limit, 100))
    q = _enriched_query(db)
    if status == STATUS_OPEN:
        q = q.filter(FlagReview.seen_at.is_(None))
    if before is not None:
        q = q.filter(FlagReview.created_at < before)
    rows = q.order_by(FlagReview.created_at.desc()).limit(limit).all()
    return [_to_record(r) for r in rows]


def record_view(db: Session, *, review_id: str, user_id: str) -> int:
    """Record that `user_id` viewed `review_id`, counting DISTINCT people. Insert-or-
    ignore via a savepoint so a repeat view (UNIQUE violation) is a silent no-op and
    never poisons the caller's transaction. Returns the current distinct-view count, or
    -1 if the review does not exist."""
    if db.query(FlagReview.id).filter(FlagReview.id == review_id).first() is None:
        return -1
    try:
        with db.begin_nested():
            db.add(FlagReviewView(review_id=review_id, user_id=user_id))
            db.flush()
    except IntegrityError:
        # Already viewed by this user (UNIQUE) — distinct count is unchanged.
        pass
    db.commit()
    return (
        db.query(func.count(FlagReviewView.id))
        .filter(FlagReviewView.review_id == review_id)
        .scalar()
        or 0
    )


def mark_seen(db: Session, *, review_id: str, user_id: str) -> Optional[FlagReview]:
    """Mark a review seen as `user_id`, FIRST-WINS and immutable.

    The acknowledger is set only on the transition from open→seen (WHERE seen_at IS
    NULL); a later 'mark seen' by anyone else leaves the original acknowledger intact.
    Also records the actor as a viewer. Returns the review (already-seen or just-seen),
    or None if the id does not exist.
    """
    review = db.query(FlagReview).filter(FlagReview.id == review_id).first()
    if review is None:
        return None
    if review.seen_at is None:
        # Atomic first-wins claim at the DB: only the row still NULL is updated.
        claimed = (
            db.query(FlagReview)
            .filter(FlagReview.id == review_id, FlagReview.seen_at.is_(None))
            .update(
                {
                    FlagReview.seen_by_id: user_id,
                    FlagReview.seen_at: datetime.now(timezone.utc),
                },
                synchronize_session=False,
            )
        )
        db.commit()
        if claimed:
            db.refresh(review)
    # The acknowledger is also a viewer (own commit inside).
    record_view(db, review_id=review_id, user_id=user_id)
    db.refresh(review)
    return review
