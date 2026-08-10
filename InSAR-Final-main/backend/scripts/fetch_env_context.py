"""
Real environmental-context fetchers — the real-data sources behind
`join_insar.build_real_env` (soil/shoreline/riparian).

Each fetcher:
  - takes an AOI + (where relevant) building centroids and observation dates
  - downloads the source data into `backend/data/raw/env/<source>/<aoi>/...`
    so reruns are cache hits, not network calls
  - returns per-building arrays (riparian/shoreline/soil/NDVI) or per-AOI
    quarterly scalars (CHIRPS/GRACE)

The integration point is `assemble_env_context(aoi, lons, lats, periods)` which
calls every fetcher and returns a single dict ready to drop into the parquet
pipeline. Anything that fails at network time is logged and left NaN/None so
the pipeline still completes and the UI can label the column missing.

Honesty:
  - SoilGrids ISRIC WRB Reference Soil Groups: real, 250 m raster, sampled at centroid
  - OSM Overpass waterways / coastline: real, distance-to-nearest from centroid
  - CHIRPS-2.0 monthly precip: real, ~5 km, sampled at AOI center, z-score per quarter
  - GRACE-FO mascons (JPL RL06): real, ~300 km, AOI-wide TWS anomaly
  - Sentinel-2 L2A NDVI via Microsoft Planetary Computer: real, monthly median
  - `reclaimed_land`: derived downstream from the real `soil_class` — SoilGrids
    classifies engineered fill as WRB Technosols/Anthrosols, which
    `_wrb_code_to_local` maps to `reclaim_fill` on coastal AOIs. The seed path
    (phenomena.py) keys the boolean off `soil_class == "reclaim_fill"` rather than
    a polygon layer, so this fetcher exposes only `soil_class` and leaves the flag
    to that rule.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

import numpy as np

from scripts.aois import AOI, bbox as aoi_bbox


BACKEND_DIR = Path(__file__).resolve().parents[1]
RAW_ENV_DIR = BACKEND_DIR / "data" / "raw" / "env"
RAW_ENV_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================================
# Soil-class translation
# ============================================================================
#
# `phenomena.py::composite_risk` uses a 6-category enum:
#     black_cotton, alluvial, red_clay, weathered_basalt, coral_rag, reclaim_fill
#
# SoilGrids exposes the WRB Reference Soil Groups (RSG). Mapping is approximate
# but defensible — each WRB group maps to the most-similar local class:

# Coastal WRB groups that map to coral_rag ONLY when the AOI is coastal.
# Inland Calcisols/Arenosols are calcium- or sand-rich subsoils, not marine
# coral — they behave more like weathered basement. Resolved at call time.
COASTAL_WRB: set[str] = {"Calcisols", "Arenosols", "Solonchaks"}

# Default WRB → local-enum mapping. AOI-aware overrides (coastal vs inland)
# are applied in `_wrb_code_to_local`.
WRB_TO_LOCAL: dict[str, str] = {
    # Expanding clays — black cotton soils in East Africa are typically
    # Vertisols. Cracking clays = high shrink-swell = high subsidence risk.
    "Vertisols":  "black_cotton",
    # Floodplain / fluvial deposits.
    "Fluvisols":  "alluvial",
    "Gleysols":   "alluvial",
    # Weathered red soils, common over basement geology in Nairobi.
    "Nitisols":   "red_clay",
    "Ferralsols": "red_clay",
    "Acrisols":   "red_clay",
    "Lixisols":   "red_clay",
    "Luvisols":   "red_clay",
    "Cambisols":  "red_clay",
    # Hard subsoil layers (silica/calcium cemented). Treat as moderately
    # stable bearing — red_clay is the conservative match in our enum.
    "Durisols":   "red_clay",
    # Volcanic / basaltic — common around Nairobi's volcanic substrate.
    "Andosols":   "weathered_basalt",
    "Leptosols":  "weathered_basalt",
    "Regosols":   "weathered_basalt",
    # Calcium / sand / saline — coastal context only (override below).
    "Calcisols":  "weathered_basalt",
    "Arenosols":  "weathered_basalt",
    "Solonchaks": "weathered_basalt",
    # Anthropic / engineered ground — reclaim_fill encodes catastrophic
    # compressibility. Only assign it on the coastal AOI where Mombasa's
    # actually reclaimed; inland Technosols are just urbanised topsoil.
    "Anthrosols": "red_clay",
    "Technosols": "red_clay",
}

# AOIs where Anthrosols/Technosols should be interpreted as reclaim_fill rather
# than ordinary disturbed ground.
RECLAIM_FILL_AOIS_PHENOMENON: set[str] = {"coastal_subsidence"}

# SoilGrids "Most Probable Class" raster encodes RSG by integer code. The official
# code list (1..32) is documented at:
#   https://files.isric.org/soilgrids/latest/data/wrb/MostProbable.qml
# We only enumerate the codes that actually occur in our two AOIs; the rest fall
# back to the default in `_wrb_code_to_local`.
WRB_CODE_TO_NAME: dict[int, str] = {
    1: "Acrisols", 2: "Albeluvisols", 3: "Alisols", 4: "Andosols",
    5: "Arenosols", 6: "Calcisols", 7: "Cambisols", 8: "Chernozems",
    9: "Cryosols", 10: "Durisols", 11: "Ferralsols", 12: "Fluvisols",
    13: "Gleysols", 14: "Gypsisols", 15: "Histosols", 16: "Kastanozems",
    17: "Leptosols", 18: "Lixisols", 19: "Luvisols", 20: "Nitisols",
    21: "Phaeozems", 22: "Planosols", 23: "Plinthosols", 24: "Podzols",
    25: "Regosols", 26: "Solonchaks", 27: "Solonetz", 28: "Stagnosols",
    29: "Technosols", 30: "Umbrisols", 31: "Vertisols", 32: "Anthrosols",
}


def _wrb_code_to_local(code: int, aoi: AOI) -> str:
    """Map SoilGrids WRB code → local 6-class enum, with AOI-aware overrides
    for coastal vs inland interpretation. Defaults to red_clay if unknown."""
    name = WRB_CODE_TO_NAME.get(int(code))
    if not name:
        return "red_clay"
    # Coastal override: Calcisols/Arenosols/Solonchaks → coral_rag on Mombasa
    if aoi.phenomenon == "coastal_subsidence" and name in COASTAL_WRB:
        return "coral_rag"
    # Reclaim_fill override: Technosols/Anthrosols on the coastal AOI → fill.
    if aoi.phenomenon in RECLAIM_FILL_AOIS_PHENOMENON and name in ("Technosols", "Anthrosols"):
        return "reclaim_fill"
    return WRB_TO_LOCAL.get(name, "red_clay")


# ============================================================================
# OSM Overpass — waterways (riparian_dist_m) and coastline (shoreline_dist_m)
# ============================================================================

OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]


def _overpass_query(query: str, *, cache_path: Path) -> dict:
    """Run an Overpass query, caching the JSON response.

    Tries each mirror in turn — the main de.overpass instance is frequently
    rate-limited, so kumi/osm.ch are real fallbacks. Once any mirror returns
    200, the result is cached and no more network calls happen.
    """
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists() and cache_path.stat().st_size > 0:
        with cache_path.open() as f:
            return json.load(f)

    import requests
    headers = {"User-Agent": "infra-proptech/0.1 (Nairobi subsidence MVP; ops@infra-proptech.local)"}
    last_err: Exception | None = None
    payload: dict | None = None
    for url in OVERPASS_MIRRORS:
        print(f"  → overpass {url}: {query.splitlines()[0]!r}", file=sys.stderr)
        try:
            r = requests.post(url, data={"data": query}, headers=headers, timeout=180)
            r.raise_for_status()
            payload = r.json()
            break
        except Exception as e:
            last_err = e
            print(f"    failed: {e}", file=sys.stderr)
            continue
    if payload is None:
        raise RuntimeError(f"all Overpass mirrors failed; last error: {last_err}")

    features: list[dict] = []
    for el in payload.get("elements", []):
        geom = el.get("geometry")
        if not geom:
            continue
        coords = [[float(p["lon"]), float(p["lat"])] for p in geom]
        if len(coords) < 2:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"osm_id": el.get("id"), "tags": el.get("tags", {})},
        })
    out = {"type": "FeatureCollection", "features": features}
    with cache_path.open("w") as f:
        json.dump(out, f)
    return out


def _distance_to_lines_m(
    centroid_lons: np.ndarray,
    centroid_lats: np.ndarray,
    lines: list[list[tuple[float, float]]],
    aoi: AOI,
) -> np.ndarray:
    """Distance (m) from each centroid to the nearest point on any line.

    Reprojects to AOI-local metres (equirectangular at AOI center) so distances
    are physically meaningful and shapely can use a planar tree.
    """
    if not lines:
        return np.full(centroid_lons.size, np.nan, dtype=np.float64)

    from shapely.geometry import LineString, Point
    from shapely.strtree import STRtree

    # Reproject AOI center as origin; metres per degree at that latitude.
    lat0 = aoi.center_lat
    lon0 = aoi.center_lon
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(lat0))

    def to_local(lon: float, lat: float) -> tuple[float, float]:
        return ((lon - lon0) * m_per_deg_lon, (lat - lat0) * m_per_deg_lat)

    proj_lines = [
        LineString([to_local(lon, lat) for (lon, lat) in line])
        for line in lines
    ]
    tree = STRtree(proj_lines)
    # Some shapely 2.x versions return indices, others return geoms — handle both.
    out = np.empty(centroid_lons.size, dtype=np.float64)
    for i in range(centroid_lons.size):
        x, y = to_local(float(centroid_lons[i]), float(centroid_lats[i]))
        p = Point(x, y)
        nearest_idx = tree.nearest(p)
        if isinstance(nearest_idx, (int, np.integer)):
            out[i] = proj_lines[int(nearest_idx)].distance(p)
        else:
            out[i] = nearest_idx.distance(p)
    return out


def fetch_riparian_dist_m(
    aoi: AOI,
    centroid_lons: np.ndarray,
    centroid_lats: np.ndarray,
) -> np.ndarray:
    """Distance (m) from each building centroid to nearest OSM waterway.

    Returns NaN per building if Overpass is unreachable AND no cached file
    exists — caller decides whether to fall back to synthesized values.

    `waterway=*` covers rivers, streams, drains, canals — all things that
    contribute to riparian-zone subsidence risk.
    """
    minlon, minlat, maxlon, maxlat = aoi_bbox(aoi)
    query = f"""
