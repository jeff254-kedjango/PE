"""Listing-location reveal API — the ONLY endpoint that returns exact coordinates.

PE/billing_architecture.md §6. Everything else (list/detail/feed) serves fuzzed
coords (services/geo_fuzz). A "reveal" sharpens one specific listing's exact,
navigable location and is gated by the entitlement primitive (services.
entitlement_service): the caller must have an active window with a free slot (or the
listing already unlocked in this window). On success we also emit a lightweight
metering event (for the company-detection line, §8) — best-effort, never blocks.

Status codes:
  200  revealed → { latitude, longitude, street_address, directions_url, remaining }
  402  Payment Required → { reason: "no_window" | "quota", tiers: [...] }  (FE opens chooser)
  404  listing not found / has no address
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from PE.weespas.core.database import get_db
from PE.weespas.models.user import User
from PE.weespas.models.property import Property, Address
from PE.weespas.services.auth_service import get_current_user
from PE.weespas.services import entitlement_service as ent
from PE.weespas.services.entitlement_service import RevealOutcome
from PE.weespas.services.billing_tiers import PAID_TIERS
from PE.weespas.services.celery_helpers import safe_delay
from PE.weespas.services.metering_service import record_metering_event_async
from PE.weespas.models.metering import EVENT_REVEAL

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reveal", tags=["reveal"])


# ---- response shapes ------------------------------------------------------

class RevealResponse(BaseModel):
    listing_id: str
    latitude: float
    longitude: float
    street_address: Optional[str] = None
    directions_url: str
    remaining: Optional[int] = None
    newly_charged: bool


def _tier_options() -> list[dict]:
    """The chooser payload the FE renders when a reveal needs payment."""
    return [
        {"code": t.code, "price_kes": t.price_kes, "locations": t.quota,
         "window_seconds": t.window_seconds}
        for t in PAID_TIERS.values()
    ]


def _payment_required(reason: str) -> JSONResponse:
    # 402 Payment Required — the FE maps this to the subscription chooser modal.
    return JSONResponse(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        content={"reason": reason, "tiers": _tier_options()},
    )


def _directions_url(lat: float, lon: float) -> str:
    """A universal maps deep-link (MVP, zero mapping cost — billing_architecture §4.3).
    The device opens its own map app for turn-by-turn navigation."""
    return f"https://www.google.com/maps/dir/?api=1&destination={lat},{lon}"


def _emit_reveal_event(request: Request, user_id: str, listing_id: str) -> None:
    """Best-effort metering for the company-detection line (§8). Never raises.

    Reuses the session spine: SessionMiddleware has already stamped
    request.state.session_id, so the event joins the user's one behavioural thread.
    Dispatched through safe_delay (Celery → inline fallback) so a metering hiccup —
    or a fully-down broker — can never fail a paid reveal."""
    try:
        session_id = getattr(request.state, "session_id", None)
        safe_delay(
            record_metering_event_async,
            EVENT_REVEAL,
            user_id=user_id,
            session_id=session_id,
            target_ref=listing_id,
        )
    except Exception:  # pragma: no cover - defensive; metering is best-effort
        logger.debug("reveal metering emit failed (swallowed)", exc_info=True)


@router.post("/{listing_id}", response_model=RevealResponse)
def reveal_listing(
    listing_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Reveal one listing's exact location, consuming a slot from the active window
    (or free if already unlocked in this window). 402 when no window / quota spent.

    Free-hook tier (commercial_model.md — "make the platform accessible as much as
    possible"): a user with NO window gets one free reveal, once per cooldown, before
    ever seeing the chooser. We try to grant the hook on NO_WINDOW and, if granted,
    re-run the reveal so the free slot is consumed in this same request. The hook is
    self-limiting (try_grant_hook claims a cooldown key NX) and superseded by any paid
    window, so this can't be farmed. QUOTA_EXHAUSTED never gets a hook — the user
    already had a window; that's a genuine upgrade prompt."""
    result = ent.reveal(user.id, listing_id)

    if result.outcome is RevealOutcome.NO_WINDOW:
        # One free look before the paywall, if the user is eligible (no active window
        # + outside cooldown). grant→re-reveal; otherwise fall through to the chooser.
        if ent.try_grant_hook(user.id) is not None:
            result = ent.reveal(user.id, listing_id)
        if result.outcome is not RevealOutcome.REVEALED:
            return _payment_required("no_window")
    elif result.outcome is RevealOutcome.QUOTA_EXHAUSTED:
        return _payment_required("quota")

    # REVEALED — fetch the exact coordinates (the paid good) and build directions.
    addr = (
        db.query(Address)
        .join(Property, Property.id == Address.property_id)
        .filter(Address.property_id == listing_id)
        .first()
    )
    if addr is None:
        raise HTTPException(status_code=404, detail="listing or address not found")

    lat, lon = float(addr.latitude), float(addr.longitude)
    if result.consumed:
        _emit_reveal_event(request, user.id, listing_id)

    return RevealResponse(
        listing_id=listing_id,
        latitude=lat,
        longitude=lon,
        street_address=addr.street_address,
        directions_url=_directions_url(lat, lon),
        remaining=result.remaining,
        newly_charged=result.consumed,
    )


@router.get("/entitlement/me")
def my_entitlement(user: User = Depends(get_current_user)) -> dict:
    """Current window snapshot for the UI status chip (tier, remaining, expiry).
    {active: False} when there is no active window."""
    return ent.entitlement_status(user.id)
