"""Metering service — records behavioural events for the company-detection line.

billing_architecture.md §8.1. This is the *write* half of the second revenue line:
every commercially-meaningful action (a reveal, a bulk InSAR view, an export) drops
one MeteringEvent row. The §8.2 policy engine + the beat job in
`services/policy_tasks.py` read these rows to decide who is a company.

Design rules (match the rest of the codebase):
  - NEVER on the request thread's critical path. Call sites dispatch through
    `safe_delay(record_metering_event_async, ...)` so a metering hiccup (or a Redis/
    broker outage) can never fail a paid reveal — the event is best-effort.
  - Store the MINIMUM (DPA-2019): an action label + ids + timestamp. No message body.
  - Validate the action against the known vocabulary so a typo can't poison the
    scorer with an unknown bucket.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from PE.weespas.core.celery_app import celery_app
from PE.weespas.core.database import SessionLocal
from PE.weespas.models.metering import MeteringEvent, VALID_EVENT_ACTIONS

logger = logging.getLogger(__name__)


def record_event(
    db: Session,
    *,
    action: str,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    target_ref: Optional[str] = None,
    aoi_code: Optional[str] = None,
    meta: Optional[str] = None,
) -> Optional[MeteringEvent]:
    """Persist one metering event. Returns the row, or None if the action is unknown.

    Unknown actions are dropped (logged), never raised — a caller passing a bad
    label must not break the action it was metering.
    """
    if action not in VALID_EVENT_ACTIONS:
        logger.warning("metering: dropping event with unknown action %r", action)
        return None
    row = MeteringEvent(
        action=action,
        user_id=user_id,
        session_id=session_id,
        target_ref=target_ref,
        aoi_code=aoi_code,
        meta=meta,
    )
    db.add(row)
    db.commit()
    return row


@celery_app.task(
    name="metering.record_event",
    ignore_result=True,
    acks_late=False,
    # A lost metering row is acceptable — it only nudges a company-likelihood score
    # that is recomputed over a rolling window anyway. Never block the request for it.
)
def record_metering_event_async(
    action: str,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    target_ref: Optional[str] = None,
    aoi_code: Optional[str] = None,
    meta: Optional[str] = None,
) -> None:
    """Celery write-offload wrapper. Opens its own session so the request's DB
    session never escapes the route handler (same pattern as analytics_tasks)."""
    db = SessionLocal()
    try:
        record_event(
            db,
            action=action,
            user_id=user_id,
            session_id=session_id,
            target_ref=target_ref,
            aoi_code=aoi_code,
            meta=meta,
        )
    except Exception as exc:  # pragma: no cover - defensive; metering is best-effort
        logger.warning("record_metering_event_async(%s) failed: %s", action, exc)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()
