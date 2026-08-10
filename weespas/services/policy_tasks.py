"""Company-detection beat job (billing_architecture.md §8.2).

Recomputes the commercial-likelihood score for users who have been commercially
active recently, writing the verdict into UserUsageProfile so the request-path
policy gate stays O(1). Runs on a slow cadence — company-scale use is a rolling
pattern, not a per-request decision.

Scoping (don't scan every user): we only recompute profiles for users who emitted
at least one COMMERCIAL event inside the score window. Everyone else is, by
definition, below threshold and is left FREE by the gate's default.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from PE.weespas.core.celery_app import celery_app
from PE.weespas.core.config import settings
from PE.weespas.core.database import SessionLocal
from PE.weespas.models.user import User
from PE.weespas.models.metering import MeteringEvent, COMMERCIAL_EVENT_ACTIONS
from PE.weespas.services import policy_engine

logger = logging.getLogger(__name__)


@celery_app.task(name="policy.recompute_usage_profiles")
def recompute_usage_profiles() -> dict:
    """Recompute UserUsageProfile for recently-commercially-active users.

    Returns a small summary {scanned, metered} for observability. Bounded work:
    only users with a commercial event in the window are considered."""
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=settings.company_score_window_days)

        # Distinct user_ids with a commercial event in the window (skip anonymous rows).
        user_ids = [
            uid for (uid,) in (
                db.query(MeteringEvent.user_id)
                .filter(
                    MeteringEvent.user_id.isnot(None),
                    MeteringEvent.created_at >= cutoff,
                    MeteringEvent.action.in_(COMMERCIAL_EVENT_ACTIONS),
                )
                .distinct()
                .all()
            )
            if uid
        ]

        scanned = 0
        metered = 0
        for uid in user_ids:
            user = db.get(User, uid)
            if user is None:
                continue
            breakdown = policy_engine.compute_score(db, user, now=now)
            profile = policy_engine.upsert_profile(db, user, breakdown)
            scanned += 1
            if profile.is_metered:
                metered += 1

        logger.info("recompute_usage_profiles: scanned=%d metered=%d", scanned, metered)
        return {"scanned": scanned, "metered": metered}
    finally:
        db.close()
