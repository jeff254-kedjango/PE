"""Ride request → matcher → dispatch (architecture §5).

"Rider → POST /rides → matcher GEOSEARCHes for N nearest drivers → PUBLISH ride-events:<driver>."
The rider identity is the authenticated token ``sub`` (never a body field). The search radius is
clamped server-side to ``ride_max_radius_m`` so a client can never force an O(n)-wide scan.

A total Redis outage (GEOSEARCH raises) surfaces as 503 — the rider learns the dispatch layer is
down rather than getting a silent empty match; a per-driver publish blip is absorbed inside the
matcher (best-effort fan-out).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from redis.exceptions import RedisError

from PE.mobility.core.auth import MobilityPrincipal, get_current_principal
from PE.mobility.core.config import settings
from PE.mobility.schemas.dispatch import RideRequest, RideResponse
from PE.mobility.services import matcher
from PE.mobility.services.denylist import DenylistUnavailable, is_denied

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dispatch", tags=["dispatch"])


@router.post("/rides", response_model=RideResponse)
async def request_ride(
    body: RideRequest,
    principal: MobilityPrincipal = Depends(get_current_principal),
) -> RideResponse:
    """Match fresh nearby drivers and dispatch the ride request to them over the SSE bus.

    Revocation gate (decision #4): requesting a ride is a dispatch ACTION, so a banned rider is
    stopped here by an O(1) denylist check BEFORE any matching work. Fails CLOSED — an unreachable
    denylist returns 503 (refuse) rather than admitting a possibly-banned actor."""
    try:
        if await is_denied(principal.sub):
            raise HTTPException(status_code=403, detail="Account is not permitted to request rides")
    except DenylistUnavailable:
        raise HTTPException(status_code=503, detail="Dispatch temporarily unavailable")

    radius = body.radius_m or settings.ride_default_radius_m
    # Clamp to the anti-O(n) server cap (and floor at a sane minimum via the schema's gt=0).
    radius = min(radius, settings.ride_max_radius_m)
    try:
        result = await matcher.match_and_dispatch(
            principal.sub, principal.name, body.lat, body.lng, radius
        )
    except RedisError:
        logger.error("rides: dispatch backend unavailable", exc_info=True)
        raise HTTPException(status_code=503, detail="Dispatch temporarily unavailable")
    return RideResponse(**result)
