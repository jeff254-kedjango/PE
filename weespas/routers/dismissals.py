"""Property dismissals — explicit 'not interested' signal.

Strong negative signal for the personalized feed: dismissed properties are
excluded from the user's home feed entirely.
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from PE.weespas.core.database import get_db
from PE.weespas.models.analytics import PropertyDismissal
from PE.weespas.models.property import Property
from PE.weespas.models.user import User
from PE.weespas.services.auth_service import get_current_user
from PE.weespas.services.personalization_tasks import invalidate_user_feed

router = APIRouter(prefix="/dismissals", tags=["Dismissals"])


def _enqueue_feed_invalidation(user_id: str) -> None:
    """Chain: invalidate → prewarm. See audit §4.2 — keeps p99 warm after writes."""
    try:
        from celery import chain
        from PE.weespas.services.property_tasks import prewarm_user_feed
        chain(
            invalidate_user_feed.s(user_id),
            # .si() = immutable signature: drops the chain's forwarded return
            # value so prewarm_user_feed receives exactly one positional arg.
            # Without this, Celery rejects with TypeError (2 args given, 1 expected).
            prewarm_user_feed.si(user_id),
        ).apply_async()
    except Exception:
        from PE.weespas.services.personalization import PersonalFeedService
        PersonalFeedService.invalidate(user_id)


class DismissalOut(BaseModel):
    property_id: str

    class Config:
        from_attributes = True


@router.get("/me", response_model=List[DismissalOut])
def list_my_dismissals(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = db.query(PropertyDismissal.property_id).filter(PropertyDismissal.user_id == user.id).all()
    return [DismissalOut(property_id=pid) for (pid,) in rows]


@router.post("/{property_id}", response_model=DismissalOut, status_code=201)
def dismiss_property(
    property_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not db.query(Property.id).filter(Property.id == property_id).first():
        raise HTTPException(status_code=404, detail="Property not found")

    existing = (
        db.query(PropertyDismissal)
        .filter(PropertyDismissal.user_id == user.id, PropertyDismissal.property_id == property_id)
        .first()
    )
    if existing:
        return DismissalOut(property_id=existing.property_id)

    row = PropertyDismissal(user_id=user.id, property_id=property_id)
    db.add(row)
    db.commit()
    _enqueue_feed_invalidation(user.id)
    return DismissalOut(property_id=row.property_id)


@router.delete("/{property_id}", status_code=204)
def undo_dismissal(
    property_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    deleted = (
        db.query(PropertyDismissal)
        .filter(PropertyDismissal.user_id == user.id, PropertyDismissal.property_id == property_id)
        .delete()
    )
    db.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="Not dismissed")
    _enqueue_feed_invalidation(user.id)
    return None
