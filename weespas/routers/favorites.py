"""Favorites — auth-gated CRUD.

Replaces the localStorage-only flow on the frontend. Powers the favorites
signal for analytics scoring.
"""
from __future__ import annotations

from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from PE.weespas.core.database import get_db
from PE.weespas.models.analytics import Favorite
from PE.weespas.models.property import Property
from PE.weespas.models.user import User
from PE.weespas.services.auth_service import get_current_user
from PE.weespas.services.personalization_tasks import invalidate_user_feed

router = APIRouter(prefix="/favorites", tags=["Favorites"])


def _enqueue_feed_invalidation(user_id: str) -> None:
    """Fire-and-forget Celery dispatch — invalidate, then prewarm.

    Per audit §4.2: chaining the prewarm onto the invalidation keeps p99
    warm after a write. Without the chain, the next request from this user
    pays the full miss. Falls back to in-process invalidation if the broker
    is unreachable so cache staleness stays bounded.
    """
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


class FavoriteIn(BaseModel):
    property_id: str = Field(..., min_length=1)


class FavoriteOut(BaseModel):
    property_id: str

    class Config:
        from_attributes = True


@router.get("/me", response_model=List[FavoriteOut])
def list_my_favorites(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = db.query(Favorite).filter(Favorite.user_id == user.id).all()
    return [FavoriteOut(property_id=r.property_id) for r in rows]


@router.post("", response_model=FavoriteOut, status_code=201)
def add_favorite(
    body: FavoriteIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Validate property exists
    if not db.query(Property.id).filter(Property.id == body.property_id).first():
        raise HTTPException(status_code=404, detail="Property not found")

    existing = (
        db.query(Favorite)
        .filter(Favorite.user_id == user.id, Favorite.property_id == body.property_id)
        .first()
    )
    if existing:
        return FavoriteOut(property_id=existing.property_id)

    fav = Favorite(user_id=user.id, property_id=body.property_id)
    db.add(fav)
    db.commit()
    _enqueue_feed_invalidation(user.id)
    return FavoriteOut(property_id=fav.property_id)


@router.delete("/{property_id}", status_code=204)
def remove_favorite(
    property_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    deleted = (
        db.query(Favorite)
        .filter(Favorite.user_id == user.id, Favorite.property_id == property_id)
        .delete()
    )
    db.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="Not in favorites")
    _enqueue_feed_invalidation(user.id)
    return None


class FavoriteBatchIn(BaseModel):
    property_ids: List[str] = Field(default_factory=list)


@router.post("/migrate", response_model=List[FavoriteOut])
def migrate_local_favorites(
    body: FavoriteBatchIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """One-shot bulk import of localStorage favorites after first login."""
    if not body.property_ids:
        return []
    valid = {
        pid for (pid,) in db.query(Property.id).filter(Property.id.in_(body.property_ids)).all()
    }
    existing = {
        pid for (pid,) in db.query(Favorite.property_id).filter(
            Favorite.user_id == user.id, Favorite.property_id.in_(valid)
        ).all()
    }
    to_add = valid - existing
    for pid in to_add:
        db.add(Favorite(user_id=user.id, property_id=pid))
    if to_add:
        db.commit()
        _enqueue_feed_invalidation(user.id)
    return [FavoriteOut(property_id=p) for p in (existing | to_add)]
