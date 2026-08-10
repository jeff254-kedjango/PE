"""Request/response schemas for the §5 dispatch spine.

Coordinates are constrained at the edge (pydantic ge/le) so an out-of-range or NaN value is
rejected with a 422 before it ever reaches Redis GEO — defence in depth alongside the
services.geo range check.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class PingRequest(BaseModel):
    """A driver GPS ping (uplink). The driver identity is the token ``sub`` — never a body field —
    so a driver can only ever move their OWN dot."""
    lat: float = Field(..., ge=-90.0, le=90.0, description="Latitude in decimal degrees")
    lng: float = Field(..., ge=-180.0, le=180.0, description="Longitude in decimal degrees")


class PingResponse(BaseModel):
    ok: bool
    # Server-stamped unix time the position was recorded at (the freshness clock the matcher uses).
    seen_at: float


class RideRequest(BaseModel):
    """A rider's ride request (the pickup point). The rider identity is the token ``sub`` — never a
    body field. ``radius_m`` is an optional override, clamped server-side to ``ride_max_radius_m``
    (a client can never widen the search past the anti-O(n) cap)."""
    lat: float = Field(..., ge=-90.0, le=90.0, description="Pickup latitude in decimal degrees")
    lng: float = Field(..., ge=-180.0, le=180.0, description="Pickup longitude in decimal degrees")
    radius_m: float | None = Field(
        None, gt=0.0, description="Optional search radius (metres); clamped to the server max"
    )


class RideResponse(BaseModel):
    ride_id: str
    drivers_matched: int   # fresh nearby drivers found within radius
    drivers_notified: int  # of those, how many were successfully pinged over the bus
