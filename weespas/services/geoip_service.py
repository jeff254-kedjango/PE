"""GeoIP lookup using MaxMind GeoLite2-City. Optional — degrades gracefully if DB missing."""
from __future__ import annotations

import logging
import os
from typing import Optional

from PE.weespas.core.config import settings

logger = logging.getLogger(__name__)

_reader = None
_init_attempted = False


def _get_reader():
    global _reader, _init_attempted
    if _init_attempted:
        return _reader
    _init_attempted = True
    path = settings.geoip_db_path
    if not os.path.exists(path):
        logger.warning("GeoIP DB not found at %s — geo IP lookups disabled", path)
        return None
    try:
        import geoip2.database  # type: ignore
        _reader = geoip2.database.Reader(path)
        logger.info("GeoIP reader initialized from %s", path)
    except Exception as e:
        logger.warning("Failed to init GeoIP reader: %s", e)
    return _reader


def lookup_ip(ip: str) -> Optional[dict]:
    """Return {lat, lng, city, county} or None when lookup fails / DB missing."""
    if not ip or ip in ("127.0.0.1", "::1", "localhost"):
        return None
    reader = _get_reader()
    if not reader:
        return None
    try:
        resp = reader.city(ip)
        return {
            "lat": float(resp.location.latitude) if resp.location.latitude else None,
            "lng": float(resp.location.longitude) if resp.location.longitude else None,
            "city": resp.city.name,
            "county": resp.subdivisions.most_specific.name if resp.subdivisions else None,
        }
    except Exception:
        return None
