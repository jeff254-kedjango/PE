"""ASGI middleware: attaches a per-visitor session row to every request.

- Reads/sets the `weespas_session` cookie (UUID token).
- Resolves client IP, INSERTs/touches a UserSession row.
- When ``settings.celery_session_geo_enabled`` is True, the MaxMind GeoIP
  read is offloaded to ``session.enrich_geo`` — the middleware writes a
  stub row with ``geo_*=NULL`` and the worker fills it in seconds later.
  This removes the GeoIP latency from EVERY uncached request (the worst
  stateless-backend offender per Celery_Audit.md §2 P1).
- Best-effort links the row to the authed user when an Authorization header
  is present.
- Stashes session_id, session_token, client_ip on request.state for routes.

Failures are swallowed — request handling must never break because analytics
write failed.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from PE.weespas.core.config import settings
from PE.weespas.core.database import SessionLocal
from PE.weespas.models.analytics import UserSession
from PE.weespas.services.geoip_service import lookup_ip
from PE.weespas.services.celery_helpers import safe_delay

logger = logging.getLogger(__name__)

COOKIE_NAME = "weespas_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def _user_id_from_auth(request: Request) -> str | None:
    """Best-effort: pull the `sub` claim out of the Bearer token.

    Returns None for missing / malformed / expired tokens — analytics never
    fails the request. We deliberately do NOT touch the DB or verify the user
    is active here; the auth dependency on protected routes handles that.
    The id is only used to stamp UserSession.user_id, and a stale id stamped
    onto a session is preferable to losing the link entirely (the user table
    keeps the row even when is_active is flipped off).
    """
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth or not auth.lower().startswith("bearer "):
        return None
    token = auth[7:].strip()
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        sub = payload.get("sub")
        return str(sub) if sub else None
    except JWTError:
        return None
    except Exception as e:
        # Defensive — secret/algorithm misconfig should never break analytics.
        logger.debug("auth decode in session middleware failed: %s", e)
        return None


def _upsert_session(
    token: str,
    ip: str | None,
    user_agent: str | None,
    user_id: str | None,
) -> tuple[str | None, bool]:
    """Returns (UserSession.id, is_new). On failure returns (None, False).

    ``is_new`` tells the middleware whether to dispatch the GeoIP enrichment
    task — only new rows need it. Existing rows already carry geo_* if their
    IP resolved on first insert.

    If ``user_id`` is provided and the existing row is still anonymous, we
    stamp it on a one-time basis (we never *overwrite* an existing user_id —
    sharing a device shouldn't let session B claim session A's stats).
    """
    db = SessionLocal()
    try:
        sess = db.query(UserSession).filter(UserSession.session_token == token).first()
        now = datetime.now(timezone.utc)
        if sess:
            sess.last_seen_at = now
            if ip and not sess.ip_address:
                sess.ip_address = ip
            # First-time link: anonymous → authed. After this, leave user_id
            # alone so device-sharing doesn't reassign historical sessions.
            if user_id and not sess.user_id:
                sess.user_id = user_id
            db.commit()
            return sess.id, False

        # New session — when the offload flag is on, insert with geo_*=NULL
        # and let the worker fill them. The 1-2ms indexed write stays sync
        # because downstream handlers depend on request.state.session being
        # set; the MaxMind read + 4-column UPDATE is what we offload.
        if settings.celery_session_geo_enabled:
            geo = None
        else:
            geo = lookup_ip(ip) if ip else None

        sess = UserSession(
            id=str(uuid.uuid4()),
            session_token=token,
            user_id=user_id,
            ip_address=ip,
            user_agent=(user_agent or "")[:500] or None,
            geo_lat=geo["lat"] if geo else None,
            geo_lng=geo["lng"] if geo else None,
            geo_city=geo["city"] if geo else None,
            geo_county=geo["county"] if geo else None,
            geo_source="ip" if geo else None,
        )
        db.add(sess)
        db.commit()
        return sess.id, True
    except Exception as e:
        logger.warning("session upsert failed: %s", e)
        try:
            db.rollback()
        except Exception:
            pass
        return None, False
    finally:
        db.close()


class SessionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Skip static & docs paths to keep them cheap
        path = request.url.path
        # /insar-telemetry (POST) and /insar/verify (GET) are cross-origin calls from
        # the stateless InSAR SPA: under SameSite=Lax the weespas_session cookie is
        # never sent, so running session upsert here would mint a throwaway anonymous
        # row on EVERY beat / page load (the exact amplification the cookie-persistence
        # note below guards against). Both carry their own scoped JWT identity instead.
        # NOTE: /insar/session-token is deliberately NOT skipped — it's called from the
        # Weespas frontend (same-origin, cookie present) and should anchor the session.
        if path.startswith(("/uploads", "/docs", "/redoc", "/openapi.json", "/health",
                            "/api/v1/insar-telemetry", "/api/v1/insar/verify")):
            return await call_next(request)

        token = request.cookies.get(COOKIE_NAME)
        new_token = False
        if not token:
            token = uuid.uuid4().hex
            new_token = True

        ip = _client_ip(request)
        user_agent = request.headers.get("user-agent")
        user_id = _user_id_from_auth(request)

        session_id, is_new_row = _upsert_session(token, ip, user_agent, user_id)

        # Phase 2.1 — fire the GeoIP enrichment for new rows only. Existing
        # session reads don't re-enrich; the data is sticky for the row's life.
        if (
            session_id
            and is_new_row
            and ip
            and settings.celery_session_geo_enabled
        ):
            from PE.weespas.services.session_tasks import enrich_session_geo
            safe_delay(enrich_session_geo, session_id, ip)

        request.state.session_id = session_id
        request.state.session_token = token
        request.state.client_ip = ip

        response: Response = await call_next(request)

        if new_token:
            # Cookie attrs matter for analytics correctness:
            #   - path="/"      The default is the request path. Without an
            #                   explicit "/" the cookie is scoped to e.g.
            #                   /api/auth/login and isn't sent for any other
            #                   route, so the next request creates a new row
            #                   and `last_seen_at` never advances.
            #   - secure=...    Required on HTTPS for SameSite=Lax to behave
            #                   predictably across browsers; some edge/CDN
            #                   stacks drop non-secure cookies on HTTPS
            #                   entirely. Driven by settings so dev on
            #                   http://localhost still works.
            # This is the cookie-persistence fix that makes /analytics/engagement
            # actually produce non-zero series — see the docstring on
            # settings.cookie_secure for the symptom that was happening.
            response.set_cookie(
                key=COOKIE_NAME,
                value=token,
                max_age=COOKIE_MAX_AGE,
                httponly=True,
                samesite="lax",
                path="/",
                secure=settings.cookie_secure,
            )
        return response
