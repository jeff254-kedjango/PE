"""Coordinate fuzzing tests (PE/billing_architecture.md §5, step 2).

Pins the security-relevant properties: the blur is bounded to ~radius, it's
DETERMINISTIC per listing (stable across calls, so it neither glitches nor can be
averaged back to the true point), different listings blur differently, and the
street address is withheld until reveal.
"""
import math

from PE.weespas.services import geo_fuzz


# A real Nairobi-ish point.
LAT, LON = -1.2921, 36.8219


def _metres_between(a, b):
    lat1, lon1 = a
    lat2, lon2 = b
    m_per_deg_lat = 111_320.0
    dlat = (lat2 - lat1) * m_per_deg_lat
    dlon = (lon2 - lon1) * m_per_deg_lat * math.cos(math.radians(lat1))
    return math.hypot(dlat, dlon)


def test_fuzz_is_deterministic_per_listing():
    a = geo_fuzz.fuzz_coords(LAT, LON, listing_id="L1")
    b = geo_fuzz.fuzz_coords(LAT, LON, listing_id="L1")
    assert a == b   # stable across calls — never jitters per request


def test_fuzz_differs_by_listing():
    a = geo_fuzz.fuzz_coords(LAT, LON, listing_id="L1")
    b = geo_fuzz.fuzz_coords(LAT, LON, listing_id="L2")
    assert a != b   # same point, different listing → different blob offset


def test_fuzz_moves_the_point_but_stays_in_neighbourhood():
    fz = geo_fuzz.fuzz_coords(LAT, LON, listing_id="L1", radius_m=1000)
    d = _metres_between((LAT, LON), fz)
    # It must actually move (not return the exact point)...
    assert d > 1.0
    # ...but stay within a neighbourhood blob. Snap-to-grid (cell≈2r) + offset(±r)
    # bounds the displacement to a few grid cells; assert a generous ceiling.
    assert d < 4 * 1000


def test_fuzz_radius_scales_displacement():
    # A wider radius should, on average, push points further. Compare a handful of
    # listings so we're not asserting on a single unlucky offset.
    ids = [f"L{i}" for i in range(20)]
    near = sum(_metres_between((LAT, LON), geo_fuzz.fuzz_coords(LAT, LON, listing_id=i, radius_m=200)) for i in ids)
    far = sum(_metres_between((LAT, LON), geo_fuzz.fuzz_coords(LAT, LON, listing_id=i, radius_m=2000)) for i in ids)
    assert far > near


def test_fuzz_rounds_to_5dp_no_spurious_precision():
    fz_lat, fz_lon = geo_fuzz.fuzz_coords(LAT, LON, listing_id="L1")
    assert fz_lat == round(fz_lat, 5)
    assert fz_lon == round(fz_lon, 5)


def test_coarse_address_withholds_street():
    assert geo_fuzz.coarse_address("12 Ngong Road, Apt 4B") is None
    assert geo_fuzz.coarse_address(None) is None


def test_fuzz_handles_equatorial_and_high_latitude():
    # cos(lat) scaling must not blow up near the equator or at high latitude.
    for lat in (0.0, -1.29, 60.0, -60.0):
        fz = geo_fuzz.fuzz_coords(lat, 36.8, listing_id="L1")
        assert -90 <= fz[0] <= 90 and -180 <= fz[1] <= 180
