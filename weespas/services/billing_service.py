"""Billing state machine: checkout → STK → settle → grant window.

PE/billing_architecture.md §4. Owns the DB (payment_intent/payment_ledger) and calls
entitlement_service.grant_window on settlement. The M-Pesa HTTP lives in mpesa_client;
this module is the idempotent coordinator.

Idempotency (the one thing that must never break): a settled payment grants EXACTLY
one window even if the callback is delivered twice or the reconciliation sweep races
the callback. Two guards:
  1. Redis SET NX on the receipt (fast dedupe, same primitive as celery_helpers).
  2. payment_ledger.mpesa_receipt UNIQUE (durable backstop — a racing duplicate fails
     the insert, which we catch and treat as already-settled).
"""
from __future__ import annotations

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from PE.weespas.core.config import settings
from PE.weespas.services.cache import redis_client
from PE.weespas.services.billing_tiers import get_tier
from PE.weespas.services import entitlement_service as ent
from PE.weespas.services import mpesa_client
from PE.weespas.services.celery_helpers import safe_delay
from PE.weespas.services.metering_service import record_metering_event_async
from PE.weespas.models.billing import (
    PaymentIntent, PaymentLedger,
    INTENT_PENDING, INTENT_PAID, INTENT_FAILED,
)
from PE.weespas.models.metering import EVENT_CHECKOUT_PAID

logger = logging.getLogger(__name__)


class BillingError(RuntimeError):
    pass


class BillingDisabled(BillingError):
    """M-Pesa isn't configured — checkout is refused (→ 503 at the router)."""


def _receipt_dedupe_key(receipt: str) -> str:
    return f"mpesa:cb:{receipt}"


def create_checkout(db: Session, *, user_id: str, phone: str, tier_code: str) -> PaymentIntent:
    """Create a pending intent and fire the STK Push. Returns the intent (FE polls it).

    Raises BillingDisabled if M-Pesa isn't configured, ValueError for a bad tier.
    On STK failure the intent is marked failed and BillingError is raised.
    """
    if not settings.is_billing_enabled:
        raise BillingDisabled("M-Pesa not configured")
    tier = get_tier(tier_code)
    if tier is None or tier.price_kes <= 0:
        raise ValueError(f"not a purchasable tier: {tier_code!r}")

    intent = PaymentIntent(
        user_id=user_id, phone=phone, tier=tier.code,
        amount_kes=tier.price_kes, status=INTENT_PENDING,
    )
    db.add(intent)
    db.commit()
    db.refresh(intent)

    try:
        resp = mpesa_client.stk_push(
            phone=phone, amount=tier.price_kes,
            account_ref=f"WSP{intent.id[:8]}", description=f"Weespas {tier.code}",
        )
    except mpesa_client.MpesaError as e:
        intent.status = INTENT_FAILED
        db.commit()
        raise BillingError(f"STK initiate failed: {e}") from e

    intent.merchant_request_id = resp.get("MerchantRequestID")
    intent.checkout_request_id = resp.get("CheckoutRequestID")
    db.commit()
    db.refresh(intent)
    return intent


def settle_from_callback(db: Session, parsed: dict) -> str:
    """Process a parsed STK callback (mpesa_client.parse_callback). Idempotent.

    Returns a short status string: 'granted' / 'duplicate' / 'failed' / 'unknown' /
    'amount_mismatch'. Never raises on a normal duplicate — that's the happy path for
    an at-least-once callback.
    """
    crid = parsed.get("checkout_request_id")
    if not crid:
        return "unknown"

    intent = (
        db.query(PaymentIntent)
        .filter(PaymentIntent.checkout_request_id == crid)
        .first()
    )
    if intent is None:
        logger.info("callback for unknown checkout_request_id=%s — ignoring", crid)
        return "unknown"

    result_code = parsed.get("result_code")
    intent.result_code = result_code if result_code is None else int(result_code)

    # Non-zero ResultCode = user cancelled / timeout / insufficient funds.
    if intent.result_code != 0:
        if intent.status == INTENT_PENDING:
            intent.status = INTENT_FAILED
            db.commit()
        return "failed"

    receipt = parsed.get("mpesa_receipt")
    if not receipt:
        logger.warning("success callback with no receipt for intent=%s", intent.id)
        return "unknown"

    # Verify the amount matches what we asked for (defends against a tampered body).
    amt = parsed.get("amount")
    if amt is not None and int(amt) != int(intent.amount_kes):
        logger.warning("amount mismatch intent=%s asked=%s got=%s",
                       intent.id, intent.amount_kes, amt)
        return "amount_mismatch"

    return _settle(db, intent, receipt)


def _settle(db: Session, intent: PaymentIntent, receipt: str) -> str:
    """The idempotent settle core: dedupe → ledger insert → mark paid → grant window.
    Shared by the callback and the reconciliation sweep."""
    # Fast path dedupe (Redis NX). If the key already exists, another delivery already
    # settled this receipt → no-op.
    try:
        first = redis_client.set(_receipt_dedupe_key(receipt), "1", nx=True, ex=86400)
    except Exception:  # Redis down — fall through to the DB UNIQUE backstop.
        first = True
    if not first:
        return "duplicate"

    tier = get_tier(intent.tier)
    ledger = PaymentLedger(
        intent_id=intent.id, user_id=intent.user_id, mpesa_receipt=receipt,
        amount_kes=intent.amount_kes, tier=intent.tier,
        quota=tier.quota, window_seconds=tier.window_seconds,
    )
    db.add(ledger)
    intent.status = INTENT_PAID
    intent.mpesa_receipt = receipt
    try:
        db.commit()
    except IntegrityError:
        # Durable backstop: a racing duplicate hit the UNIQUE(mpesa_receipt). Already
        # settled by the winner → roll back and report duplicate (still idempotent).
        db.rollback()
        return "duplicate"

    # Grant the window AFTER the money is durably recorded. grant_window replaces any
    # existing window (option A). If Redis is momentarily down here, the ledger still
    # records the entitlement; a retry/poll re-grants from the paid intent.
    ent.grant_window(intent.user_id, intent.tier, txn_id=receipt)
    # Best-effort metering (§8). Fires here so it covers BOTH the callback and the
    # reconciliation settle paths (both reach _settle). No session in the service
    # layer → user-anchored only. Never let a metering hiccup unsettle a paid window.
    safe_delay(
        record_metering_event_async,
        EVENT_CHECKOUT_PAID,
        user_id=intent.user_id,
        target_ref=intent.id,
        meta=intent.tier,
    )
    return "granted"


def reconcile_intent(db: Session, intent: PaymentIntent) -> str:
    """Reconciliation sweep for a pending intent whose callback may have been lost:
    ask Daraja (STK Query) and settle if it actually succeeded. Idempotent via _settle.
    Returns the settle status, or 'still_pending' / 'query_error'."""
    if not intent.checkout_request_id:
        return "still_pending"
    try:
        res = mpesa_client.stk_query(checkout_request_id=intent.checkout_request_id)
    except mpesa_client.MpesaError as e:
        logger.info("reconcile query failed intent=%s: %s", intent.id, e)
        return "query_error"

    code = res.get("ResultCode")
    if code is None or int(code) != 0:
        return "still_pending"
    # STK Query confirms payment but does NOT return the receipt; use a synthetic,
    # deterministic receipt id keyed on the checkout so it's unique + idempotent.
    receipt = f"RC-{intent.checkout_request_id}"
    return _settle(db, intent, receipt)
