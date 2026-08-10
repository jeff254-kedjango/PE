"""Session-side endpoints — currently just /sessions/geo to record the
browser-supplied geolocation onto the active UserSession row.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from PE.weespas.core.database import get_db
from PE.weespas.models.analytics import UserSession

router = APIRouter(prefix="/sessions", tags=["Sessions"])


class GeoIn(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)


@router.post("/geo")
def update_session_geo(
    body: GeoIn,
    request: Request,
    db: Session = Depends(get_db),
):
    sid = getattr(request.state, "session_id", None)
    if not sid:
        raise HTTPException(status_code=400, detail="No active session")

    sess = db.query(UserSession).filter(UserSession.id == sid).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    sess.geo_lat = body.lat
    sess.geo_lng = body.lng
    sess.geo_source = "browser"
    db.commit()
    return {"ok": True}
