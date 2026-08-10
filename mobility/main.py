"""Mobility service entrypoint.

The realtime dispatch layer of the trading layer (PE/weespas_trade_architecture.md §5): live
driver positions, ride matching, and the SSE dispatch downlink. Stands up alongside weespas
(:8000), InSAR (:8002) and commerce (:8003) on its own port (:8004), verifying weespas-minted
RS256 tokens with the PUBLIC key only.

This slice is the §5 dispatch spine and is Redis-only (GEO positions + Pub/Sub bus); the durable
graph (asyncpg + GeoAlchemy2 KYC store, ride history) and settlement are later slices.

Boot guard (fail-closed, like commerce): in production the app REFUSES to start unless a JWT
public key is configured. Mobility carries dispatch identity, so a forgotten key must never
silently run the service unauthenticated.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from PE.mobility.core.config import settings
from PE.mobility.routers.events import router as events_router
from PE.mobility.routers.ping import router as ping_router
from PE.mobility.routers.rides import router as rides_router
from PE.mobility.services import event_bus

logger = logging.getLogger(__name__)

VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.is_production() and not settings.auth_enabled:
        # Fail closed: never serve dispatch identity unauthenticated in production.
        raise RuntimeError(
            "MOBILITY_ENV=production but no JWT public key is configured "
            "(set MOBILITY_JWT_PUBLIC_KEY_PATH or MOBILITY_JWT_PUBLIC_KEY_INLINE). "
            "Refusing to start unauthenticated."
        )
    if not settings.auth_enabled:
        logger.warning(
            "mobility starting with auth DISABLED (no JWT public key) — dev only; "
            "every protected endpoint will return 503 until a key is provisioned."
        )
    yield
    # Close the shared async Redis pool used by the SSE bus. Idempotent — a no-op if no SSE
    # traffic ever created the client this process.
    await event_bus.aclose()


app = FastAPI(title="Mobility API", version=VERSION, lifespan=lifespan)

# Origin-locked CORS (mirrors commerce). allow_credentials so a frontend may send the mobility
# bearer with a credentialed fetch.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["health"])
def health() -> dict:
    return {"status": "ok", "version": VERSION, "auth_enabled": settings.auth_enabled}


app.include_router(ping_router, prefix="/api/v1", tags=["dispatch"])
app.include_router(rides_router, prefix="/api/v1", tags=["dispatch"])
app.include_router(events_router, prefix="/api/v1", tags=["dispatch"])
