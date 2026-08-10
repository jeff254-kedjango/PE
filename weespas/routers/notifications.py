"""In-app notification inbox endpoints (the bell + dropdown).

Every endpoint is gated by `get_current_user` and operates ONLY on
`current_user.id` — there is no parameter that lets a caller read or mutate another
user's inbox. `mark_read` returns 404 (not 403) for a foreign/absent id so the API
never even confirms that someone else's notification exists.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from PE.weespas.core.database import get_db
from PE.weespas.models.user import User
from PE.weespas.services import notification_service
from PE.weespas.services.auth_service import get_current_user

router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationOut(BaseModel):
    id: str
    kind: str
    title: str
    body: str
    link: Optional[str] = None
    read_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class UnreadCount(BaseModel):
    count: int


class MarkAllResult(BaseModel):
    marked: int


@router.get("", response_model=List[NotificationOut])
def list_notifications(
    limit: int = Query(20, ge=1, le=100),
    before: Optional[datetime] = Query(
        None, description="Keyset cursor: pass the oldest created_at you've seen."
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Newest-first page of the caller's OWN notifications."""
    return notification_service.list_for_user(
        db, current_user.id, limit=limit, before=before
    )


@router.get("/unread-count", response_model=UnreadCount)
def unread_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Indexed count of the caller's unread notifications (the bell badge)."""
    return UnreadCount(count=notification_service.unread_count(db, current_user.id))


@router.post("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
def mark_read(
    notification_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark one of the caller's own notifications read. 404 if it isn't theirs."""
    ok = notification_service.mark_read(db, current_user.id, notification_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return None


@router.post("/read-all", response_model=MarkAllResult)
def mark_all_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark every unread notification of the caller read."""
    return MarkAllResult(marked=notification_service.mark_all_read(db, current_user.id))