[out:json][timeout:60];
(
  way["waterway"]({minlat},{minlon},{maxlat},{maxlon});
);
out geom;
""".strip()
    cache = RAW_ENV_DIR / "osm" / f"waterways_{aoi.code}.geojson"
    try:
        fc = _overpass_query(query, cache_path=cache)
    except Exception as e:
        print(f"  ! overpass waterways failed for {aoi.code}: {e}", file=sys.stderr)
        return np.full(centroid_lons.size, np.nan, dtype=np.float64)
    lines = [f["geometry"]["coordinates"] for f in fc["features"]]
    if not lines:
        # Real answer: no waterways in the bbox. NaN tells the composite to
        # contribute 0 from this term — matches the synthetic Mombasa behavior.
        return np.full(centroid_lons.size, np.nan, dtype=np.float64)
    return _distance_to_lines_m(centroid_lons, centroid_lats, lines, aoi)


def fetch_shoreline_dist_m(
    aoi: AOI,
    centroid_lons: np.ndarray,
    centroid_lats: np.ndarray,
) -> np.ndarray:
    """Distance (m) from each centroid to nearest OSM coastline."""
    minlon, minlat, maxlon, maxlat = aoi_bbox(aoi)
    # Coastlines often extend beyond the bbox — pad by 0.05° so we don't miss
    # a stretch just south of the box. Distance results stay valid.
    pad = 0.05
    query = f"""
