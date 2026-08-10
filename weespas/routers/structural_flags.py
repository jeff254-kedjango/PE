"""Structural-flag API — the manual-entry seam for the engineer/authority sensor.

A professional (engineer) or authority records a structural judgement for an InSAR
building; staff/admin may act on their behalf. The InSAR build reads these and fuses
them into the collapse score. No notification is sent here (that is P4c).

All routes are auth-gated by `require_certifier` (professional/authority/staff/admin).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from PE.weespas.core.database import get_db
from PE.weespas.models.user import User
from PE.weespas.models.insar_link import (
    FLAG_CLEARED, FLAG_UNSAFE, FLAG_AUTH_UNSAFE,
)
from PE.weespas.services.auth_service import require_certifier, require_staff
from PE.weespas.services import structural_flag_service as svc
from PE.weespas.services import structural_flag_export as export_svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/structural-flags", tags=["structural-flags"])


class FlagCreate(BaseModel):
    aoi_code: str = Field(..., max_length=64)
    insar_building_id: int
    # 1=CLEARED, 2=UNSAFE, 3=AUTH_UNSAFE (mirrors InSAR STRUCT_*). NONE is not recordable.
    state: int = Field(..., ge=FLAG_CLEARED, le=FLAG_AUTH_UNSAFE)
    source: str = Field(..., description="'engineer' | 'authority'")
    observed_at: Optional[date] = None
    note: Optional[str] = Field(None, max_length=2000)


class FlagOut(BaseModel):
    id: str
    aoi_code: str
    insar_building_id: int
    state: int
    source: str
    observed_at: Optional[date]
    note: Optional[str]
    granted_by: Optional[str]

    model_config = ConfigDict(from_attributes=True)


@router.post("", response_model=FlagOut, status_code=status.HTTP_201_CREATED)
def create_flag(
    body: FlagCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_certifier),
) -> FlagOut:
    """Record a structural judgement for one building. Role + state rules enforced
    in the service (only an authority may set AUTH_UNSAFE)."""
    flag = svc.record_flag(
        db,
        actor=actor,
        aoi_code=body.aoi_code,
        insar_building_id=body.insar_building_id,
        state=body.state,
        source=body.source,
        observed_at=body.observed_at,
        note=body.note,
    )
    # Best-effort sync to the InSAR build's flag export, then ask the InSAR pipeline
    # to (debounced) rebuild this AOI so the new flag reaches the score. The flag is
    # already durably recorded above; if either step is unreachable we log and move on
    # rather than fail the write (the operator can re-run POST /export and rebuild, and
    # the next scheduled export will pick it up anyway). Both steps are no-ops when not
    # configured (no export dir / no control-API URL).
    try:
        path = export_svc.export_aoi(db, body.aoi_code)
        if path is not None:
            # Only trigger a rebuild once the file is actually on disk — a rebuild
            # with no fresh export would just re-score the old flags.
            export_svc.trigger_rebuild(body.aoi_code)
    except Exception:  # pragma: no cover - defensive; never block a recorded flag
        logger.exception("structural-flag export/trigger failed for aoi=%s (flag still "
                         "recorded)", body.aoi_code)
    return FlagOut.model_validate(flag)


@router.post("/export", status_code=status.HTTP_200_OK)
def export_flags(
    aoi_code: Optional[str] = None,
    db: Session = Depends(get_db),
    actor: User = Depends(require_staff),
) -> dict:
    """Re-export the structural flags to the InSAR build's flag dir. One AOI if
    `aoi_code` is given, else all AOIs that have flags. Staff/admin only.

    Returns the paths written (empty if export is disabled — no dir configured)."""
    if aoi_code:
        path = export_svc.export_aoi(db, aoi_code)
        written = [str(path)] if path else []
    else:
        written = [str(p) for p in export_svc.export_all(db)]
    return {"exported": written, "count": len(written)}


@router.get("/{aoi_code}/{insar_building_id}", response_model=Optional[FlagOut])
def get_latest_flag(
    aoi_code: str,
    insar_building_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(require_certifier),
) -> Optional[FlagOut]:
    """The most-recent flag for a building (what the InSAR build would fuse)."""
    flag = svc.latest_flag_for_building(
        db, aoi_code=aoi_code, insar_building_id=insar_building_id
    )
    return FlagOut.model_validate(flag) if flag else None
