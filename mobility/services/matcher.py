"""Ride matcher — GEOSEARCH nearest drivers, then dispatch over the SSE bus (architecture §5).

    Rider --POST /rides--> matcher: GEOSEARCH Redis for N nearest drivers
                                          PUBLISH ride-events:<driver_id>  (one per matched driver)

Bounded by construction (anti-O(n), S8-style): GEOSEARCH is O(log n + k) and we cap k with
``ride_max_matches``; the freshness filter is one batched ``ZMSCORE`` over those ≤k members; the
publish fan-out is ≤k. Nothing here scans all drivers.

Freshness: Redis GEO has no per-member TTL, so an off-shift driver lingers in the position set.
We never dispatch to a driver whose last server-stamped ping is older than ``driver_stale_seconds``
— checked against the companion ``mobility:drivers:seen`` ZSET (services.geo).

This chunk dispatches to every fresh nearby driver. The KYC eligibility filter + the O(1) Redis
revocation-denylist check are layered on in chunk 4 (the matcher gains an ``eligible``-set
intersection before publish); they are deliberately NOT stubbed here to keep this chunk free of
inert code.
"""
from __future__ import annotations

import logging
import time
import uuid

from redis.exceptions import RedisError

from PE.mobility.core.config import settings
from PE.mobility.services import event_bus
from PE.mobility.services.denylist import denylist_key
from PE.mobility.services.event_bus import get_client
from PE.mobility.services.geo import _ELIGIBLE_KEY, _POS_KEY, _SEEN_KEY

logger = logging.getLogger(__name__)


def _ride_channel(sub: str) -> str:
    """Per-recipient dispatch channel. Same ``ride-events:<user_id>`` shape the doc §5 diagram
    names; each driver/rider holds one SSE subscription to its OWN channel."""
    return f"ride-events:{sub}"


async def _dispatchable_nearby_drivers(
    lat: float, lng: float, radius_m: float, limit: int, *, now: float
) -> list[tuple[str, float]]:
    """Return up to ``limit`` (driver_sub, distance_m) pairs within ``radius_m`` of (lat,lng),
    nearest first, restricted to drivers that are simultaneously:
      * FRESH — last server-stamped ping within ``driver_stale_seconds`` (on-shift), AND
      * ELIGIBLE — KYC-passed (in the eligible set, refreshed each ping from dispatch:eligible), AND
      * NOT DENIED — absent from the revocation denylist (a banned driver never receives rides).

    Two Redis round-trips regardless of driver count: one radius query (≤limit members) and one
    pipelined batch of three membership/score checks per candidate. O(log n + k).

    Uses GEORADIUS rather than the newer GEOSEARCH: the deployed Redis is 6.0 (GEOSEARCH needs
    6.2+). GEORADIUS is the same geohash-backed O(log n + k) radius query with the same COUNT cap
    and stays fully supported. Switch to GEOSEARCH if/when the deployed Redis is >= 6.2."""
    client = get_client()
    # GEORADIUS returns nearest-first with distances; COUNT caps k.
    raw = await client.georadius(
        _POS_KEY,
        longitude=lng,
        latitude=lat,
        radius=radius_m,
        unit="m",
        sort="ASC",
        count=limit,
        withdist=True,
    )
    # raw is a list of [member, distance] (decode_responses=True ⇒ str member, str/float dist).
    candidates = [(str(m), float(d)) for m, d in raw]
    if not candidates:
        return []
    # One batched read over the ≤k candidates (never a scan of any full set). Three checks per
    # candidate — freshness (ZSCORE), eligibility (SISMEMBER eligible), revocation (SISMEMBER
    # denylist) — issued in a single pipeline: still ONE network round-trip and O(k). (ZSCORE in a
    # pipeline rather than ZMSCORE because the deployed Redis is 6.0.)
    pipe = client.pipeline(transaction=False)
    for sub, _ in candidates:
        pipe.zscore(_SEEN_KEY, sub)
        pipe.sismember(_ELIGIBLE_KEY, sub)
        pipe.sismember(denylist_key(), sub)
    flat = await pipe.execute()
    cutoff = now - settings.driver_stale_seconds
    dispatchable: list[tuple[str, float]] = []
    for i, (sub, dist) in enumerate(candidates):
        score, is_eligible, is_denied = flat[3 * i], flat[3 * i + 1], flat[3 * i + 2]
        fresh = score is not None and float(score) >= cutoff
        if fresh and is_eligible and not is_denied:
            dispatchable.append((sub, dist))
    return dispatchable


async def match_and_dispatch(
    rider_sub: str, rider_name: str, lat: float, lng: float, radius_m: float
) -> dict:
    """Create a ride, find fresh nearby drivers, and publish a ``ride_request`` to each driver's
    channel. Returns a summary for the rider (ride_id + how many drivers were pinged).

    Radius is clamped to ``ride_max_radius_m`` by the caller. A per-driver publish failure is
    logged and skipped — one unreachable channel must never sink the whole dispatch (best-effort
    fan-out); a total Redis outage surfaces as the GEOSEARCH raising, which the router maps to 503.
    The driver event carries the pickup + a correlation ``ride_id`` and the rider's DISPLAY name
    only — never the rider's ``sub`` (that is the rider's private channel handle)."""
    now = time.time()
    drivers = await _dispatchable_nearby_drivers(
        lat, lng, radius_m, settings.ride_max_matches, now=now
    )
    ride_id = uuid.uuid4().hex
    dispatched = 0
    for driver_sub, dist in drivers:
        event = {
            "kind": "ride_request",
            "ride_id": ride_id,
            "pickup": {"lat": lat, "lng": lng},
            "distance_m": round(dist, 1),
            "rider_name": rider_name or "Rider",
        }
        try:
            await event_bus.publish(_ride_channel(driver_sub), event)
            dispatched += 1
        except RedisError:
            # Best-effort: skip this driver, keep dispatching the rest.
            logger.warning("matcher: publish failed for driver %s (ride %s)", driver_sub, ride_id)
    return {"ride_id": ride_id, "drivers_matched": len(drivers), "drivers_notified": dispatched}
