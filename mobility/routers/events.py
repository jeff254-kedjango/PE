"""SSE dispatch downlink (architecture §5).

"Server → rider/driver events, SSE." Each client (driver or rider) holds ONE long-lived SSE GET
subscribed to its OWN channel (``ride-events:<sub>``). The channel is keyed on the verified token
``sub`` — never caller-supplied — so a caller can only ever hear their OWN events; there is no
cross-user channel to attempt to subscribe to. The matcher (chunk 2) PUBLISHes ride requests to a
driver's channel; whichever mobility instance holds that driver's SSE connection relays it down —
horizontally scalable with no sticky sessions (doc §5).

Mirrors the proven weespas §8.1b contact-stream wiring exactly (StreamingResponse +
``text/event-stream`` + no-buffering headers). No DB work, no body: a pure realtime pipe.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from PE.mobility.core.auth import MobilityPrincipal, get_current_principal
from PE.mobility.core.config import settings
from PE.mobility.services import event_bus
from PE.mobility.services.matcher import _ride_channel

router = APIRouter(prefix="/dispatch", tags=["dispatch"])


@router.get("/events")
async def events(
    request: Request,
    principal: MobilityPrincipal = Depends(get_current_principal),
) -> StreamingResponse:
    """Subscribe to this caller's OWN dispatch channel as an SSE stream.

    The stream emits one ``data:`` frame per event plus a periodic ``:keep-alive`` comment, and is
    torn down cleanly on client disconnect or after ``sse_max_connection_seconds`` (the client
    transparently reconnects, so a closed tab leaks no subscriber slot). ``X-Accel-Buffering: no``
    disables nginx response buffering so frames flush immediately (see doc §5 deploy note);
    ``Cache-Control: no-cache`` keeps proxies from caching the stream."""
    stream = event_bus.sse_subscribe(
        _ride_channel(principal.sub),
        is_disconnected=request.is_disconnected,
        heartbeat_s=settings.sse_heartbeat_seconds,
        max_connection_s=settings.sse_max_connection_seconds,
    )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
