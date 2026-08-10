"""Commerce service entrypoint.

The first service of the trading layer (PE/weespas_trade_architecture.md): a social,
proximity-native marketplace. Stands up alongside weespas (:8000) and InSAR (:8002) on
its own DB and port (:8003), verifying weespas-minted RS256 tokens with the public key
only.

Boot guard (fail-closed): in production the app REFUSES to start unless a JWT public key
is configured. Commerce carries trade identity, so a forgotten key must never silently run
the service unauthenticated (contrast the InSAR read app, which may run public). Uses the
modern lifespan context manager (InSAR style) to host the guard.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from PE.commerce.core.config import settings
from PE.commerce.core.database import create_tables
from PE.commerce.routers.feed import router as feed_router
from PE.commerce.routers.sellers import router as sellers_router
from PE.commerce.routers.engagement import router as engagement_router
from PE.commerce.routers.orders import router as orders_router
from PE.commerce.routers.receipts import router as receipts_router
from PE.commerce.routers.reviews import router as reviews_router
from PE.commerce.routers.trending import router as trending_router
from PE.commerce.routers.quick_buys import router as quick_buys_router
from PE.commerce.routers.flash_sales import router as flash_sales_router
from PE.commerce.routers.search import router as search_router
# §8 Chunk B: seller-console Ranking Card data source. Sits under /sellers so it's colocated
# with the other seller-owner routes even though it lives in its own file to keep sellers.py
# from growing further.
from PE.commerce.routers.shop_ranking import router as shop_ranking_router
# §8 Chunk C: Viewing Card (heartbeat + live-count + view-history) and the Promote-All button.
# Sits under /shops/{shop_id}/... — the heartbeat is anon-callable, the rest are owner-only.
from PE.commerce.routers.shop_views import router as shop_views_router

logger = logging.getLogger(__name__)

VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.is_production() and not settings.auth_enabled:
        # Fail closed: never serve trade identity unauthenticated in production.
        raise RuntimeError(
            "COMMERCE_ENV=production but no JWT public key is configured "
            "(set COMMERCE_JWT_PUBLIC_KEY_PATH or COMMERCE_JWT_PUBLIC_KEY_INLINE). "
            "Refusing to start unauthenticated."
        )
    if not settings.auth_enabled:
        logger.warning(
            "commerce starting with auth DISABLED (no JWT public key) — dev only; "
            "every protected endpoint will return 503 until a key is provisioned."
        )
    create_tables()
    yield


app = FastAPI(title="Commerce API", version=VERSION, lifespan=lifespan)

# Origin-locked CORS (mirrors weespas). allow_credentials so the weespas frontend may
# send the commerce bearer with a credentialed fetch.
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


app.include_router(feed_router, prefix="/api/v1", tags=["feed"])
app.include_router(sellers_router, prefix="/api/v1", tags=["sellers"])
app.include_router(engagement_router, prefix="/api/v1", tags=["engagement"])
app.include_router(orders_router, prefix="/api/v1", tags=["orders"])
app.include_router(receipts_router, prefix="/api/v1", tags=["receipts"])
app.include_router(reviews_router, prefix="/api/v1", tags=["reviews"])
app.include_router(trending_router, prefix="/api/v1", tags=["trending"])
app.include_router(quick_buys_router, prefix="/api/v1", tags=["quick-buys"])
app.include_router(flash_sales_router, prefix="/api/v1", tags=["flash-sales"])
app.include_router(search_router, prefix="/api/v1", tags=["search"])
app.include_router(shop_ranking_router, prefix="/api/v1", tags=["ranking"])
app.include_router(shop_views_router, prefix="/api/v1", tags=["shop-views"])
