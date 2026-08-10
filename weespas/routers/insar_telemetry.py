"""InSAR commercial-usage telemetry sink (the metering half of the bridge).

This is the server-TRUSTED counterpart to routers/metering.py. The narrow
/metering/event endpoint deliberately REFUSES insar_* actions (a browser must not be
able to forge the very signals that drive company-detection). Instead the InSAR
frontend authenticates with a telemetry-SCOPED token (minted by /insar/session-token),
which proves "this is user X" without granting access to anything else — so the
insar_building_view / insar_export it reports are attributable and unforgeable.

Best-effort by contract: events are dispatched via safe_delay (never block the caller)
and the endpoint always returns 202. session_id is None by design — these POSTs are
cross-origin from the stateless InSAR SPA and carry no weespas_session cookie; the
scorer keys on user_id + aoi_code, so a null session is fine (and the session
middleware skips this path to avoid minting throwaway rows).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from PE.weespas.services.auth_service import require_insar_telemetry_token
from PE.weespas.services.celery_helpers import safe_delay
from PE.weespas.services.metering_service import record_metering_event_async
from PE.weespas.models.metering import (
    EVENT_INSAR_BUILDING_VIEW, EVENT_INSAR_EXPORT, EVENT_INSAR_BUNDLE_FETCH,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/insar-telemetry", tags=["insar-telemetry"])

# The ONLY actions this scoped token may emit. A telemetry token can do nothing but log
# these — it can't reach money (checkout_*) or reveal events. insar_bundle_fetch is the
# server-side access signal the InSAR data API reports on a full bundle pull (so direct
# scraping is visible to §8, not just frontend clicks).
_INSAR_ACTIONS = {EVENT_INSAR_BUILDING_VIEW, EVENT_INSAR_EXPORT, EVENT_INSAR_BUNDLE_FETCH}


class InsarEvent(BaseModel):
    action: str
    building_id: Optional[int] = None   # the InSAR building viewed
    aoi_code: Optional[str] = None      # the AOI (drives the breadth signal)
    count: Optional[int] = None         # rows in an export (drives export weight)


@router.post("/event", status_code=status.HTTP_202_ACCEPTED)
def report_event(
    body: InsarEvent,
    user_id: str = Depends(require_insar_telemetry_token),
) -> dict:
    """Record one InSAR commercial-usage event for the token's user. Silently ignores
    any action outside the InSAR set — never an error back to the map UI."""
    if body.action not in _INSAR_ACTIONS:
        return {"accepted": False}
    safe_delay(
        record_metering_event_async,
        body.action,
        user_id=user_id,
        session_id=None,
        target_ref=str(body.building_id) if body.building_id is not None else None,
        aoi_code=body.aoi_code,
        meta=str(body.count) if body.count is not None else None,
    )
    return {"accepted": True}
