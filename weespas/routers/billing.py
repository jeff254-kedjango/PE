"""Billing API — checkout (STK Push), the M-Pesa callback, and checkout polling.

PE/billing_architecture.md §4 / §9. Three endpoints:
  POST /billing/checkout            (JWT)   → initiate STK, return checkout id to poll
  GET  /billing/checkout/{id}       (JWT)   → poll intent status (pending→paid/failed)
  POST /billing/mpesa/callback      (public) → Safaricom posts the result here
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from PE.weespas.core.database import get_db
from PE.weespas.models.user import User
from PE.weespas.models.billing import PaymentIntent
from PE.weespas.core.config import settings
from PE.weespas.services.auth_service import get_current_user
from PE.weespas.services import billing_service, mpesa_client, entitlement_service
from PE.weespas.services.billing_tiers import PAID_TIERS
from PE.weespas.middleware.session import _client_ip
from PE.weespas.services.celery_helpers import safe_delay
from PE.weespas.services.metering_service import record_metering_event_async
from PE.weespas.models.metering import EVENT_CHECKOUT_INITIATED

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


class CheckoutRequest(BaseModel):
    tier: str   # T1 / T2 / T3


class CheckoutResponse(BaseModel):
    checkout_id: str
    status: str


@router.post("/checkout", response_model=CheckoutResponse)
def checkout(
    body: CheckoutRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Start a tier purchase: fire the STK Push to the user's verified phone and
    return a checkout id the FE polls. The phone prompt is what the user pays on."""
    if not user.phone:
        raise HTTPException(400, "no phone number on file for STK Push")
    # Rate-limit STK initiation so a user can't machine-gun PIN prompts (cost +
    # abuse). O(1), fail-open on Redis error (billing_architecture.md §10).
    if not entitlement_service.check_rate_limit(
        "checkout", str(user.id),
        max_hits=settings.checkout_rate_max,
        window_seconds=settings.checkout_rate_window_s,
    ):
        raise HTTPException(429, "Too many checkout attempts. Please try again shortly.")
    try:
        intent = billing_service.create_checkout(
            db, user_id=user.id, phone=user.phone, tier_code=body.tier
        )
    except billing_service.BillingDisabled:
        raise HTTPException(503, "billing not configured")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except billing_service.BillingError as e:
        raise HTTPException(502, f"payment initiation failed: {e}")
    # Best-effort metering (§8) — never blocks the checkout. checkout_paid is emitted
    # from billing_service.settle so it fires for callback AND reconciliation alike.
    safe_delay(
        record_metering_event_async,
        EVENT_CHECKOUT_INITIATED,
        user_id=user.id,
        session_id=getattr(request.state, "session_id", None),
        target_ref=intent.id,
        meta=body.tier,
    )
    return CheckoutResponse(checkout_id=intent.id, status=intent.status)


@router.get("/checkout/{checkout_id}")
def checkout_status(
    checkout_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Poll a checkout. The FE shows 'confirming payment…' and polls until the
    status flips to 'paid' (then retries the pending reveal) or 'failed'."""
    intent = (
        db.query(PaymentIntent)
        .filter(PaymentIntent.id == checkout_id, PaymentIntent.user_id == user.id)
        .first()
    )
    if intent is None:
        raise HTTPException(404, "checkout not found")
    return {"checkout_id": intent.id, "status": intent.status, "tier": intent.tier}


@router.post("/mpesa/callback")
async def mpesa_callback(request: Request, db: Session = Depends(get_db)) -> dict:
    """Safaricom posts the STK result here. PUBLIC (Safaricom calls it) but safe: the
    body is only a lookup key; we verify ResultCode + Amount and dedupe on the receipt.
    Always returns 200 with Daraja's expected shape (a non-200 makes Safaricom retry)."""
    # Defense-in-depth: if an IP allow-list is configured, drop callbacks from any
    # other source. Default (empty list) = no check, so sandbox/dev are unchanged.
    # We still return 200 on a drop — a non-200 would make Safaricom retry-storm —
    # and simply don't process the (untrusted) body.
    allowed_ips = settings.mpesa_callback_allowed_ip_set
    if allowed_ips:
        client_ip = _client_ip(request)
        if client_ip not in allowed_ips:
            logger.warning("mpesa callback from non-allowed ip=%s (ignored)", client_ip)
            return {"ResultCode": 0, "ResultDesc": "Accepted"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    parsed = mpesa_client.parse_callback(body)
    try:
        outcome = billing_service.settle_from_callback(db, parsed)
        logger.info("mpesa callback outcome=%s crid=%s", outcome,
                    parsed.get("checkout_request_id"))
    except Exception:  # never error back to Safaricom; we'll reconcile if needed
        logger.exception("mpesa callback processing error (will reconcile)")
    return {"ResultCode": 0, "ResultDesc": "Accepted"}


@router.get("/tiers")
def list_tiers() -> dict:
    """The purchasable ladder (for the chooser modal). Public — no secret here."""
    return {"tiers": [
        {"code": t.code, "price_kes": t.price_kes, "locations": t.quota,
         "window_seconds": t.window_seconds}
        for t in PAID_TIERS.values()
    ]}
