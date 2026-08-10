"""Client-emitted metering events (billing_architecture.md §8.1).

The server emits the events it owns (reveal, checkout_*) directly. A few signals are
only observable in the browser — opening the map, tapping a directions link — so the
FE reports them here. This endpoint is deliberately NARROW: it accepts ONLY the
client-safe action vocabulary, so a client can never forge a money event
(checkout_paid) or an InSAR commercial event to skew its own company score the other
way. Everything is best-effort and returns 202 regardless.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from PE.weespas.core.database import get_db
from PE.weespas.models.user import User
from PE.weespas.services.auth_service import get_current_user_optional
from PE.weespas.services.celery_helpers import safe_delay
from PE.weespas.services.metering_service import record_metering_event_async
from PE.weespas.models.metering import EVENT_MAP_OPEN, EVENT_DIRECTIONS_OPEN

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/metering", tags=["metering"])

# Only these may be reported by a client. Money + InSAR commercial events are
# server-emitted and NOT acceptable here (anti-forgery).
_CLIENT_ACTIONS = {EVENT_MAP_OPEN, EVENT_DIRECTIONS_OPEN}


class ClientEvent(BaseModel):
    action: str
    listing_id: Optional[str] = None


@router.post("/event", status_code=status.HTTP_202_ACCEPTED)
def report_event(
    body: ClientEvent,
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
) -> dict:
    """Record a client-observed behavioural event. Anonymous-safe (session-anchored).
    Silently ignores actions outside the client-safe set — never an error to the UI."""
    if body.action not in _CLIENT_ACTIONS:
        return {"accepted": False}
    safe_delay(
        record_metering_event_async,
        body.action,
        user_id=getattr(user, "id", None),
        session_id=getattr(request.state, "session_id", None),
        target_ref=body.listing_id,
    )
    return {"accepted": True}
