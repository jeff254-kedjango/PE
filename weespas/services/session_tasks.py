"""Session-related Celery tasks — runs on the default queue."""
from __future__ import annotations

import logging

from PE.weespas.core.celery_app import celery_app
from PE.weespas.core.database import SessionLocal
from PE.weespas.models.analytics import UserSession
from PE.weespas.services.geoip_service import lookup_ip

logger = logging.getLogger(__name__)


@celery_app.task(
    name="session.enrich_geo",
    ignore_result=True,
    acks_late=False,
)
def enrich_session_geo(session_id: str, ip: str | None) -> None:
    """Resolve GeoIP for a UserSession row, off the request thread.

    The session row is already INSERTed by the middleware with geo_*=NULL —
    we only fill the fields. We refuse to overwrite an existing geo_lat so a
    second request from the same session never wastes a MaxMind read.
    """
    if not ip:
        return
    geo = lookup_ip(ip)
    if not geo:
        return

    db = SessionLocal()
    try:
        row = db.query(UserSession).filter(UserSession.id == session_id).first()
        # Only enrich if still unset — saves a write if a parallel task beat us.
        if row is None or row.geo_lat is not None:
            return
        row.geo_lat = geo.get("lat")
        row.geo_lng = geo.get("lng")
        row.geo_city = geo.get("city")
        row.geo_county = geo.get("county")
        row.geo_source = "ip"
        db.commit()
    except Exception as exc:
        logger.debug("enrich_session_geo(%s) failed: %s", session_id, exc)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()
