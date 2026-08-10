"""Saved searches CRUD — Phase 3 of Profile_Architecture.md.

Mounted under /me/saved-searches for symmetry with the rest of the
user-self resources in routers.me. Kept in its own router module so the
me.py file doesn't balloon and so saved_searches has clean import
boundaries (its model + schema only).

Performance posture:
- List endpoint caps at 25 rows per user; the index on
  (user_id, last_used_at DESC) makes it a sub-2ms query.
- No Redis cache — each user's list is tiny and stale presets would
  surprise users mid-edit.
- Apply ("touch") is a single UPDATE; no read-modify-write on the
  filters JSON.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from PE.weespas.core.database import get_db
from PE.weespas.models.saved_search import SavedSearch
from PE.weespas.models.user import User
from PE.weespas.services.auth_service import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/me/saved-searches", tags=["Saved Searches"])

MAX_PER_USER = 25  # soft cap; one user with 25 presets is already power-user territory


# ── Schemas ─────────────────────────────────────────────────────────
class SavedSearchCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    filters: dict = Field(default_factory=dict)


class SavedSearchUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=80)
    filters: Optional[dict] = None
    touch: Optional[bool] = Field(
        None,
        description="If true, bump last_used_at to NOW. Used when a preset is applied.",
    )


class SavedSearchOut(BaseModel):
    id: str
    name: str
    filters: dict
    created_at: datetime
    last_used_at: Optional[datetime] = None

    class Config:
        from_attributes = True


def _to_out(row: SavedSearch) -> SavedSearchOut:
    try:
        filters = json.loads(row.filters) if row.filters else {}
    except Exception:
        filters = {}
    return SavedSearchOut(
        id=row.id,
        name=row.name,
        filters=filters,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
    )


# ── Routes ──────────────────────────────────────────────────────────
@router.get(
    "",
    response_model=List[SavedSearchOut],
    summary="List the user's saved searches (most recently used first)",
)
def list_saved_searches(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = (
        db.query(SavedSearch)
        .filter(SavedSearch.user_id == user.id)
        .order_by(SavedSearch.last_used_at.desc().nullslast())
        .limit(MAX_PER_USER)
        .all()
    )
    return [_to_out(r) for r in rows]


@router.post(
    "",
    response_model=SavedSearchOut,
    status_code=status.HTTP_201_CREATED,
    summary="Save a new search preset",
)
def create_saved_search(
    body: SavedSearchCreate = Body(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Soft cap — block creation once the user is over the limit so the
    # list query stays bounded. The user can delete an old one to make room.
    count = db.query(SavedSearch).filter(SavedSearch.user_id == user.id).count()
    if count >= MAX_PER_USER:
        raise HTTPException(
            status_code=409,
            detail=f"Maximum of {MAX_PER_USER} saved searches reached — delete one to add another.",
        )

    row = SavedSearch(
        user_id=user.id,
        name=body.name.strip(),
        filters=json.dumps(body.filters or {}, separators=(",", ":")),
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="A saved search with that name already exists")
    db.refresh(row)
    logger.info("saved_search.created user_id=%s id=%s name=%s", user.id, row.id, row.name)
    return _to_out(row)


@router.patch(
    "/{search_id}",
    response_model=SavedSearchOut,
    summary="Rename, update filters, or touch last_used_at on a saved search",
)
def update_saved_search(
    search_id: str,
    body: SavedSearchUpdate = Body(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = (
        db.query(SavedSearch)
        .filter(SavedSearch.id == search_id, SavedSearch.user_id == user.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Saved search not found")

    if body.name is not None:
        row.name = body.name.strip()
    if body.filters is not None:
        row.filters = json.dumps(body.filters, separators=(",", ":"))
    if body.touch:
        row.last_used_at = datetime.now(timezone.utc)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="A saved search with that name already exists")
    db.refresh(row)
    return _to_out(row)


@router.delete(
    "/{search_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a saved search",
)
def delete_saved_search(
    search_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    deleted = (
        db.query(SavedSearch)
        .filter(SavedSearch.id == search_id, SavedSearch.user_id == user.id)
        .delete()
    )
    db.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="Saved search not found")
    return None
