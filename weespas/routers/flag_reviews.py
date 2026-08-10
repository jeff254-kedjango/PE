"""Flag-review queue endpoints — the staff/admin side of "flag a building".

Every route is gated by `require_staff` (staff/admin). The acknowledger/viewer is
always `current_user` (taken from the auth token), never a request parameter, so
identity cannot be spoofed. The flagger↔building↔note join is sensitive (the §4.2
corruption surface), which is why the whole router is staff-gated and never public.

A non-existent review id returns 404 (the API never confirms whether some other id
exists) — consistent with the notifications router.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from PE.weespas.core.database import get_db
from PE.weespas.models.user import User
from PE.weespas.services import flag_review_service
from PE.weespas.services.flag_review_service import STATUS_OPEN
from PE.weespas.services.auth_service import require_staff

router = APIRouter(prefix="/flag-reviews", tags=["flag-reviews"])


class FlagReviewOut(BaseModel):
    id: str
    flag_id: str
    aoi_code: str
    insar_building_id: int
    state: int
    source: str
    note: Optional[str] = None
    observed_at: Optional[date] = None
    flagged_at: Optional[datetime] = None
    flagged_by_id: Optional[str] = None
    flagged_by_name: Optional[str] = None
    seen: bool
    seen_at: Optional[datetime] = None
    seen_by_id: Optional[str] = None
    seen_by_name: Optional[str] = None
    views: int


class OpenCount(BaseModel):
    count: int


class ViewResult(BaseModel):
    views: int


@router.get("", response_model=List[FlagReviewOut])
def list_flag_reviews(
    status_filter: str = Query(
        STATUS_OPEN, alias="status", pattern="^(open|all)$",
        description="'open' (unseen only) or 'all'.",
    ),
    limit: int = Query(20, ge=1, le=100),
    before: Optional[datetime] = Query(
        None, description="Keyset cursor: pass the oldest created_at you've seen."
    ),
    db: Session = Depends(get_db),
    _staff: User = Depends(require_staff),
):
    """Newest-first page of flag reviews, enriched with flagger, acknowledger, and
    distinct-view count. Staff/admin only."""
    return flag_review_service.list_reviews(
        db, status=status_filter, limit=limit, before=before
    )


@router.get("/open-count", response_model=OpenCount)
def open_count(
    db: Session = Depends(get_db),
    _staff: User = Depends(require_staff),
):
    """Indexed count of unseen reviews — the staff/admin bell badge."""
    return OpenCount(count=flag_review_service.open_count(db))


@router.post("/{review_id}/seen", response_model=FlagReviewOut)
def mark_seen(
    review_id: str,
    db: Session = Depends(get_db),
    staff: User = Depends(require_staff),
):
    """Mark a review seen as the caller (first-wins, immutable). 404 if it doesn't exist."""
    review = flag_review_service.mark_seen(db, review_id=review_id, user_id=staff.id)
    if review is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    # Re-read the enriched record so the response carries flagger/acknowledger/views.
    rec = flag_review_service.get_record(db, review_id)
    if rec is None:  # pragma: no cover - mark_seen already proved it exists
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return rec


@router.post("/{review_id}/view", response_model=ViewResult)
def record_view(
    review_id: str,
    db: Session = Depends(get_db),
    staff: User = Depends(require_staff),
):
    """Record that the caller viewed a review (distinct people). 404 if it doesn't exist."""
    views = flag_review_service.record_view(db, review_id=review_id, user_id=staff.id)
    if views < 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return ViewResult(views=views)
