"""In-app notification inbox service.

Every read/write is SCOPED BY user_id in the WHERE clause — there is no code path that
returns or mutates a row by id alone. A user can never see or touch another user's
inbox even by guessing an id (the cardinal security rule for this feature).

All queries hit the (user_id, read_at) / (user_id, created_at) composite indexes, so
the unread-count is an indexed count and the inbox list is a bounded keyset scan — no
full-table scans, no per-user N+1.

The `create` helper flushes but does NOT commit, so a caller (e.g. the verify task) can
commit the notification atomically with whatever else it's writing in the same txn.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from PE.weespas.models.notification import Notification, KIND_LISTING_VERIFICATION


def create(
    db: Session,
    *,
    user_id: str,
    title: str,
    body: str,
    kind: str = KIND_LISTING_VERIFICATION,
    link: Optional[str] = None,
) -> Notification:
    """Add a notification row for a user. Flushes (so the id is populated) but leaves
    the commit to the caller, allowing it to be atomic with sibling writes."""
    row = Notification(
        user_id=user_id, kind=kind, title=title, body=body, link=link
    )
    db.add(row)
    db.flush()
    return row


def list_for_user(
    db: Session, user_id: str, *, limit: int = 20, before: Optional[datetime] = None
) -> List[Notification]:
    """Newest-first inbox page for one user. Keyset-paginated by created_at (pass the
    oldest created_at you've seen as `before` to fetch the next page). Bounded by limit."""
    limit = max(1, min(limit, 100))  # hard cap — never let a caller request unbounded
    q = db.query(Notification).filter(Notification.user_id == user_id)
    if before is not None:
        q = q.filter(Notification.created_at < before)
    return q.order_by(Notification.created_at.desc()).limit(limit).all()


def unread_count(db: Session, user_id: str) -> int:
    """O(log n) indexed count of this user's unread notifications."""
    return (
        db.query(func.count(Notification.id))
        .filter(Notification.user_id == user_id, Notification.read_at.is_(None))
        .scalar()
        or 0
    )


def mark_read(db: Session, user_id: str, notification_id: str) -> bool:
    """Mark ONE of the user's own notifications read. Returns False if it isn't theirs
    (or doesn't exist) — the user_id filter is what makes id-guessing inert."""
    row = (
        db.query(Notification)
        .filter(Notification.id == notification_id, Notification.user_id == user_id)
        .first()
    )
    if row is None:
        return False
    if row.read_at is None:
        row.read_at = datetime.now(timezone.utc)
        db.commit()
    return True


def mark_all_read(db: Session, user_id: str) -> int:
    """Mark all of a user's unread notifications read. Returns the number flipped."""
    n = (
        db.query(Notification)
        .filter(Notification.user_id == user_id, Notification.read_at.is_(None))
        .update({Notification.read_at: datetime.now(timezone.utc)},
                synchronize_session=False)
    )
    db.commit()
    return n
