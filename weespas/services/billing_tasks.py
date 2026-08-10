"""Billing Celery tasks — the reconciliation sweep for lost STK callbacks.

PE/billing_architecture.md §4 (debit-but-callback-fails). The M-Pesa callback is the
fast path, but it can be lost (no public URL in dev, a transient outage, Safaricom
not retrying). This periodic sweep asks Daraja (STK Query) about every still-pending
intent in a bounded age window and settles the ones that actually succeeded — through
the SAME idempotent billing_service._settle, so it can never double-grant against a
callback that arrives late.

Runs on the `default` queue. No-op (returns 0) when billing isn't configured, so Beat
firing it on an un-provisioned deploy is harmless.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from PE.weespas.core.celery_app import celery_app
from PE.weespas.core.config import settings
from PE.weespas.core.database import SessionLocal
from PE.weespas.models.billing import PaymentIntent, INTENT_PENDING
from PE.weespas.services import billing_service

logger = logging.getLogger(__name__)

# Only sweep intents in a window: old enough that the callback has had its chance,
# young enough that the STK Query is still meaningful (Daraja expires the request).
RECONCILE_MIN_AGE_SECONDS = 90        # give the callback ~1.5 min to land first
RECONCILE_MAX_AGE_SECONDS = 3600      # past 1h the STK is dead → leave for expiry
RECONCILE_BATCH_LIMIT = 100           # bound the work per tick (O(batch), not O(all))


@celery_app.task(
    name="billing.reconcile_pending",
    ignore_result=True,
    acks_late=False,
)
def reconcile_pending_intents() -> int:
    """Reconcile pending payment intents whose callback may have been lost.
    Returns the number of intents that settled to 'granted' this sweep."""
    if not settings.is_billing_enabled:
        return 0

    now = datetime.now(timezone.utc)
    young = now - timedelta(seconds=RECONCILE_MIN_AGE_SECONDS)
    old = now - timedelta(seconds=RECONCILE_MAX_AGE_SECONDS)

    db = SessionLocal()
    granted = 0
    try:
        intents = (
            db.query(PaymentIntent)
            .filter(
                PaymentIntent.status == INTENT_PENDING,
                PaymentIntent.checkout_request_id.isnot(None),
                PaymentIntent.created_at <= young,
                PaymentIntent.created_at >= old,
            )
            .order_by(PaymentIntent.created_at.asc())
            .limit(RECONCILE_BATCH_LIMIT)
            .all()
        )
        for intent in intents:
            try:
                outcome = billing_service.reconcile_intent(db, intent)
                if outcome == "granted":
                    granted += 1
            except Exception:
                logger.exception("reconcile_intent failed intent=%s", intent.id)
                db.rollback()
        if intents:
            logger.info("billing reconcile sweep: %d checked, %d granted",
                        len(intents), granted)
    finally:
        db.close()
    return granted