[out:json][timeout:60];
(
  way["natural"="coastline"]({minlat-pad},{minlon-pad},{maxlat+pad},{maxlon+pad});
);
out geom;
""".strip()
    cache = RAW_ENV_DIR / "osm" / f"coastline_{aoi.code}.geojson"
    # Inland AOIs (Huruma) genuinely have no coastline — write an empty cache so
    # the next run is a no-op, not another 504-timeout poll.
    if not aoi.phenomenon == "coastal_subsidence":
        cache.parent.mkdir(parents=True, exist_ok=True)
        if not cache.exists():
            cache.write_text('{"type":"FeatureCollection","features":[]}')
        return np.full(centroid_lons.size, np.nan, dtype=np.float64)
    try:
        fc = _overpass_query(query, cache_path=cache)
    except Exception as e:
        print(f"  ! overpass coastline failed for {aoi.code}: {e}", file=sys.stderr)
        return np.full(centroid_lons.size, np.nan, dtype=np.float64)
    lines = [f["geometry"]["coordinates"] for f in fc["features"]]
    if not lines:
        return np.full(centroid_lons.size, np.nan, dtype=np.float64)
    return _distance_to_lines_m(centroid_lons, centroid_lats, lines, aoi)


# ============================================================================
# SoilGrids — WRB Reference Soil Groups (Most Probable Class)
# ============================================================================

SOILGRIDS_WCS = (
    "https://maps.isric.org/mapserv?map=/map/wrb.map"
    "&SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage"
    "&COVERAGEID=MostProbable"
    "&FORMAT=image/tiff"
    "&SUBSET=long({minlon},{maxlon})"
    "&SUBSET=lat({minlat},{maxlat})"
    "&SUBSETTINGCRS=http://www.opengis.net/def/crs/EPSG/0/4326"
    "&OUTPUTCRS=http://www.opengis.net/def/crs/EPSG/0/4326"
)


def fetch_soil_class(
    aoi: AOI,
    centroid_lons: np.ndarray,
    centroid_lats: np.ndarray,
) -> np.ndarray:
    """Sample SoilGrids WRB Most-Probable-Class raster at each centroid.

    Returns an object array of strings matching the 6-class enum used by
    `composite_risk`. If the raster can't be fetched/opened, returns an
    array of `None` so the caller can fall back.
    """
    minlon, minlat, maxlon, maxlat = aoi_bbox(aoi)
    # Pad slightly so a centroid right at the bbox edge still has a pixel.
    pad = 0.005
    url = SOILGRIDS_WCS.format(
        minlon=minlon - pad, maxlon=maxlon + pad,
        minlat=minlat - pad, maxlat=maxlat + pad,
    )
    cache = RAW_ENV_DIR / "soilgrids" / f"{aoi.code}_wrb_mpc.tif"
    cache.parent.mkdir(parents=True, exist_ok=True)

    if not cache.exists() or cache.stat().st_size == 0:
        import requests
        print(f"  → SoilGrids WCS: {aoi.code}", file=sys.stderr)
        try:
            r = requests.get(url, timeout=180)
            r.raise_for_status()
            cache.write_bytes(r.content)
        except Exception as e:
            print(f"  ! SoilGrids fetch failed for {aoi.code}: {e}", file=sys.stderr)
            return np.full(centroid_lons.size, None, dtype=object)

    try:
        import rasterio
        with rasterio.open(cache) as src:
            # Sample one band at every centroid in a single vectorised call.
            samples = list(src.sample(
                [(float(lon), float(lat))
                 for lon, lat in zip(centroid_lons, centroid_lats)]
            ))
            # Also load the full band so we can do a nearest-neighbour-of-valid
            # search for centroids that hit a nodata pixel (the SoilGrids WCS
            # response masks built-up cores).
            band = src.read(1)
            transform = src.transform
    except Exception as e:
        print(f"  ! SoilGrids raster sample failed for {aoi.code}: {e}", file=sys.stderr)
        return np.full(centroid_lons.size, None, dtype=object)

    # Precompute valid-pixel coordinates in raster-grid (row, col) and their
    # WGS84 centers, so we can answer "nearest valid pixel" in O(N_valid).
    valid_mask = (band > 0) & (band <= 32)
    rows_v, cols_v = np.where(valid_mask)
    valid_codes = band[rows_v, cols_v]
    if valid_codes.size:
        valid_lons = np.empty(valid_codes.size, dtype=np.float64)
        valid_lats = np.empty(valid_codes.size, dtype=np.float64)
        for k in range(valid_codes.size):
            x, y = rasterio.transform.xy(transform, int(rows_v[k]), int(cols_v[k]))
            valid_lons[k] = x
            valid_lats[k] = y
    else:
        valid_lons = valid_lats = np.empty(0, dtype=np.float64)

    out = np.empty(centroid_lons.size, dtype=object)
    n_fallback = 0
    for i, s in enumerate(samples):
        code = int(s[0]) if s is not None and len(s) and s[0] is not None else 0
        if code > 0 and code <= 32:
            out[i] = _wrb_code_to_local(code, aoi)
            continue
        # Nodata pixel — fall back to nearest valid pixel by planar distance.
        if valid_codes.size == 0:
            out[i] = None
            continue
        dlon = valid_lons - float(centroid_lons[i])
        dlat = valid_lats - float(centroid_lats[i])
        idx = int(np.argmin(dlon * dlon + dlat * dlat))
        out[i] = _wrb_code_to_local(int(valid_codes[idx]), aoi)
        n_fallback += 1
    if n_fallback:
        print(f"  · {n_fallback}/{centroid_lons.size} centroids used nearest-valid-pixel fallback",
              file=sys.stderr)
    return out


# ============================================================================
# CHIRPS — monthly rainfall anomaly
# ============================================================================

CHIRPS_MONTHLY_URL = (
    "https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_monthly/cogs/"
    "chirps-v2.0.{year}.{month:02d}.cog"
)


@dataclass
class QuarterlyValue:
    """Per-AOI scalar at a given period_start (first day of quarter)."""
    period: date
    value: float | None


def _months_in_window(periods: Iterable[date]) -> list[tuple[int, int]]:
    """Expand a list of quarter-start dates into the (year, month) tuples
    that cover the full observation window."""
    months: set[tuple[int, int]] = set()
    for p in periods:
        for k in range(3):
            m = p.month + k
            y = p.year + (m - 1) // 12
            m = ((m - 1) % 12) + 1
            months.add((y, m))
    return sorted(months)


def fetch_chirps_anomaly(
    aoi: AOI,
    periods: list[date],
) -> list[QuarterlyValue]:
    """Per-quarter rainfall z-score anomaly at AOI center.

    CHIRPS-2.0 monthly grid is 5 km. We download one COG per (year, month)
    in the observation window, sample at the AOI center, and compute a
    z-score per quarter using the climatology of the whole 24/60-month
    window as the reference distribution.

    Real signal, real source. Cached per (year, month) under
    `data/raw/env/chirps/`. Returns one value per period; None if the
    download or sample fails.
    """
    cache_dir = RAW_ENV_DIR / "chirps"
    cache_dir.mkdir(parents=True, exist_ok=True)

    months = _months_in_window(periods)
    monthly_mm: dict[tuple[int, int], float] = {}

    import requests
    try:
        import rasterio
    except ImportError:
        print("  ! rasterio not available for CHIRPS sampling", file=sys.stderr)
        return [QuarterlyValue(p, None) for p in periods]

    for (y, m) in months:
        cog = cache_dir / f"chirps_{y}_{m:02d}.cog"
        if not cog.exists() or cog.stat().st_size == 0:
            url = CHIRPS_MONTHLY_URL.format(year=y, month=m)
            try:
                print(f"  → CHIRPS: {y}-{m:02d}", file=sys.stderr)
                r = requests.get(url, timeout=120)
                r.raise_for_status()
                cog.write_bytes(r.content)
            except Exception as e:
                print(f"  ! CHIRPS {y}-{m:02d} fetch failed: {e}", file=sys.stderr)
                continue
        try:
            with rasterio.open(cog) as src:
                vals = list(src.sample([(aoi.center_lon, aoi.center_lat)]))
            v = float(vals[0][0])
            if v < 0:  # CHIRPS nodata is typically -9999
                continue
            monthly_mm[(y, m)] = v
        except Exception as e:
            print(f"  ! CHIRPS {y}-{m:02d} sample failed: {e}", file=sys.stderr)
            continue

    if not monthly_mm:
        return [QuarterlyValue(p, None) for p in periods]

    series = np.array(list(monthly_mm.values()), dtype=np.float64)
    mean_mm = float(series.mean())
    std_mm = float(series.std()) or 1.0

    out: list[QuarterlyValue] = []
    for p in periods:
        # Quarter total = sum of the three months starting at p.
        total = 0.0
        n_have = 0
        for k in range(3):
            mm = p.month + k
            yy = p.year + (mm - 1) // 12
            mm = ((mm - 1) % 12) + 1
            if (yy, mm) in monthly_mm:
                total += monthly_mm[(yy, mm)]
                n_have += 1
        if n_have == 0:
            out.append(QuarterlyValue(p, None))
            continue
        # Reported value: rainfall anomaly in mm — quarter total minus the
        # equivalent climatology total. Composite-risk treats positive values
        # as "wetter than average".
        clim_total = mean_mm * 3.0
        out.append(QuarterlyValue(p, total - clim_total))
    return out


# ============================================================================
# GRACE-FO — terrestrial water storage anomaly
# ============================================================================

# JPL RL06 mascon Level-3 dataset on the GES DISC OPeNDAP server.
# Single ~80 MB NetCDF covering the entire mission timeline.
GRACE_JPL_URL = (
    "https://podaac-opendap.jpl.nasa.gov/opendap/allData/tellus/L3/mascon/RL06.1/"
    "JPL/v04/CRI/netcdf/GRCTellus.JPL.200204_202407.GLO.RL06.1M.MSCNv04CRI.nc"
)


def fetch_grace_anomaly(
    aoi: AOI,
    periods: list[date],
) -> list[QuarterlyValue]:
    """AOI-wide terrestrial water storage anomaly per quarter (cm of EWH).

    GRACE-FO mascons are ~3° (≈330 km). For our two AOIs the nearest mascon
    cell value is the right scalar; we don't interpolate. Returns a value per
    quarter (latest GRACE month that falls inside the quarter).
    """
    cache = RAW_ENV_DIR / "grace" / "GRCTellus.JPL.GLO.nc"
    cache.parent.mkdir(parents=True, exist_ok=True)

    if not cache.exists() or cache.stat().st_size == 0:
        import requests
        print("  → GRACE JPL mascon download (~80 MB)", file=sys.stderr)
        try:
            # PO.DAAC OPeNDAP allows anonymous HTTPS GET of the .nc.
            r = requests.get(GRACE_JPL_URL, timeout=300, stream=True)
            r.raise_for_status()
            with cache.open("wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
        except Exception as e:
            print(f"  ! GRACE fetch failed: {e}", file=sys.stderr)
            return [QuarterlyValue(p, None) for p in periods]

    try:
        import xarray as xr
        ds = xr.open_dataset(cache)
    except Exception as e:
        print(f"  ! GRACE open failed: {e}", file=sys.stderr)
        return [QuarterlyValue(p, None) for p in periods]

    try:
        # JPL mascon var name: lwe_thickness (Liquid Water Equivalent, cm).
        # Longitude is 0..360 in this file; convert AOI lon if negative.
        lon_360 = aoi.center_lon if aoi.center_lon >= 0 else aoi.center_lon + 360
        twsa = ds["lwe_thickness"].sel(
            lat=aoi.center_lat, lon=lon_360, method="nearest"
        )
        times = ds["time"].values  # numpy datetime64[ns]
        vals = twsa.values         # cm of equivalent water height
    finally:
        ds.close()

    # Build the per-quarter value: average of all monthly mascon estimates
    # whose timestamp falls inside [period, period + 3 months).
    out: list[QuarterlyValue] = []
    for p in periods:
        next_q_y = p.year + ((p.month - 1 + 3) // 12)
        next_q_m = ((p.month - 1 + 3) % 12) + 1
        p_end = date(next_q_y, next_q_m, 1)
        t_start = np.datetime64(p.isoformat())
        t_end = np.datetime64(p_end.isoformat())
        mask = (times >= t_start) & (times < t_end)
        if not mask.any():
            out.append(QuarterlyValue(p, None))
            continue
        out.append(QuarterlyValue(p, float(np.nanmean(vals[mask]))))
    return out


# ============================================================================
# Sentinel-2 L2A — monthly median NDVI via Microsoft Planetary Computer
# ============================================================================

def fetch_ndvi_per_building(
    aoi: AOI,
    centroid_lons: np.ndarray,
    centroid_lats: np.ndarray,
    period: date,
) -> np.ndarray:
    """Median NDVI per building over the quarter starting `period`.

    Uses Microsoft Planetary Computer's STAC catalog (anonymous reads).
    Loads Sentinel-2 L2A B04 (red) and B08 (nir) at 10 m resolution, masks
    clouds via the SCL band, computes median NDVI across the quarter,
    and samples at each centroid.
    """
    try:
        import planetary_computer
        from pystac_client import Client
        import rasterio
        from rasterio.windows import Window
    except ImportError as e:
        print(f"  ! S2 deps missing: {e}", file=sys.stderr)
        return np.full(centroid_lons.size, np.nan, dtype=np.float64)

    minlon, minlat, maxlon, maxlat = aoi_bbox(aoi)
    next_q_y = period.year + ((period.month - 1 + 3) // 12)
    next_q_m = ((period.month - 1 + 3) % 12) + 1
    p_end = date(next_q_y, next_q_m, 1)

    try:
        catalog = Client.open(
            "https://planetarycomputer.microsoft.com/api/stac/v1",
            modifier=planetary_computer.sign_inplace,
        )
        search = catalog.search(
            collections=["sentinel-2-l2a"],
            bbox=[minlon, minlat, maxlon, maxlat],
            datetime=f"{period.isoformat()}/{p_end.isoformat()}",
            query={"eo:cloud_cover": {"lt": 40}},
        )
        items = list(search.items())
    except Exception as e:
        print(f"  ! S2 STAC search failed ({aoi.code} {period}): {e}", file=sys.stderr)
        return np.full(centroid_lons.size, np.nan, dtype=np.float64)

    if not items:
        return np.full(centroid_lons.size, np.nan, dtype=np.float64)

    # Sample each item at every centroid and collect per-building NDVI samples.
    samples: list[np.ndarray] = []
    pts = [(float(lon), float(lat)) for lon, lat in zip(centroid_lons, centroid_lats)]
    for it in items:
        try:
            b04_href = it.assets["B04"].href
            b08_href = it.assets["B08"].href
            with rasterio.open(b04_href) as red, rasterio.open(b08_href) as nir:
                red_vals = np.array([float(v[0]) for v in red.sample(pts)])
                nir_vals = np.array([float(v[0]) for v in nir.sample(pts)])
            with np.errstate(divide="ignore", invalid="ignore"):
                ndvi = (nir_vals - red_vals) / (nir_vals + red_vals)
            ndvi[~np.isfinite(ndvi)] = np.nan
            # S2 L2A scale: surface reflectance × 10000. Anything near 0 is
            # nodata / shadow; throw it out.
            ndvi[(red_vals < 100) & (nir_vals < 100)] = np.nan
            samples.append(ndvi)
        except Exception as e:
            print(f"  ! S2 scene {it.id} sample failed: {e}", file=sys.stderr)
            continue

    if not samples:
        return np.full(centroid_lons.size, np.nan, dtype=np.float64)
    stack = np.vstack(samples)
    return np.nanmedian(stack, axis=0)


# ============================================================================
# Top-level assembly
# ============================================================================

@dataclass
class EnvBundle:
    """All real env-data outputs for one AOI, ready to drop into the parquet
    pipeline. Per-building arrays are indexed in the same order as the
    centroids passed in."""

    soil_class:        np.ndarray   # dtype=object, str or None
    riparian_dist_m:   np.ndarray   # float64, NaN = missing
    shoreline_dist_m:  np.ndarray   # float64, NaN = missing
    rainfall_anom_mm:  dict[date, float | None]  # per-quarter, AOI-scalar
    groundwater_anom:  dict[date, float | None]  # per-quarter, AOI-scalar
    ndvi_per_quarter:  dict[date, np.ndarray]    # per-quarter, per-building


def assemble_env_context(
    aoi: AOI,
    centroid_lons: np.ndarray,
    centroid_lats: np.ndarray,
    periods: list[date],
    *,
    include_ndvi: bool = True,
) -> EnvBundle:
    """Pull every real env source for an AOI. Each source is independent; any
    failure leaves its column missing rather than aborting the whole run.

    `include_ndvi` is a knob because Sentinel-2 fetches are the heaviest step
    (~minutes per quarter). Caller can defer to a separate run.
    """
    print(f"\n[env-context] {aoi.code}: assembling real environmental data...",
          file=sys.stderr)

    soil = fetch_soil_class(aoi, centroid_lons, centroid_lats)
    ripa = fetch_riparian_dist_m(aoi, centroid_lons, centroid_lats)
    shor = fetch_shoreline_dist_m(aoi, centroid_lons, centroid_lats)
    chirps = {q.period: q.value for q in fetch_chirps_anomaly(aoi, periods)}
    grace = {q.period: q.value for q in fetch_grace_anomaly(aoi, periods)}

    ndvi: dict[date, np.ndarray] = {}
    if include_ndvi:
        for p in periods:
            ndvi[p] = fetch_ndvi_per_building(aoi, centroid_lons, centroid_lats, p)
    else:
        for p in periods:
            ndvi[p] = np.full(centroid_lons.size, np.nan, dtype=np.float64)

    return EnvBundle(
        soil_class=soil,
        riparian_dist_m=ripa,
        shoreline_dist_m=shor,
        rainfall_anom_mm=chirps,
        groundwater_anom=grace,
        ndvi_per_quarter=ndvi,
    )


if __name__ == "__main__":
    # Smoke-test entry point: `python -m scripts.fetch_env_context huruma`
    import argparse
    from scripts.aois import by_code

    ap = argparse.ArgumentParser()
    ap.add_argument("aoi")
    ap.add_argument("--no-ndvi", action="store_true")
    args = ap.parse_args()

    a = by_code(args.aoi)
    # Sample 5 fake centroids near the center to exercise each fetcher.
    lon0, lat0 = a.center_lon, a.center_lat
    lons = np.array([lon0 - 0.005, lon0, lon0 + 0.005, lon0, lon0])
    lats = np.array([lat0, lat0 - 0.005, lat0, lat0 + 0.005, lat0])
    periods = [date(2025, 1, 1), date(2025, 4, 1)]

    eb = assemble_env_context(a, lons, lats, periods,
                              include_ndvi=not args.no_ndvi)
    print("soil_class:", eb.soil_class)
    print("riparian_dist_m:", eb.riparian_dist_m)
    print("shoreline_dist_m:", eb.shoreline_dist_m)
    print("rainfall_anom_mm:", eb.rainfall_anom_mm)
    print("groundwater_anom:", eb.groundwater_anom)
    print("ndvi[2025-01-01]:", eb.ndvi_per_quarter[date(2025, 1, 1)])
