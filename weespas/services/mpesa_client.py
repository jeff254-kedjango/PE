"""Thin Daraja (M-Pesa) STK Push client — token, initiate, query.

PE/billing_architecture.md §4. Kept deliberately small and side-effect-free beyond
the HTTP calls; billing_service owns the DB/Redis state machine. Works against the
Daraja SANDBOX in dev (settings.mpesa_host) and production by swapping the base
URL + real credentials. NO real secret lives in the repo — all from settings/.env.

When billing is not configured (settings.is_billing_enabled is False) the caller
(billing_service) refuses checkout with a clear 503; this module assumes it is only
invoked when configured.
"""
from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone

import requests

from PE.weespas.core.config import settings

logger = logging.getLogger(__name__)


class MpesaError(RuntimeError):
    """A Daraja call failed (network, auth, or a non-OK response body)."""


def _timestamp() -> str:
    # Daraja wants YYYYMMDDHHMMSS in EAT; UTC+3. We build it from UTC to avoid
    # depending on server local time.
    now = datetime.now(timezone.utc).astimezone()
    return now.strftime("%Y%m%d%H%M%S")


def _password(timestamp: str) -> str:
    """base64(Shortcode + Passkey + Timestamp) — the STK 'Password' field."""
    raw = f"{settings.mpesa_shortcode}{settings.mpesa_passkey}{timestamp}"
    return base64.b64encode(raw.encode()).decode()


def get_access_token() -> str:
    """OAuth token (Basic auth with consumer key/secret). Short-lived; we fetch per
    operation — simple and correct. (A 50-min cache is a later optimisation.)"""
    url = f"{settings.mpesa_host}/oauth/v1/generate?grant_type=client_credentials"
    try:
        resp = requests.get(
            url,
            auth=(settings.mpesa_consumer_key, settings.mpesa_consumer_secret),
            timeout=settings.mpesa_timeout_s,
        )
        resp.raise_for_status()
        token = resp.json().get("access_token")
        if not token:
            raise MpesaError("no access_token in Daraja response")
        return token
    except requests.RequestException as e:
        raise MpesaError(f"token request failed: {e}") from e


def stk_push(*, phone: str, amount: int, account_ref: str, description: str) -> dict:
    """Initiate an STK Push (the PIN prompt on the user's phone).

    Returns the Daraja response dict, which includes `MerchantRequestID` and
    `CheckoutRequestID` (the latter is how we match the async callback). Raises
    MpesaError on any failure so billing_service can mark the intent failed.
    """
    token = get_access_token()
    ts = _timestamp()
    payload = {
        "BusinessShortCode": settings.mpesa_shortcode,
        "Password": _password(ts),
        "Timestamp": ts,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": int(amount),
        "PartyA": _normalize_msisdn(phone),
        "PartyB": settings.mpesa_shortcode,
        "PhoneNumber": _normalize_msisdn(phone),
        "CallBackURL": settings.mpesa_callback_url,
        "AccountReference": account_ref[:12],   # Daraja caps this field
        "TransactionDesc": description[:13],
    }
    url = f"{settings.mpesa_host}/mpesa/stkpush/v1/processrequest"
    try:
        resp = requests.post(url, json=payload,
                             headers={"Authorization": f"Bearer {token}"},
                             timeout=settings.mpesa_timeout_s)
        resp.raise_for_status()
        body = resp.json()
    except requests.RequestException as e:
        raise MpesaError(f"stk_push failed: {e}") from e
    # Daraja returns ResponseCode "0" on a successfully *accepted* request (not yet paid).
    if str(body.get("ResponseCode", "")) != "0":
        raise MpesaError(f"stk_push rejected: {body}")
    return body


def stk_query(*, checkout_request_id: str) -> dict:
    """Query the status of a prior STK Push (the reconciliation path for a lost
    callback). Returns the Daraja response; `ResultCode` 0 means the user paid."""
    token = get_access_token()
    ts = _timestamp()
    payload = {
        "BusinessShortCode": settings.mpesa_shortcode,
        "Password": _password(ts),
        "Timestamp": ts,
        "CheckoutRequestID": checkout_request_id,
    }
    url = f"{settings.mpesa_host}/mpesa/stkpushquery/v1/query"
    try:
        resp = requests.post(url, json=payload,
                             headers={"Authorization": f"Bearer {token}"},
                             timeout=settings.mpesa_timeout_s)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        raise MpesaError(f"stk_query failed: {e}") from e


def _normalize_msisdn(phone: str) -> str:
    """Daraja wants 2547XXXXXXXX (no +, no leading 0). Best-effort normalise the
    common Kenyan formats; leave anything already in 254… form untouched."""
    p = phone.strip().replace("+", "").replace(" ", "")
    if p.startswith("0") and len(p) == 10:
        return "254" + p[1:]
    if p.startswith("7") and len(p) == 9:
        return "254" + p
    return p


def parse_callback(body: dict) -> dict:
    """Extract the fields we need from the STK callback envelope. Returns a dict with
    checkout_request_id, result_code, and (on success) mpesa_receipt + amount + phone.
    Tolerant of missing CallbackMetadata (failure callbacks omit it)."""
    stk = (((body or {}).get("Body") or {}).get("stkCallback") or {})
    out = {
        "checkout_request_id": stk.get("CheckoutRequestID"),
        "merchant_request_id": stk.get("MerchantRequestID"),
        "result_code": stk.get("ResultCode"),
        "mpesa_receipt": None,
        "amount": None,
        "phone": None,
    }
    items = ((stk.get("CallbackMetadata") or {}).get("Item")) or []
    for it in items:
        name, value = it.get("Name"), it.get("Value")
        if name == "MpesaReceiptNumber":
            out["mpesa_receipt"] = value
        elif name == "Amount":
            out["amount"] = value
        elif name == "PhoneNumber":
            out["phone"] = value
    return out
