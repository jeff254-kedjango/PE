"""Deterministic coordinate blurring — the server-side reveal gate.

PE/billing_architecture.md §5. The exact lat/lon of a listing is the PAID good
(commercial_model.md §3.4), so list/feed responses must never carry it for an
un-revealed listing. This module blurs a precise point to a ~FUZZ_RADIUS_M
neighbourhood blob.

Two properties matter:

  * DETERMINISTIC per listing — the blurred marker is STABLE across requests. A
    random per-request jitter would (a) look glitchy on the map and (b) let an
    attacker average many requests back to the true point. We seed the offset on
    the listing id (+ the app secret), so the same listing always blurs the same
    way, but the offset is not guessable without the secret.
  * SNAP + OFFSET — we snap to a coarse grid (cell ≈ 2*radius) and add a
    per-listing pseudo-random sub-cell offset, so blobs aren't all sitting on
    tidy grid centres (which would be trivially reversible).

Pure functions; no DB, no Redis. The serializer calls `fuzz_coords` for any
listing the requesting user has not revealed, and `coarse_address` to drop the
house-number-level street string.
"""
from __future__ import annotations

import hashlib
import math
import os

from PE.weespas.core.config import settings

# Blob radius in metres. The conversion lever from commercial_model.md §3.4:
# bigger ⇒ more accessible free map but the exact pin is more worth buying;
# smaller ⇒ the free view may be "good enough" and depress conversion. A/B this.
FUZZ_RADIUS_M: float = float(os.environ.get("BILLING_FUZZ_RADIUS_M", "1000"))

# Metres per degree of latitude (≈ constant). Longitude is scaled by cos(lat).
_M_PER_DEG_LAT = 111_320.0


def _seed(listing_id: str) -> int:
    """A stable 64-bit seed for this listing, salted with the app secret so the
    exact offset isn't reproducible by an outsider who only knows the id."""
    h = hashlib.sha256(f"{settings.secret_key}:{listing_id}".encode()).digest()
    return int.from_bytes(h[:8], "big")


def _unit_offsets(listing_id: str) -> tuple[float, float]:
    """Two deterministic values in [-1, 1) derived from the listing id."""
    s = _seed(listing_id)
    a = ((s & 0xFFFFFFFF) / 0xFFFFFFFF) * 2.0 - 1.0
    b = (((s >> 32) & 0xFFFFFFFF) / 0xFFFFFFFF) * 2.0 - 1.0
    return a, b


def fuzz_coords(lat: float, lon: float, *, listing_id: str,
                radius_m: float | None = None) -> tuple[float, float]:
    """Blur (lat, lon) to a ~radius_m neighbourhood blob, deterministically per
    listing. Returns (fuzz_lat, fuzz_lon) rounded to ~5 decimals (≈1 m grid, well
    under the blob) so the response can't carry spurious precision.
    """
    r = FUZZ_RADIUS_M if radius_m is None else radius_m
    lat = float(lat)
    lon = float(lon)

    # Snap to a grid whose cell ≈ 2*r so the point loses its sub-blob position,
    # then add a per-listing offset up to ±r so blobs aren't pinned to grid centres.
    cell_lat = (2.0 * r) / _M_PER_DEG_LAT
    m_per_deg_lon = _M_PER_DEG_LAT * max(math.cos(math.radians(lat)), 1e-6)
    cell_lon = (2.0 * r) / m_per_deg_lon

    snap_lat = math.floor(lat / cell_lat) * cell_lat + cell_lat / 2.0
    snap_lon = math.floor(lon / cell_lon) * cell_lon + cell_lon / 2.0

    off_a, off_b = _unit_offsets(listing_id)
    fuzz_lat = snap_lat + off_a * (r / _M_PER_DEG_LAT)
    fuzz_lon = snap_lon + off_b * (r / m_per_deg_lon)

    return round(fuzz_lat, 5), round(fuzz_lon, 5)


def coarse_address(street_address: str | None) -> None:
    """The exact street + house number is as revealing as the pin, so it's withheld
    until reveal. We return None (the UI shows location_name/city/county instead).
    Kept as a function so the policy lives in one place and can soften later (e.g.
    keep the street name but drop the number) without touching call sites."""
    return None
