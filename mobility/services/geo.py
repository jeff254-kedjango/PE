"""Live driver-position store — Redis GEO (architecture §4).

Driver GPS is ephemeral and high-churn (a boda pings every 3–5 s). Redis GEO (a geohash-scored
sorted set) is purpose-built for it: O(1) ``GEOADD`` writes, O(log n + k) ``GEOSEARCH`` radius
reads, and no MVCC dead-tuple / VACUUM write-amplification that a PostGIS GiST index would suffer
under this write rate (doc §4). Positions are ephemeral by nature — if Redis loses them the next
ping repopulates — so nothing here is a durable source of truth.

Two keys, both mobility-scoped on the service's own Redis DB index (config.redis_url = db 4):
  * ``mobility:drivers:pos``  — GEO set: member = driver ``sub``, the geohash-scored position.
  * ``mobility:drivers:seen`` — ZSET: member = driver ``sub``, score = server-stamped unix ping
    time. Redis GEO has no per-member TTL, so a stale/off-shift driver would otherwise linger in
    the GEO set forever; this companion ZSET lets the matcher (chunk 2) filter to drivers seen
    within ``driver_stale_seconds`` and lets a sweep trim old members. Server-stamped (never a
    client-supplied timestamp) so a driver can't forge freshness.

Reuses the ONE shared async Redis pool from services.event_bus (same instance, same db index) —
no second client. All calls are async (mobility is async end-to-end, doc §4).
"""
from __future__ import annotations

import time

from PE.mobility.services.event_bus import get_client

# Mobility-scoped Redis keys (self-contained on db 4).
_POS_KEY = "mobility:drivers:pos"
_SEEN_KEY = "mobility:drivers:seen"
# Drivers currently KYC-eligible for dispatch (doc §16). Membership is refreshed on EVERY ping
# from the token's dispatch:eligible scope — so if weespas revokes a driver's KYC, the very next
# ping (≤3–5 s later) drops them from this set and the matcher stops dispatching to them. A
# stateless token can't be un-minted before its TTL, but this per-ping refresh gives near-real-time
# eligibility without a per-ping denylist round-trip.
_ELIGIBLE_KEY = "mobility:drivers:eligible"


def _valid_lat(lat: float) -> bool:
    # Excludes NaN/inf: a NaN comparison is always False, so the range test rejects it.
    return -90.0 <= lat <= 90.0


def _valid_lng(lng: float) -> bool:
    return -180.0 <= lng <= 180.0


async def record_ping(
    sub: str, lat: float, lng: float, *, eligible: bool, now: float | None = None
) -> float:
    """Upsert a driver's live position, stamp its last-seen time (server clock), and refresh its
    dispatch eligibility from the token's ``dispatch:eligible`` scope.

    Returns the server timestamp stamped. Raises ``ValueError`` on an out-of-range/NaN coordinate
    — the caller maps that to 422 (never store a garbage point that would poison GEORADIUS). The
    writes are pipelined in one round-trip. They are not wrapped in a MULTI: a torn write self-heals
    on the next 3-second ping and only ever makes a driver momentarily look stale/ineligible, never
    mis-located. ``eligible`` toggles the driver in/out of the eligible set every ping, so a
    KYC revocation upstream propagates within one ping interval."""
    if not (_valid_lat(lat) and _valid_lng(lng)):
        raise ValueError("latitude/longitude out of range")
    ts = time.time() if now is None else now
    pipe = get_client().pipeline(transaction=False)
    # GEOADD member=sub at (lng, lat) — note Redis takes longitude FIRST.
    pipe.geoadd(_POS_KEY, (lng, lat, sub))
    # Record freshness; score is the server-stamped unix time.
    pipe.zadd(_SEEN_KEY, {sub: ts})
    # Refresh eligibility: present in the scope ⇒ add, absent ⇒ remove.
    if eligible:
        pipe.sadd(_ELIGIBLE_KEY, sub)
    else:
        pipe.srem(_ELIGIBLE_KEY, sub)
    await pipe.execute()
    return ts


async def last_seen(sub: str) -> float | None:
    """The server timestamp of a driver's most recent ping, or None if never seen."""
    score = await get_client().zscore(_SEEN_KEY, sub)
    return float(score) if score is not None else None
