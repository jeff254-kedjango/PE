"""
Per-AOI synthetic physics. Each `gen_*` function takes an AOI and produces
three Arrow tables matching the production schema:

  - buildings           (one row per footprint)
  - subsidence          (one row per footprint × monthly observation)
  - env_index           (one row per footprint × quarterly observation)

The numbers are *plausible*, not real: spatial autocorrelation, a few hotspots,
soil/geology biases, sensible noise levels. Replaced by real HyP3+MintPy output
in Phase 4/5; the schema is what survives.

InSAR-derived height (`insar_height_m`) and horizontal east-west drift
(`velocity_horizontal_ew_mm_yr`) follow the physics outlined in
`backend/ARCHITECTURE_ONE.md`. The InSAR height has a per-building Gaussian
noise floor that scales inversely with footprint area (small footprints
suffer more from Sentinel-1's 5×20 m pixel size). The fused height is an
inverse-variance-weighted blend of the floor-count estimate (σ ≈ 1.5 m,
constant) and the InSAR estimate (σ from the noise model). E-W drift is
biased per phenomenon: in Huruma it points toward the synthetic tributary
(failing-bank slide), in Mombasa it points seaward on reclaimed fill.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date
from typing import Callable

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from pathlib import Path
from shapely.geometry import Polygon
from shapely import wkb as shp_wkb

from .aois import AOI
from .postprocess import (
    BUILDINGS_SCHEMA as _BUILDINGS_SCHEMA,
    ENV_SCHEMA as _ENV_SCHEMA,
    SOIL_CLASSES,
    CLASS_INDETERMINATE,
    CLASS_CONFIRMED_THREAT,
    CLASS_ENV_NOISE,
    CLASS_STABLE_ANCHOR,
    CLASS_MIXED_SIGNAL,
    FAILURE_ELASTIC,
    FAILURE_PLASTIC,
    _meters_to_deg,
    _insar_height,
    _fused_height,
    _classify,
    defensibility_thresholds,
    _stl_decompose,
    _velocity_sigma_from_coherence,
    _cohort_percentiles,
    _rank_within_groups,
    assign_blocks,
    _acceleration_mm_yr2,
    _trailing_velocity,
    composite_risk,
    danger_level,
)
from scripts.structural_flags import fetch_structural_flags


# ============================================================================
# Shared building geometry generator
# ============================================================================

# `_meters_to_deg` lives in postprocess.py — imported above for callers below.


@dataclass
class _RawBuilding:
    """Internal: holds local-meter coords for physics, then projected to lon/lat for output.

    When `real_poly` is set (real-footprint path), the geometry is taken
    verbatim from the source parquet instead of being synthesized via
    `_make_polygon`; `x_m`/`y_m`/`w_m`/`h_m` are still filled (from the real
    centroid offset + footprint bbox) so the synthetic velocity/coherence
    physics — which read those fields — keep working unchanged.
    """
    bid: int
    x_m: float
    y_m: float
    w_m: float
    h_m: float
    angle: float
    n_floors: int
    soil_class: str
    riparian_dist_m: float | None
    shoreline_dist_m: float | None
    reclaimed_land: bool | None
    built_year: int
    # Real-footprint provenance (None on the synthetic path). `source_id` is the
    # native id from the footprint file (int64 for OSM); it is routed to the
    # osm_id (int64) or open_buildings_id (string) output column by source.
    real_poly: Polygon | None = None
    source_id: object | None = None
    footprint_source: str = "synthetic"
    # Real measured building height (m, AGL) from the footprint source, when the
    # source provides one (Google Open Buildings does; OSM mostly doesn't). None
    # → fall back to the floor-count height estimate. `confidence` is the
    # source's footprint confidence (0..1), carried for the honesty layer.
    real_height_m: float | None = None
    confidence: float | None = None


def _make_polygon(b: _RawBuilding, aoi: AOI) -> Polygon:
    dlon_per_m, dlat_per_m = _meters_to_deg(aoi.center_lat)
    cos_a, sin_a = math.cos(b.angle), math.sin(b.angle)
    corners = [(-b.w_m/2, -b.h_m/2), (b.w_m/2, -b.h_m/2), (b.w_m/2, b.h_m/2), (-b.w_m/2, b.h_m/2)]
    rotated = [(cx*cos_a - cy*sin_a + b.x_m, cx*sin_a + cy*cos_a + b.y_m) for cx, cy in corners]
    lonlat = [(aoi.center_lon + x * dlon_per_m, aoi.center_lat + y * dlat_per_m) for x, y in rotated]
    return Polygon(lonlat)


# ============================================================================
# Real-footprint loader
# ============================================================================

_FOOTPRINTS_DIR = Path(__file__).resolve().parents[1] / "data" / "footprints"


def _real_buildings(aoi: AOI, rng: random.Random) -> list[_RawBuilding] | None:
    """Build `_RawBuilding`s from the real footprint parquet for `aoi`, with
    real static terrain (SoilGrids `soil_class`, OSM riparian/shoreline
    distance) attached via the offline fetchers in `fetch_env_context`.

    Returns None when the footprint file is absent so the caller falls back to
    the fully-synthetic generator. Velocity/coherence are *not* set here — they
    come from the phenomenon physics, which read the local-meter fields we fill
    from each real centroid + footprint bbox.
    """
    path = _FOOTPRINTS_DIR / f"{aoi.code}.parquet"
    if not path.exists():
        return None

    from . import fetch_env_context as fe

    tbl = pq.read_table(path)
    cols = set(tbl.column_names)
    geom_wkbs = tbl.column("geom_wkb").to_pylist()
    c_lon = np.asarray(tbl.column("centroid_lon").to_numpy(), dtype=np.float64)
    c_lat = np.asarray(tbl.column("centroid_lat").to_numpy(), dtype=np.float64)
    n_floors_col = tbl.column("n_floors").to_pylist()
    built_col    = tbl.column("built_year").to_pylist()
    n = tbl.num_rows

    # Native footprint id: route by source. OSM parquets carry `osm_id` (int64);
    # Open Buildings parquets carry `open_buildings_id` (string plus-code).
    #
    # Stage E guard: an `open_buildings` AOI MUST have a real `open_buildings_id`
    # column. Without this, a parquet still carrying OSM ids (e.g. an interim
    # re-seed run before the GEE fetch) would fall through to the `osm_id` branch
    # and write OSM ids into the open_buildings_id DB column — silently mislabeling
    # provenance (the Mombasa id debt). Fail loud instead so the mislabel can't
    # land unnoticed; the fix is to run open_buildings_footprints.py for the AOI.
    if aoi.footprint_source == "open_buildings" and "open_buildings_id" not in cols:
        raise ValueError(
            f"AOI '{aoi.code}' declares footprint_source='open_buildings' but its "
            f"parquet ({path}) has no 'open_buildings_id' column (cols={sorted(cols)}). "
            f"Run: python -m scripts.open_buildings_footprints --aoi {aoi.code}"
        )
    if aoi.footprint_source == "open_buildings":
        source_ids = tbl.column("open_buildings_id").to_pylist()
    elif "osm_id" in cols:
        source_ids = tbl.column("osm_id").to_pylist()
    else:
        source_ids = [None] * n

    # Real measured height + confidence, when the source provides them. Guarded
    # so the OSM contract (which the reader historically ignored for height)
    # still works unchanged.
    height_col = tbl.column("height_m").to_pylist() if "height_m" in cols else [None] * n
    conf_col   = tbl.column("confidence").to_pylist() if "confidence" in cols else [None] * n

    # Real static terrain, batched (vectorised fetchers, one read each).
    soil = fe.fetch_soil_class(aoi, c_lon, c_lat)
    riparian = fe.fetch_riparian_dist_m(aoi, c_lon, c_lat)
    shoreline = fe.fetch_shoreline_dist_m(aoi, c_lon, c_lat)

    # Local-meter offset of each centroid from the AOI centre, so the synthetic
    # velocity physics (which key on x_m/y_m hotspots) still produce a spatially
    # coherent field anchored to the real buildings.
    dlon_per_m, dlat_per_m = _meters_to_deg(aoi.center_lat)
    x_m = (c_lon - aoi.center_lon) / dlon_per_m
    y_m = (c_lat - aoi.center_lat) / dlat_per_m

    out: list[_RawBuilding] = []
    for i in range(n):
        poly = shp_wkb.loads(bytes(geom_wkbs[i]))
        minx, miny, maxx, maxy = poly.bounds
        w_m = max(3.0, (maxx - minx) / dlon_per_m)
        h_m = max(3.0, (maxy - miny) / dlat_per_m)

        soil_class = str(soil[i])
        ripa = None if not np.isfinite(riparian[i]) else float(riparian[i])
        shore = None if not np.isfinite(shoreline[i]) else float(shoreline[i])
        # Reclaimed-land flag is keyed on the REAL engineered-ground signal:
        # SoilGrids classifies anthropic fill as WRB Technosols/Anthrosols, which
        # `_wrb_code_to_local` maps to `reclaim_fill` on coastal AOIs. That is the
        # ground truth for "this building sits on reclaimed land" — far more
        # faithful than the old synthetic `shore < 250m` line, which never fired
        # on real Mombasa geometry (real footprints are >440 m from the coast).
        # Inland (non-coastal) AOIs have no reclaim concept → None.
        reclaimed = (
            (soil_class == "reclaim_fill")
            if aoi.phenomenon == "coastal_subsidence"
            else None
        )

        nf = int(n_floors_col[i]) if n_floors_col[i] else rng.choice([1, 2, 3])
        by = int(built_col[i]) if built_col[i] else rng.randint(1990, 2022)
        rh = float(height_col[i]) if height_col[i] is not None and np.isfinite(height_col[i]) else None
        cf = float(conf_col[i]) if conf_col[i] is not None and np.isfinite(conf_col[i]) else None

        out.append(_RawBuilding(
            bid=(100_000 if aoi.code == "huruma" else 200_000) + i,
            x_m=float(x_m[i]), y_m=float(y_m[i]),
            w_m=w_m, h_m=h_m,
            angle=0.0,
            n_floors=nf,
            soil_class=soil_class,
            riparian_dist_m=ripa,
            shoreline_dist_m=shore,
            reclaimed_land=reclaimed,
            built_year=by,
            real_poly=poly,
            source_id=source_ids[i],
            footprint_source=aoi.footprint_source,
            real_height_m=rh,
            confidence=cf,
        ))
    return out


# ============================================================================
# Phenomenon: informal-settlement subsidence (Huruma)
# ============================================================================

_HURUMA_SOIL_BIAS = {
    "black_cotton":     -8.0,
    "alluvial":         -6.0,
    "red_clay":         -2.5,
    "weathered_basalt": -0.5,
}


def _huruma_buildings(aoi: AOI, rng: random.Random) -> list[_RawBuilding]:
    half = aoi.side_m / 2.0
    out: list[_RawBuilding] = []
    for i in range(aoi.n_synthetic_buildings):
        bx, by = rng.uniform(-half, half), rng.uniform(-half, half)
        # synthetic tributary: line y = 0.3x + 200; distance is riparian_dist_m
        ripa = abs(0.3 * bx - by + 200.0) / math.sqrt(0.3**2 + 1.0)
        if ripa < 200:
            soil = rng.choices(["black_cotton", "alluvial", "red_clay"], weights=[5, 4, 1])[0]
        elif ripa < 600:
            soil = rng.choices(["black_cotton", "alluvial", "red_clay", "weathered_basalt"], weights=[3, 2, 3, 1])[0]
        else:
            soil = rng.choices(["black_cotton", "alluvial", "red_clay", "weathered_basalt"], weights=[1, 1, 3, 4])[0]
        n_floors = rng.choices([1, 2, 3, 4, 5, 6, 7], weights=[6, 8, 7, 5, 3, 2, 1])[0]
        out.append(_RawBuilding(
            bid=100_000 + i,
            x_m=bx, y_m=by,
            w_m=rng.uniform(6, 15), h_m=rng.uniform(6, 15),
            angle=rng.uniform(0, math.pi),
            n_floors=n_floors,
            soil_class=soil,
            riparian_dist_m=ripa,
            shoreline_dist_m=None,
            reclaimed_land=None,
            built_year=rng.randint(1995, 2023),
        ))
    return out


def _huruma_velocity(b: _RawBuilding, rng: random.Random) -> float:
    """Long-run vertical velocity (mm/yr, negative = subsiding)."""
    v = _HURUMA_SOIL_BIAS.get(b.soil_class, -1.0)
    v += rng.gauss(0, 1.2)
    v += -3.0 * math.exp(-(b.riparian_dist_m or 1e9) / 250.0)
    # hotspots — three of them, accelerating zones
    hotspots = [(-300, 150, 250, -15.0), (400, -400, 180, -22.0), (-600, -700, 300, -9.0)]
    for hx, hy, r, extra in hotspots:
        d = math.hypot(b.x_m - hx, b.y_m - hy)
        if d < r * 2:
            v += extra * math.exp(-(d / r) ** 2)
    return v


def _huruma_coherence(b: _RawBuilding, rng: random.Random) -> float:
    """Corrugated-iron roofs decorrelate hard; coherence is genuinely low here."""
    base = rng.gauss(0.45, 0.18)
    # Bigger buildings have a slightly steadier signal
    bonus = 0.08 * (b.n_floors - 1) / 6.0
    return max(0.05, min(0.92, base + bonus))


def _huruma_ew_bias(b: _RawBuilding) -> float:
    """Mean east-west drift (mm/yr, +east). Buildings near the synthetic
    tributary lean toward it; the tributary line is y = 0.3x + 200 (the same
    line _huruma_buildings uses for `ripa`). Buildings WEST of the line
    drift east; buildings EAST drift west. Magnitude tapers with distance."""
    ripa = b.riparian_dist_m or 1e9
    # Signed perpendicular: sign tells us which side of the line we're on.
    # Line normal (0.3, -1)/||.||; sign of (0.3·x - y + 200).
    side = math.copysign(1.0, 0.3 * b.x_m - b.y_m + 200.0)
    drift = -side * 12.0 * math.exp(-ripa / 250.0)
    return drift


# ============================================================================
# Phenomenon: coastal subsidence (Mombasa)
# ============================================================================

_MOMBASA_SOIL_BIAS = {
    "coral_rag":        -0.8,
    "reclaim_fill":     -7.5,
    "alluvial":         -3.0,
    "red_clay":         -1.5,
}


def _mombasa_buildings(aoi: AOI, rng: random.Random) -> list[_RawBuilding]:
    """Synthetic shoreline runs north-south at x = -700m (western strip = reclaimed land)."""
    half = aoi.side_m / 2.0
    out: list[_RawBuilding] = []
    for i in range(aoi.n_synthetic_buildings):
        bx, by = rng.uniform(-half, half), rng.uniform(-half, half)
        shoreline_dist = bx - (-700.0)            # +ve = inland, -ve would be in the sea
        reclaimed = shoreline_dist < 250.0        # narrow strip along the shore
        if reclaimed:
            soil = rng.choices(["reclaim_fill", "alluvial"], weights=[7, 1])[0]
        elif shoreline_dist < 800:
            soil = rng.choices(["coral_rag", "alluvial", "red_clay"], weights=[5, 3, 2])[0]
        else:
            soil = rng.choices(["coral_rag", "red_clay"], weights=[7, 3])[0]
        n_floors = rng.choices([1, 2, 3, 4, 5], weights=[4, 8, 6, 3, 1])[0]
        out.append(_RawBuilding(
            bid=200_000 + i,
            x_m=bx, y_m=by,
            w_m=rng.uniform(8, 22), h_m=rng.uniform(8, 22),  # bigger footprints than Huruma
            angle=rng.uniform(0, math.pi),
            n_floors=n_floors,
            soil_class=soil,
            riparian_dist_m=None,
            shoreline_dist_m=max(0.0, shoreline_dist),
            reclaimed_land=reclaimed,
            built_year=rng.randint(1960, 2022),
        ))
    return out


def _mombasa_velocity(b: _RawBuilding, rng: random.Random) -> float:
    v = _MOMBASA_SOIL_BIAS.get(b.soil_class, -1.0)
    v += rng.gauss(0, 0.6)                                            # lower noise — cleaner InSAR
    if b.reclaimed_land:
        v += -4.0 * math.exp(-(b.shoreline_dist_m or 0.0) / 200.0)    # extra creep on the fill
    return v


def _mombasa_coherence(b: _RawBuilding, rng: random.Random) -> float:
    """Concrete and bare-coral surfaces hold coherence well."""
    base = rng.gauss(0.72, 0.10)
    return max(0.30, min(0.96, base))


def _mombasa_ew_bias(b: _RawBuilding) -> float:
    """Seaward (westward, -E) drift, mm/yr.

    Reclaimed fill creeps seaward as the engineered ground consolidates and
    laterally spreads — this is a property of the *fill itself*, so the dominant
    term is driven by `reclaimed_land` (now keyed on real `reclaim_fill` soil),
    not shoreline proximity. We keep a gentle distance modulation (closer to the
    coast = a bit faster) but floor it so a real reclaim cohort — which sits
    hundreds of metres inland — still shows the characteristic seaward signature
    rather than tapering to zero, as the old `exp(-d/200)` form did on real
    geometry. Non-reclaim buildings near the coast get a small seaward nudge."""
    if b.reclaimed_land:
        # Base seaward creep of fill, with a mild ≤1.5× near-shore boost.
        d = b.shoreline_dist_m if b.shoreline_dist_m is not None else 600.0
        return -6.0 * (1.0 + 0.5 * math.exp(-d / 400.0))
    if b.shoreline_dist_m is not None and b.shoreline_dist_m < 800.0:
        return -2.0 * math.exp(-b.shoreline_dist_m / 400.0)
    return 0.0


# ============================================================================
# Dispatcher
# ============================================================================

@dataclass
class _Phenomenon:
    buildings: Callable[[AOI, random.Random], list[_RawBuilding]]
    velocity:  Callable[[_RawBuilding, random.Random], float]
    coherence: Callable[[_RawBuilding, random.Random], float]
    ew_bias:   Callable[[_RawBuilding], float]     # mean E-W drift mm/yr
    tidal_amplitude_mm: float                       # low-frequency periodic component in time series
    insar_height_noise_floor_m: float               # min σ for InSAR height even on a big footprint


_PHENOMENA: dict[str, _Phenomenon] = {
    "informal_settlement_subsidence": _Phenomenon(
        buildings=_huruma_buildings,
        velocity=_huruma_velocity,
        coherence=_huruma_coherence,
        ew_bias=_huruma_ew_bias,
        tidal_amplitude_mm=0.0,
        # Dense informal settlement: heavy layover, decorrelation, sub-pixel
        # rooflines. Even big footprints don't get much better than ±2 m.
        insar_height_noise_floor_m=2.0,
    ),
    "coastal_subsidence": _Phenomenon(
        buildings=_mombasa_buildings,
        velocity=_mombasa_velocity,
        coherence=_mombasa_coherence,
        ew_bias=_mombasa_ew_bias,
        tidal_amplitude_mm=3.0,
        # Concrete + bare-coral surfaces hold coherence; cleaner phase fringes.
        insar_height_noise_floor_m=1.0,
    ),
}


# Tier-1/2/3 helpers — `_classify`, `composite_risk`, `_stl_decompose`,
# `_velocity_sigma_from_coherence`, `_cohort_percentiles`, `_acceleration_mm_yr2`,
# `_insar_height`, `_fused_height`, and the CLASS_*/FAILURE_* constants — all
# moved to `postprocess.py` (imported above) so the real-InSAR path can reuse
# them. The functions are byte-identical; only call sites moved.


# ============================================================================
# Top-level generator
# ============================================================================

def generate_aoi_dataset(
    *,
    aoi: AOI,
    dates: list[date],
    rng: random.Random,
    np_rng: np.random.Generator,
) -> tuple[pa.Table, pa.Table, pa.Table]:
    pheno = _PHENOMENA[aoi.phenomenon]
    # Prefer real footprints + real static terrain (SoilGrids soil, OSM
    # riparian/shoreline). Falls back to the fully-synthetic generator only when
    # no footprint parquet exists for this AOI. Velocity/coherence stay
    # synthetic either way until the real MintPy join runs (join_insar.py).
    raw = _real_buildings(aoi, rng)
    if raw is None:
        raw = pheno.buildings(aoi, rng)
    n_months = len(dates)

    # ---- buildings table (initial pass — building_id, geometry, static attrs;
    # classification + acceleration come back after we synthesize the
    # time-series below, since they depend on end-of-series state) -----------
    rows_b: list[dict] = []
    velocities: dict[int, float] = {}
    coherences: dict[int, float] = {}
    fused_heights: dict[int, float] = {}
    polys: dict[int, Polygon] = {}

    for b in raw:
        poly = b.real_poly if b.real_poly is not None else _make_polygon(b, aoi)
        polys[b.bid] = poly
        velocities[b.bid] = pheno.velocity(b, rng)
        coherences[b.bid] = pheno.coherence(b, rng)

        # Prefer the real measured height from the footprint source (Google Open
        # Buildings). Only fall back to the floor-count estimate when the source
        # has no height (synthetic AOIs, or OSM rows without a height tag).
        if b.real_height_m is not None and b.real_height_m > 0:
            footprint_h = b.real_height_m
        else:
            footprint_h = b.n_floors * rng.uniform(2.8, 3.2)
        area_m2 = b.w_m * b.h_m
        insar_h, insar_sigma = _insar_height(
            footprint_h, area_m2, pheno.insar_height_noise_floor_m, rng,
        )
        fused_h = _fused_height(footprint_h, insar_h, insar_sigma)
        fused_heights[b.bid] = fused_h

        rows_b.append({
            "building_id":          b.bid,
            "aoi_code":             aoi.code,
            "footprint_source":     b.footprint_source,
            # osm_id is int64; open_buildings_id is string. Route the native
            # footprint id (and cast) by source so each column gets its type.
            "osm_id":               int(b.source_id) if (b.footprint_source == "osm" and b.source_id is not None) else None,
            "open_buildings_id":    str(b.source_id) if (b.footprint_source == "open_buildings" and b.source_id is not None) else None,
            "geom_wkb":             shp_wkb.dumps(poly, hex=False),
            "centroid_lon":         poly.centroid.x,
            "centroid_lat":         poly.centroid.y,
            "height_m":             footprint_h,
            "insar_height_m":       insar_h,
            "insar_height_sigma_m": insar_sigma,
            "fused_height_m":       fused_h,
            # Synthetic footprints always carry a height (real or floor-derived),
            # so height is imputed only when no real source height existed.
            "height_imputed":       bool(b.real_height_m is None or b.real_height_m <= 0),
            "n_floors":             b.n_floors,
            # Each synthetic building is modelled independently → one footprint
            # per InSAR cell. (The shared-cell caveat only applies to real joins.)
            "insar_pixel_share":    1,
            "soil_class":           b.soil_class,
            "riparian_dist_m":      b.riparian_dist_m,
            "shoreline_dist_m":     b.shoreline_dist_m,
            "reclaimed_land":       b.reclaimed_land,
            "built_year":           b.built_year,
            # Filled in after time-series synthesis (see below)
            "classification":         CLASS_INDETERMINATE,
            "velocity_accel_mm_yr2":  0.0,
            "trend_slope_mm_yr":      0.0,
            "seasonal_amplitude_mm":  0.0,
            "trend_r2":               0.0,
            "failure_mode":           FAILURE_ELASTIC,
            # Tier 3 (honesty layer)
            "velocity_sigma_mm_yr":   0.0,
            "velocity_ew_sigma_mm_yr": 0.0,
            "cohort_composite_pct":   50,
            "cohort_shear_pct":       50,
            "cohort_size":            1,
            # ARCHITECTURE_THREE C1/C4 — block membership + block cohort pct.
            # Filled in after time-series synthesis (see cohort block below).
            "block_id":               0,
            "cohort_block_pct":       50,
            # ARCHITECTURE_THREE B1/B3 — synthetic diagnostics (real values
            # come from MintPy via scripts/join_insar.py). Closure RMS scales
            # inversely with coherence (low γ → noisy phase → high closure
            # residual); DEM error is a small Gaussian with occasional outliers.
            "closure_rms_rad":        0.0,
            "dem_err_m":              0.0,
            "dem_err_flag":           False,
        })

    # ---- time series -------------------------------------------------------
    # Vectorized for speed: n_buildings × n_months arrays.
    n = len(raw)
    bids = np.array([b.bid for b in raw], dtype=np.int64)
    v_long = np.array([velocities[b.bid] for b in raw], dtype=np.float64)
    coh_base = np.array([coherences[b.bid] for b in raw], dtype=np.float64)

    # Acceleration: ~10% of buildings ramp up in the back half
    accel = np_rng.random(n) < 0.10
    months_idx = np.arange(n_months)
    half = n_months // 2
    mult = np.where(
        accel[:, None] & (months_idx[None, :] > half),
        1.0 + 0.6 * (months_idx[None, :] - half) / n_months,
        1.0,
    )
    monthly_increment = (v_long[:, None] / 12.0) * np.clip(mult, 0.0, None)
    noise = np_rng.normal(0, 0.6, size=(n, n_months))
    # Tidal-loading-ish low-frequency component on top
    if pheno.tidal_amplitude_mm > 0:
        period = 6.0  # months
        phase = np_rng.uniform(0, 2 * math.pi, size=n)
        tidal = pheno.tidal_amplitude_mm * np.sin(2 * math.pi * months_idx[None, :] / period + phase[:, None])
        tidal_increment = np.diff(tidal, axis=1, prepend=tidal[:, :1] * 0.0)
    else:
        tidal_increment = 0.0
    cumulative = np.cumsum(monthly_increment + noise + tidal_increment, axis=1)

    # Trailing-12mo linear velocity (vectorized polyfit-equivalent)
    velocities_per_month = _trailing_velocity(cumulative, window=12)

    # Per-month coherence drifts a little around the base
    coh = np.clip(coh_base[:, None] + np_rng.normal(0, 0.04, size=(n, n_months)), 0.05, 0.97)

    # Horizontal east-west velocity per (building, month).
    # The long-run bias is per-building (set by phenomenon); month-to-month
    # noise is modest and proportional to (1 - coherence) so low-coherence
    # buildings have a noisier drift signal — matches the InSAR physics where
    # vector-decomposition error scales with phase noise.
    ew_bias = np.array([pheno.ew_bias(b) for b in raw], dtype=np.float64)
    ew_noise_scale = 0.8 + 2.0 * (1.0 - coh_base[:, None])     # shape (n,1)
    ew_velocity = ew_bias[:, None] + np_rng.normal(
        loc=0.0, scale=ew_noise_scale,
        size=(n, n_months),
    )

    # STL decomposition on the cumulative displacement series, per building.
    # We use the period closest to 12 months that fits within the series length;
    # STL needs ≥ 2 full periods, so a 24-month series gives period=12 exactly.
    stl_period = min(12, max(2, n_months // 2))
    (trend_disp, trend_slope, seasonal_amp,
     trend_r2, failure_mode) = _stl_decompose(cumulative, period=stl_period)

    date_array = np.array([d.toordinal() for d in dates])
    bids_repeat = np.repeat(bids, n_months)
    dates_tile  = np.tile(date_array, n)

    ts_table = pa.table({
        "building_id":                    pa.array(bids_repeat, type=pa.int64()),
        "aoi_code":                       pa.array([aoi.code] * (n * n_months), type=pa.string()),
        "observation_date":               pa.array([date.fromordinal(int(d)) for d in dates_tile], type=pa.date32()),
        "displacement_mm":                pa.array(cumulative.reshape(-1), type=pa.float64()),
        "trend_displacement_mm":          pa.array(trend_disp.reshape(-1), type=pa.float64()),
        "velocity_mm_yr":                 pa.array(velocities_per_month.reshape(-1), type=pa.float64()),
        "velocity_horizontal_ew_mm_yr":   pa.array(ew_velocity.reshape(-1), type=pa.float64()),
        "coherence":                      pa.array(coh.reshape(-1), type=pa.float64()),
    })

    # ---- end-of-series classification + acceleration -----------------------
    # These are per-building scalars derived from the matrices we just built.
    v_end       = velocities_per_month[:, -1]
    v_ew_end    = ew_velocity[:, -1]
    coh_end     = coh[:, -1]
    accel_arr   = _acceleration_mm_yr2(velocities_per_month, lookback=6)
    # Tier 3 #6: per-building velocity σ from end-of-series coherence. Computed
    # before classification so the court-defensibility gate can read it.
    v_sigma     = _velocity_sigma_from_coherence(coh_end)
    v_ew_sigma  = _velocity_sigma_from_coherence(coh_end)   # same model; ew-noise scales with same γ
    # Court-defensibility gate thresholds: σ_max from this AOI's own σ p75, r2_min
    # an absolute floor (see postprocess.defensibility_thresholds).
    r2_min, sigma_max = defensibility_thresholds(v_sigma)
    classifications = np.array(
        [_classify(float(v_end[i]), float(v_ew_end[i]), float(coh_end[i]),
                   float(accel_arr[i]), float(trend_r2[i]), float(v_sigma[i]),
                   r2_min, sigma_max)
         for i in range(n)],
        dtype=np.uint8,
    )

    # ---- env index (quarterly) ---------------------------------------------
    # Composite risk is computed once from end-of-series state (v_end, v_ew_end,
    # classification) and broadcast across the quarterly env rows. Pre-Tier-1
    # the composite recomputed per quarter with quarter-independent noise;
    # we keep that quarter-to-quarter jitter so the time-shifted UI isn't flat.
    quarters = [date(2024, m, 1) for m in (6, 9, 12)] + \
               [date(2025, m, 1) for m in (3, 6, 9, 12)] + \
               [date(2026, 3, 1), date(2026, 5, 1)]
    rows_e: list[dict] = []
    # External engineer/authority structural flags (the second sensor). Even the
    # SYNTHETIC build honours real flags recorded in Weespas + exported to disk, so
    # the integration loop is live before any real InSAR data exists. Absent export ⇒
    # all STRUCT_NONE ⇒ scores identical to the no-flag synthetic baseline.
    sflag_state, sflag_age, sflag_observed_at, sflag_source = fetch_structural_flags(
        aoi.code, bids
    )
    # We also need the latest composite per building to compute cohort
    # percentiles (Tier 3 #7) — capture it as we iterate.
    latest_composite = np.zeros(n, dtype=np.float32)
    danger_arr = np.zeros(n, dtype=np.uint8)
    for i, b in enumerate(raw):
        v_b      = float(v_end[i])
        vew_b    = float(v_ew_end[i])
        accel_b  = float(accel_arr[i])
        slope_b  = float(trend_slope[i])
        fmode_b  = int(failure_mode[i])
        cls_b    = int(classifications[i])
        fh_b     = float(fused_heights[b.bid])
        sfs_b    = int(sflag_state[i])
        sfa_b    = float(sflag_age[i])
        # Collapse score + absolute danger are deterministic per building (no
        # per-quarter signal feeds them), so compute once and broadcast.
        comp = composite_risk(
            soil_class=b.soil_class,
            riparian_dist_m=b.riparian_dist_m,
            shoreline_dist_m=b.shoreline_dist_m,
            vel=v_b,
            v_ew=vew_b,
            accel=accel_b,
            trend_slope=slope_b,
            failure_mode=fmode_b,
            classification=cls_b,
            fused_h_m=fh_b,
            structural_flag_state=sfs_b,
            flag_age_days=sfa_b,
        )
        danger_arr[i] = danger_level(
            vel=v_b, v_ew=vew_b, accel=accel_b,
            failure_mode=fmode_b, classification=cls_b,
            structural_flag_state=sfs_b,
        )
        last_q_composite = comp
        for q in quarters:
            rows_e.append({
                "building_id":      b.bid,
                "aoi_code":         aoi.code,
                "period_start":     q,
                "groundwater_anom": rng.gauss(0, 1),
                "rainfall_anom_mm": rng.gauss(0, 20),
                "ndvi_proxy":       rng.uniform(0.1, 0.5),
                "composite_risk":   comp,
            })
        latest_composite[i] = last_q_composite
    env_tbl = pa.Table.from_pylist(rows_e, schema=_ENV_SCHEMA)

    # Tier 3 #7: cohort percentiles — height-band × soil-class peer groups.
    fused_h_arr = np.array([fused_heights[b.bid] for b in raw], dtype=np.float32)
    soil_list   = [b.soil_class for b in raw]
    composite_pct, shear_pct, cohort_size = _cohort_percentiles(
        latest_composite, np.abs(v_ew_end.astype(np.float32)),
        fused_h_arr, soil_list,
    )

    # ARCHITECTURE_THREE C1/C4 — block membership + block-relative cohort.
    from scripts.aois import bbox as _aoi_bbox
    c_lon_arr = np.array([row["centroid_lon"] for row in rows_b], dtype=np.float64)
    c_lat_arr = np.array([row["centroid_lat"] for row in rows_b], dtype=np.float64)
    block_id_arr, _block_meta = assign_blocks(c_lon_arr, c_lat_arr, _aoi_bbox(aoi))
    n_blocks = _block_meta["nx"] * _block_meta["ny"]
    cohort_block_pct = _rank_within_groups(
        latest_composite, block_id_arr.astype(np.int64), n_blocks
    )

    # ARCHITECTURE_THREE B1/B3 — plausible-synthetic diagnostics, vectorised.
    # closure_rms scales inversely with coherence (low γ → noisy phase →
    # higher closure residual). Real range observed in tropical S1 stacks
    # is ~0.05 to ~0.8 rad; we follow ~ 0.4*(1-γ) + small noise.
    closure_rms_arr = np.clip(
        0.4 * (1.0 - coh_base.astype(np.float32)) + np_rng.normal(0, 0.05, size=n).astype(np.float32),
        0.0, np.pi,
    )
    # DEM error: small Gaussian + ~5% outliers > 15 m (the threshold the UI
    # flags). Matches MintPy's empirical demErr distribution on 30 m SRTM.
    dem_err_arr = np_rng.normal(0, 4.0, size=n).astype(np.float32)
    outlier_mask = np_rng.random(n) < 0.05
    dem_err_arr[outlier_mask] += np_rng.choice([-1.0, 1.0], size=int(outlier_mask.sum())).astype(np.float32) * 20.0
    dem_err_flag_arr = np.abs(dem_err_arr) > 15.0

    # Back-fill into the buildings rows (preserves the rows_b order = raw order).
    for i, row in enumerate(rows_b):
        row["classification"]         = int(classifications[i])
        row["velocity_accel_mm_yr2"]  = float(accel_arr[i])
        row["trend_slope_mm_yr"]      = float(trend_slope[i])
        row["seasonal_amplitude_mm"]  = float(seasonal_amp[i])
        row["trend_r2"]               = float(trend_r2[i])
        row["failure_mode"]           = int(failure_mode[i])
        row["danger_level"]           = int(danger_arr[i])
        row["velocity_sigma_mm_yr"]   = float(v_sigma[i])
        row["velocity_ew_sigma_mm_yr"] = float(v_ew_sigma[i])
        row["cohort_composite_pct"]   = int(composite_pct[i])
        row["cohort_shear_pct"]       = int(shear_pct[i])
        row["cohort_size"]            = int(cohort_size[i])
        row["block_id"]               = int(block_id_arr[i])
        row["cohort_block_pct"]       = int(cohort_block_pct[i])
        row["closure_rms_rad"]        = float(closure_rms_arr[i])
        row["dem_err_m"]              = float(dem_err_arr[i])
        row["dem_err_flag"]           = bool(dem_err_flag_arr[i])
        # External structural flag (real engineer/authority judgements exported from
        # Weespas). All-NONE when no export exists ⇒ unflagged sentinel.
        row["structural_flag_state"]       = int(sflag_state[i])
        row["structural_flag_observed_at"] = sflag_observed_at[i]
        row["structural_flag_source"]      = sflag_source[i]

    buildings_tbl = pa.Table.from_pylist(rows_b, schema=_BUILDINGS_SCHEMA)

    return buildings_tbl, ts_table, env_tbl


# Arrow schemas and `_trailing_velocity` are in postprocess.py — see top-of-file
# imports. `_BUILDINGS_SCHEMA` / `_ENV_SCHEMA` are aliased there.
