import logging
import mimetypes
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from PE.weespas.core.config import settings
from PE.weespas.core.database import create_tables, SessionLocal
from PE.weespas.models.user import User, UserRole
from PE.weespas.routers.properties import router as properties_router
from PE.weespas.routers.auth import router as auth_router
from PE.weespas.routers.contact import router as contact_router
from PE.weespas.routers.agents import router as agents_router
from PE.weespas.routers.admin import router as admin_router
from PE.weespas.routers.staff import router as staff_router
from PE.weespas.routers.media import router as media_router
from PE.weespas.routers.analytics import router as analytics_router
from PE.weespas.routers.favorites import router as favorites_router
from PE.weespas.routers.dismissals import router as dismissals_router
from PE.weespas.routers.sessions import router as sessions_router
from PE.weespas.routers.me import router as me_router
from PE.weespas.routers.saved_searches import router as saved_searches_router
from PE.weespas.routers.role_applications import router as role_applications_router
from PE.weespas.routers.structural_flags import router as structural_flags_router
from PE.weespas.routers.reveal import router as reveal_router
from PE.weespas.routers.billing import router as billing_router
from PE.weespas.routers.metering import router as metering_router
from PE.weespas.routers.policy import router as policy_router
from PE.weespas.routers.insar import router as insar_router
from PE.weespas.routers.insar_telemetry import router as insar_telemetry_router
from PE.weespas.routers.notifications import router as notifications_router
from PE.weespas.routers.flag_reviews import router as flag_reviews_router
from PE.weespas.routers.commerce import router as commerce_router
from PE.weespas.services import event_bus
from PE.weespas.middleware import SessionMiddleware

logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Weespas API",
    description="Enterprise-scale property marketplace API for millions of concurrent users",
    version="2.0.0"
)


# Analytics session middleware (must run inside CORS so it can set cookies)
app.add_middleware(SessionMiddleware)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """HSTS in production. Gated on `cookie_secure` — the existing prod/HTTPS signal
    (cookie_secure=True is set only when we're served over HTTPS, see config.py). HSTS is
    meaningless without TLS, so this stays off in local http dev and turns on with the same
    switch that hardens the session cookie. One header per response."""
    response = await call_next(request)
    if settings.cookie_secure:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response

# CORS Middleware — origins are env-driven (settings.cors_origins, CSV in .env).
# Default = the local Vite dev ports, so dev is unchanged; in PROD set CORS_ORIGINS
# to the real frontend origin(s) only. Origin-locking is the meaningful control here
# (credentials are allowed for the session cookie + JWT); methods/headers stay open
# because the cookie+JWT flows send a variety of headers and same-origin in prod.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===================== STARTUP EVENTS =====================
@app.on_event("startup")
async def startup_event():
    """Initialize database tables and seed admin user on startup."""
    create_tables()
    _seed_admin()


@app.on_event("shutdown")
async def shutdown_event():
    """Close the async Redis pool backing the SSE contact bus (§8.1b). Idempotent —
    a no-op when no SSE traffic ever opened the client this process."""
    await event_bus.aclose()


def _seed_admin():
    """Ensure Kwemange Nyagrowa is always set to admin role."""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "kwemangenyagrowa@gmail.com").first()
        if user and user.role != UserRole.ADMIN:
            logger.info("Promoting %s (%s) to admin", user.name, user.email)
            user.role = UserRole.ADMIN
            db.commit()
        elif user:
            logger.info("Admin user %s already has admin role", user.email)
        else:
            logger.warning("Admin user kwemangenyagrowa@gmail.com not found in DB — register first")
    finally:
        db.close()


# ===================== API ROUTES =====================
app.include_router(properties_router, prefix="/api/v1", tags=["properties"])
app.include_router(auth_router, prefix="/api/v1", tags=["auth"])
app.include_router(contact_router, prefix="/api/v1", tags=["contact"])
app.include_router(agents_router, prefix="/api/v1", tags=["agents"])
app.include_router(admin_router, prefix="/api/v1", tags=["admin"])
app.include_router(staff_router, prefix="/api/v1", tags=["staff"])
app.include_router(media_router, prefix="/api/v1", tags=["media"])
app.include_router(analytics_router, prefix="/api/v1", tags=["analytics"])
app.include_router(favorites_router, prefix="/api/v1", tags=["favorites"])
app.include_router(dismissals_router, prefix="/api/v1", tags=["dismissals"])
app.include_router(sessions_router, prefix="/api/v1", tags=["sessions"])
app.include_router(me_router, prefix="/api/v1", tags=["me"])
app.include_router(saved_searches_router, prefix="/api/v1", tags=["saved-searches"])
app.include_router(role_applications_router, prefix="/api/v1", tags=["role-applications"])
app.include_router(structural_flags_router, prefix="/api/v1", tags=["structural-flags"])
app.include_router(reveal_router, prefix="/api/v1", tags=["reveal"])
app.include_router(billing_router, prefix="/api/v1", tags=["billing"])
app.include_router(metering_router, prefix="/api/v1", tags=["metering"])
app.include_router(policy_router, prefix="/api/v1", tags=["policy"])
app.include_router(insar_router, prefix="/api/v1", tags=["insar"])
app.include_router(insar_telemetry_router, prefix="/api/v1", tags=["insar-telemetry"])
app.include_router(notifications_router, prefix="/api/v1", tags=["notifications"])
app.include_router(flag_reviews_router, prefix="/api/v1", tags=["flag-reviews"])
app.include_router(commerce_router, prefix="/api/v1", tags=["commerce"])

# Serve uploaded files (images, videos)
#
# WebP MUST be registered before the mount. StaticFiles derives Content-Type from
# `mimetypes.guess_type`, and CPython's table has no .webp entry on this interpreter
# (verified: 3.10 returns None), so every .webp fell back to `text/plain; charset=utf-8`.
# WebP is our PREFERRED stored format — routers/media.py transcodes to it and both upload
# paths accept image/webp — so this mislabels the bulk of served media, avatars included.
#
# It renders today only because no response sets `X-Content-Type-Options: nosniff`; browsers
# sniff the magic bytes and show the image anyway. That makes it a latent trap: adding nosniff
# (a routine hardening step) would instantly break every WebP image site-wide, with no code
# change anywhere near the images. Registering the real type removes the dependence on sniffing.
#
# `mimetypes.add_type` is idempotent and process-local — it cannot leak to other services.
mimetypes.add_type("image/webp", ".webp")
_uploads_dir = Path("uploads")
_uploads_dir.mkdir(exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(_uploads_dir)), name="uploads")


# ===================== HEALTH CHECK =====================
@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring"""
    return {"status": "ok", "version": "2.0.0"}


@app.get("/")
async def root():
    """Root endpoint with API info"""
    return {
        "name": "Weespas API",
        "version": "2.0.0",
        "description": "Enterprise-scale property marketplace",
        "docs": "/docs",
        "redoc": "/redoc"
    }