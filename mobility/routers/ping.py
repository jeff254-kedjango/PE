"""Driver GPS ping uplink (architecture §5).

"Driver → server GPS pings, every 3–5 s, plain HTTP POST, fire-and-forget." The driver's
identity is the authenticated token ``sub`` — a driver can only ever move their OWN dot, never
spoof another's position. The write is O(1) into Redis GEO (services.geo).

Any valid mobility-scoped token may ping (a driver going on-shift). Whether that driver is
DISPATCHABLE (KYC-passed) is a separate check applied by the matcher at ride time (chunk 4) —
posting a position is not the same as being eligible to receive rides.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from PE.mobility.core.auth import (
    DISPATCH_ELIGIBLE_SCOPE,
    MobilityPrincipal,
    get_current_principal,
)
from PE.mobility.schemas.dispatch import PingRequest, PingResponse
from PE.mobility.services import geo

router = APIRouter(prefix="/dispatch", tags=["dispatch"])


@router.post("/ping", response_model=PingResponse)
async def ping(
    body: PingRequest,
    principal: MobilityPrincipal = Depends(get_current_principal),
) -> PingResponse:
    """Record the calling driver's live position + dispatch eligibility. Identity is the token
    ``sub`` (never a body field). Eligibility is read from the token's ``dispatch:eligible`` scope
    (weespas's KYC signal, doc §16) and refreshed every ping so a revocation propagates within one
    ping interval. Coordinates are range-validated by the schema; services.geo re-checks as defence
    in depth and maps a bad value to 422."""
    eligible = principal.has_scope(DISPATCH_ELIGIBLE_SCOPE)
    try:
        seen_at = await geo.record_ping(
            principal.sub, body.lat, body.lng, eligible=eligible
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return PingResponse(ok=True, seen_at=seen_at)
