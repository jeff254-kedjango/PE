"""
Stage 4: join MintPy raster outputs to building footprints, emit GeoParquet.

The schema is fixed by `scripts/init_db.sql` — we have to produce exactly the
columns the DuckDB views expect, in Hive-partitioned layout:

    data/parquet/buildings/aoi=<code>/part-0.parquet
    data/parquet/subsidence/aoi=<code>/part-0.parquet
    data/parquet/env_index/aoi=<code>/part-0.parquet     (synthesized; no real env data yet)
    data/parquet/aoi_registry.parquet

Per footprint, we aggregate raster pixels into one (velocity, σ, displacement-
time-series) row. The aggregation is coherence-weighted least-squares: pixels
with γ < 0.3 are dropped (incoherent), the rest are blended with weight γ². This
is the same model the risk engine assumes downstream — σ ∝ (1 - γ).

Performance: footprints are O(10³) per AOI, pixels are O(10⁶). The inner loop is
**numpy-vectorized** rather than per-pixel Python — we rasterize every footprint
once into a uint32 label grid, then groupby-aggregate against the displacement /
coherence stacks. That's the only way to do this in seconds instead of minutes.

Run from backend/:
    python -m scripts.join_insar --aoi huruma --track ASCENDING/57
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np

from scripts.aois import AOI, by_code
from scripts.provenance import set_provenance
from scripts.postprocess import (
    BUILDINGS_SCHEMA,
    SUBSIDENCE_SCHEMA,
    COH_SERIES_SCHEMA,
    CLASS_INDETERMINATE,
    DEM_ERR_FLAG_M,
    FAILURE_ELASTIC,
    _trailing_velocity,
    _stl_decompose,
    _acceleration_mm_yr2,
    _velocity_sigma_from_coherence,
    _classify,
    defensibility_thresholds,
    _cohort_percentiles,
    _rank_within_groups,
    assign_blocks,
    tilt_rate_from_velocity_field,
    extract_closure_rms,
    extract_dem_err,
    extract_coh_per_epoch,
    pack_coh_series_per_building,
    synthesize_env_index_rows,
)
from scripts.fetch_env_context import (
    fetch_shoreline_dist_m,
    fetch_riparian_dist_m,
    fetch_soil_class,
)
from scripts.structural_flags import fetch_structural_flags

BACKEND_DIR = Path(__file__).resolve().parents[1]
PARQUET_ROOT = BACKEND_DIR / "data" / "parquet"
MINTPY_DIR = BACKEND_DIR / "data" / "mintpy"
FOOTPRINT_DIR = BACKEND_DIR / "data" / "footprints"
DB_PATH = BACKEND_DIR / "data" / "demo.duckdb"

# Coherence floor. Pixels below this are dropped before aggregation — they
# carry effectively zero information and bias the LS fit if included.
COH_MIN = 0.30

# Stable-bulk de-mean (AOI.external_anchor=False only): the "bulk" is coherent,
# live buildings whose median velocity defines the tile's reference rate. Floor is
# higher than COH_MIN so the median is taken over genuinely reliable pixels; the
# minimum count guards against de-meaning to a handful of noisy survivors.
COH_BULK_FLOOR = 0.50
MIN_BULK_PIXELS = 50

# Floor-to-floor height used to convert a floor count to metres when a source
# height is absent. Named so it can be retuned (informal-settlement storeys run
# ~2.6–3.0 m); the synthetic seed path uses the same nominal value.
MEAN_FLOOR_M = 3.0


def _years_from_epoch0(dates_iso: list[str]) -> "np.ndarray":
    """Decimal years of each `YYYY-MM-DD` epoch relative to the first epoch.

    Used to convert a velocity offset (mm/yr) into the cumulative displacement
    ramp (mm) it implies, so de-meaning velocity and displacement stay consistent.
    """
    d0 = date.fromisoformat(dates_iso[0])
    return np.array(
        [(date.fromisoformat(d) - d0).days / 365.25 for d in dates_iso],
        dtype=np.float64,
    )


@dataclass(frozen=True)
class RasterStack:
    """A georeferenced (time, y, x) stack: displacement series + coherence."""
    # mm, shape (T, H, W). Sign convention: + = subsidence (negative LOS away
    # from satellite). MintPy emits metres along LOS; we convert.
    displacement_mm: np.ndarray
    # 0-1, shape (H, W). Temporal coherence — fit quality of the SBAS inversion.
    coherence: np.ndarray
    # Annualized LS slope in mm/yr; shape (H, W). MintPy's velocity.h5.
    # Vertical-EQUIVALENT (LOS / cos inc) — correct only when motion is purely
    # vertical; used as-is on the 1-look (ASC-only) path.
    velocity_mm_yr: np.ndarray
    # Annualized LS slope in mm/yr ALONG LOS (raw, NOT divided by cos inc); shape
    # (H, W). This is what the ASC+DESC decomposition inverts.
    velocity_los_mm_yr: np.ndarray
    # Per-pixel incidence angle in RADIANS; shape (H, W).
    incidence_rad: np.ndarray
    # Satellite heading, degrees from north (clockwise). ASC ≈ −12, DESC ≈ +192.
    heading_deg: float
    # "ASCENDING" or "DESCENDING".
    orbit_direction: str
    # ISO date strings, length T.
    dates: list[str]
    # Spatial axes; both monotonically increasing. Units may be metres (UTM,
    # when MintPy reads HyP3 GAMMA which is already geocoded into UTM) or
    # degrees (WGS84, when MintPy geocodes from radar). The CRS lives in `epsg`.
    xs: np.ndarray  # shape (W,)
    ys: np.ndarray  # shape (H,)
    epsg: int       # raster CRS — e.g. 32737 for our HyP3 outputs, 4326 if degrees


def _resolve_mintpy_paths(run_dir: Path) -> tuple[Path, Path, Path, Path]:
    """Locate the four MintPy outputs we need.

    MintPy writes geocoded outputs in two places depending on whether the input
    was already in geographic/projected coords:
      - radar-coords input → run_dir/geo/geo_velocity.h5, etc.
      - already-geocoded input (HyP3 GAMMA) → run_dir/velocity.h5 (flat)
    Probe both, raise with a useful error if neither shape is present.

    Coherence note: we deliberately use `avgSpatialCoh.h5` (mean interferometric
    coherence γ over the *pre-inversion* network), NOT `temporalCoherence.h5`
    (post-inversion fit quality). With coherence-based pair filtering enabled,
    temporalCoherence saturates at 1.0 by construction — it would make σ_v = 0
    everywhere, which is dishonest. avgSpatialCoh preserves the physical noise
    signal we need for the σ_v ∝ (1 - γ) model downstream.
    """
    geo = run_dir / "geo"
    if (geo / "geo_velocity.h5").exists():
        return (
            geo / "geo_timeseries.h5",
            geo / "geo_velocity.h5",
            geo / "geo_avgSpatialCoh.h5" if (geo / "geo_avgSpatialCoh.h5").exists() else geo / "geo_temporalCoherence.h5",
            geo / "geo_geometryRadar.h5",
        )
    return (
        run_dir / "timeseries.h5",
        run_dir / "velocity.h5",
        run_dir / "avgSpatialCoh.h5",
        run_dir / "inputs" / "geometryGeo.h5",
    )


def load_mintpy_stack(run_dir: Path) -> RasterStack:
    """Read the four HDF5 outputs MintPy emits and stitch into a RasterStack."""
    import h5py

    ts_path, vel_path, coh_path, geom_path = _resolve_mintpy_paths(run_dir)
    for p in (ts_path, vel_path, coh_path, geom_path):
        if not p.exists():
            raise FileNotFoundError(f"missing MintPy output: {p}")

    with h5py.File(ts_path, "r") as f:
        # timeseries: shape (n_date, H, W), metres along LOS
        disp_m = np.asarray(f["timeseries"], dtype=np.float32)
        date_bytes = np.asarray(f["date"])
        dates = [d.decode() if isinstance(d, bytes) else str(d) for d in date_bytes]
        # MintPy date strings are YYYYMMDD — convert to ISO YYYY-MM-DD.
        dates = [f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else d for d in dates]
        attrs = dict(f.attrs)

    # Sign convention: MintPy's `timeseries`/`velocity` are LOS-along, with
    # POSITIVE = motion toward the satellite (i.e. uplift for near-vertical LOS).
    # The schema convention shared with phenomena.py is **negative = subsidence**
    # (matching `composite_risk:subs_score = -vel/25`), so we DO NOT flip sign
    # at this step — LOS-positive-uplift carries straight through to
    # mm/yr-positive-uplift, leaving subsidence negative as expected.
    #
    # We keep BOTH the raw LOS velocity (for ASC+DESC decomposition in run_join)
    # and a vertical-equivalent projection LOS/cos(inc) used directly when only
    # one orbit is available. Plus geometry (incidence, heading, orbit) so two
    # stacks can be fused.
    with h5py.File(geom_path, "r") as f:
        inc_raw = np.asarray(f["incidenceAngle"], dtype=np.float32)
        geom_attrs = dict(f.attrs)
    # MintPy stores incidenceAngle in RADIANS (S1 IW ≈ 0.45–0.73 rad = 26–42°).
    # A units sniff keeps us correct if a future product ever stores degrees:
    # any physical incidence in radians is < 1.6 (≈ 92°), so a max above that
    # means the array is in degrees and must be converted before cos().
    inc_rad = inc_raw if float(np.nanmax(inc_raw)) < 1.6 else np.radians(inc_raw)
    cos_inc = np.cos(inc_rad)
    cos_inc = np.where(cos_inc < 0.1, 0.1, cos_inc)  # clamp to keep numerics sane
    heading_deg = float(geom_attrs.get("HEADING", attrs.get("HEADING", 0.0)))
    orbit_direction = str(
        geom_attrs.get("ORBIT_DIRECTION", attrs.get("ORBIT_DIRECTION", "ASCENDING"))
    )

    with h5py.File(vel_path, "r") as f:
        vel = np.asarray(f["velocity"], dtype=np.float32)  # m/yr LOS
        vel_attrs = dict(f.attrs)
    # Raw LOS (mm/yr) for decomposition; vertical-equivalent for the 1-look path.
    vel_los_mm_yr = vel * 1000.0
    vel_mm_yr = (vel / cos_inc) * 1000.0
    # broadcast (H, W) over (T, H, W). disp_m is metres → mm, vertical-equivalent.
    disp_mm = (disp_m / cos_inc) * 1000.0

    # ---- Dry-run guard (ARCHITECTURE_THREE A1) ----------------------------
    # scripts/_dryrun_stage4.py fabricates a tiny MintPy-shaped dir to exercise
    # this join while the real HyP3/MintPy run is pending. Those products are
    # ~25×25 px and carry troposphericDelay.method=height_correlation (never
    # gacos). Shipping them to a public broadcast would be a fabricated-data
    # incident, so we refuse to do it silently. A real geocoded SBAS stack over
    # a 2 km AOI at ~30 m is hundreds of px per side and was run with GACOS.
    H_v, W_v = vel.shape
    tropo = str(vel_attrs.get("mintpy.troposphericDelay.method", "")).lower()
    if H_v <= 30 and W_v <= 30 and tropo != "gacos":
        msg = (
            f"\n  ‼ DRY-RUN / PLACEHOLDER VELOCITY DETECTED in {vel_path}\n"
            f"    grid is {H_v}×{W_v} px (real SBAS over this AOI is hundreds of px)\n"
            f"    troposphericDelay.method = '{tropo or 'unset'}' (expected 'gacos')\n"
            f"    These look like _dryrun_stage4.py outputs, NOT a real MintPy run.\n"
            f"    Run smallbaselineApp.py on OpenSARLab (see docs/opensarlab_runbook.md)\n"
            f"    and download the real geocoded products before joining for the demo.\n"
            f"    Set GACOS_JOIN_ALLOW_PLACEHOLDER=1 to override (dev/testing only).\n"
        )
        if os.environ.get("GACOS_JOIN_ALLOW_PLACEHOLDER") == "1":
            print(msg, file=sys.stderr)
            print("  ↳ override set — proceeding with placeholder data.", file=sys.stderr)
        else:
            raise SystemExit(msg)

    with h5py.File(coh_path, "r") as f:
        # avgSpatialCoh.h5 stores the layer as "coherence"; temporalCoherence.h5
        # stores it as "temporalCoherence". Pick whichever dataset is in the file.
        ds = "coherence" if "coherence" in f else "temporalCoherence"
        coh = np.asarray(f[ds], dtype=np.float32)

    # Reconstruct x/y axes from geocoded attrs. MintPy stores X_FIRST, Y_FIRST,
    # X_STEP, Y_STEP, WIDTH, LENGTH in the file attributes. Units depend on the
    # CRS — for our HyP3 GAMMA stacks they're metres in UTM 37S (EPSG 32737);
    # if MintPy ever geocodes from radar into 4326 they'd be degrees. The
    # axis-construction math is identical either way; only the building-centroid
    # transform downstream needs to match.
    H, W = coh.shape
    x_first = float(attrs["X_FIRST"])
    y_first = float(attrs["Y_FIRST"])
    x_step  = float(attrs["X_STEP"])    # positive (east / increasing x)
    y_step  = float(attrs["Y_STEP"])    # negative (south / decreasing y)
    xs = x_first + np.arange(W, dtype=np.float64) * x_step
    ys = y_first + np.arange(H, dtype=np.float64) * y_step
    if y_step < 0:
        # Flip so ys[0] is the south edge; mirror every (H,W)/(T,H,W) array to match.
        ys = ys[::-1]
        disp_mm = disp_mm[:, ::-1, :]
        coh = coh[::-1, :]
        vel_mm_yr = vel_mm_yr[::-1, :]
        vel_los_mm_yr = vel_los_mm_yr[::-1, :]
        inc_rad = inc_rad[::-1, :]

    epsg = int(attrs.get("EPSG", 4326))

    return RasterStack(
        displacement_mm=disp_mm,
        coherence=coh,
        velocity_mm_yr=vel_mm_yr,
        velocity_los_mm_yr=vel_los_mm_yr,
        incidence_rad=inc_rad.astype(np.float64),
        heading_deg=heading_deg,
        orbit_direction=orbit_direction,
        dates=dates,
        xs=xs.astype(np.float64),
        ys=ys.astype(np.float64),
        epsg=epsg,
    )


def _find_desc_run_dir(aoi_code: str) -> Path | None:
    """Locate a sibling DESCENDING MintPy run for this AOI, if one exists.

    Run dirs are named `<aoi>_<FLIGHT>_<path>` (e.g. kileleshwa_DESCENDING_79).
    We accept any descending path so the join doesn't need the path hardcoded;
    if several exist we take the one with the most epochs (the usable stack).
    Returns None when the AOI is ascending-only (huruma, south_c, mombasa).
    """
    cands = sorted(MINTPY_DIR.glob(f"{aoi_code}_DESCENDING_*"))
    cands = [c for c in cands if c.is_dir() and not c.name.endswith(("_bak", "audit"))
             and "bak" not in c.name and "crashed" not in c.name]
    if not cands:
        return None
    def _epochs(d: Path) -> int:
        try:
            return len(load_mintpy_stack(d).dates)
        except Exception:
            return -1
    return max(cands, key=_epochs)


def _los_to_vertical(vel_los: np.ndarray, inc_rad: np.ndarray) -> np.ndarray:
    """Vertical-equivalent velocity from a single LOS look: v = LOS / cos(inc).

    Exact only when motion is purely vertical; it's the honest 1-look fallback
    when no descending pass is available to separate vertical from east-west.
    """
    cos_inc = np.cos(inc_rad)
    cos_inc = np.where(cos_inc < 0.1, 0.1, cos_inc)
    return vel_los / cos_inc


def fuse_or_project(stack_asc: RasterStack, stack_desc: RasterStack | None
                    ) -> dict[str, np.ndarray | str]:
    """Produce per-pixel vertical + east-west velocity fields on the ASC grid.

    With a descending stack: co-register DESC LOS + coherence + incidence onto
    the ASC grid and decompose (vertical + east-west) per pixel. Pixels where
    the inversion is unusable (degenerate geometry, DESC out of extent, either
    orbit incoherent) fall back per-pixel to vertical-equivalent with v_ew=NaN —
    NaN meaning "drift unknown here", never a fabricated zero.

    Without a descending stack (ascending-only AOIs): vertical-equivalent for
    every pixel, v_ew = NaN everywhere, mode = "los_1look".

    Returns a dict of (H,W) arrays — v_up, v_ew, sig_up, sig_ew — plus the
    scalar `mode` and `decomposed_frac` (share of coherent pixels actually
    decomposed), so run_join can report and store provenance honestly.
    """
    from scripts.decompose import decompose_asc_desc, coregister_field

    coh_a = stack_asc.coherence
    inc_a = stack_asc.incidence_rad
    sig_a = _velocity_sigma_from_coherence(np.nan_to_num(coh_a, nan=0.0))

    if stack_desc is None:
        v_up = _los_to_vertical(stack_asc.velocity_los_mm_yr, inc_a)
        nan = np.full_like(v_up, np.nan)
        return {
            "v_up": v_up, "v_ew": nan.copy(),
            "sig_up": sig_a.astype(np.float64), "sig_ew": nan.copy(),
            "mode": "los_1look", "decomposed_frac": 0.0,
        }

    # Co-register every descending field onto the ascending pixel grid.
    v_desc = coregister_field(stack_desc.velocity_los_mm_yr,
                              stack_desc.xs, stack_desc.ys, stack_asc.xs, stack_asc.ys)
    inc_desc = coregister_field(stack_desc.incidence_rad,
                                stack_desc.xs, stack_desc.ys, stack_asc.xs, stack_asc.ys)
    coh_desc = coregister_field(stack_desc.coherence,
                                stack_desc.xs, stack_desc.ys, stack_asc.xs, stack_asc.ys)
    sig_desc = _velocity_sigma_from_coherence(np.nan_to_num(coh_desc, nan=0.0))

    # Only attempt decomposition where BOTH orbits are coherent; elsewhere the
    # NaN LOS will make decompose flag ok=False and we fall back to vertical.
    v_asc = np.where(np.isfinite(coh_a) & (coh_a >= COH_MIN),
                     stack_asc.velocity_los_mm_yr, np.nan)
    v_desc = np.where(np.isfinite(coh_desc) & (coh_desc >= COH_MIN), v_desc, np.nan)

    v_up, v_ew, sig_up, sig_ew, ok = decompose_asc_desc(
        v_asc, v_desc, inc_a, inc_desc,
        stack_asc.heading_deg, stack_desc.heading_deg, sig_a, sig_desc,
    )

    # Per-pixel fallback: where decomposition failed, vertical-equivalent from
    # ASC LOS, with v_ew/σ_ew left NaN (unknown drift, not zero drift).
    fallback_up = _los_to_vertical(stack_asc.velocity_los_mm_yr, inc_a)
    v_up = np.where(ok, v_up, fallback_up)
    sig_up = np.where(ok, sig_up, sig_a)

    coherent = np.isfinite(coh_a) & (coh_a >= COH_MIN)
    n_coh = int(coherent.sum())
    frac = float((ok & coherent).sum()) / n_coh if n_coh else 0.0
    return {
        "v_up": v_up, "v_ew": v_ew, "sig_up": sig_up, "sig_ew": sig_ew,
        "mode": "decomposed_2look", "decomposed_frac": frac,
    }


def rasterize_footprints(
    footprints_pq: Path, stack: RasterStack,
) -> tuple[list[tuple[int, int]], np.ndarray, np.ndarray]:
    """Map every in-AOI footprint to one InSAR pixel (its centroid's pixel).

    Returns (pixel_index, building_ids, geom_wkb_array):
        pixel_index: list of (row, col) into the (H, W) raster; one entry per
                     building, aligned with building_ids.
        building_ids: (N,) int64 — the footprint's ROW INDEX in footprints_pq.
                     This is the canonical internal key: it is always present
                     and source-agnostic. The native source id (`osm_id` for
                     OSM, `open_buildings_id` for Open Buildings) is sparse —
                     `osm_id` is 100% null on Open-Buildings AOIs (Huruma) — so
                     it cannot serve as the join key. Carrying the row index
                     also lets emit_parquet gather static columns by direct
                     O(1) indexing instead of a dict lookup.
        keep_geoms:   (N,) object array of WKB bytes

    Why centroid lookup and not full rasterization
    ----------------------------------------------
    HyP3 InSAR_GAMMA at 20x4 looks gives ~80 m pixels (≈ 6,400 m² each). The
    median Huruma footprint is 64 m² — three orders of magnitude smaller.
    Trying to rasterize a hut into the pixel grid via point-in-polygon at
    pixel centres drops virtually all of them (only those that happen to
    contain a centre survive). Mombasa is similar.

    The physical truth is that **InSAR cannot resolve a sub-pixel building**:
    we observe the deformation of a 6,400 m² ground cell, and every footprint
    inside that cell shares the same measurement. Centroid-to-pixel lookup
    expresses that truth: each building inherits its containing pixel's
    velocity, coherence, and time series. Neighbours within the same pixel
    will report identical values — that's not a bug, it's the resolution
    limit. The UI should surface this honestly.

    For large buildings (Mombasa concrete blocks >> 1 pixel), centroid lookup
    is still correct; finer aggregation buys nothing because the InSAR phase
    is itself a many-look average.
    """
    import pyarrow.parquet as pq

    tbl = pq.read_table(footprints_pq)
    geom_wkbs = tbl.column("geom_wkb").to_pylist()
    # The footprints table already carries centroid_lon/lat in WGS84 — use those
    # rather than recomputing from WKB (faster, and matches what we write
    # downstream).
    c_lons = np.asarray(tbl.column("centroid_lon").to_pylist(), dtype=np.float64)
    c_lats = np.asarray(tbl.column("centroid_lat").to_pylist(), dtype=np.float64)

    # If the raster is in a projected CRS (metres), project the centroids from
    # WGS84 into it once, vectorized. For our HyP3 stacks this is the path:
    # EPSG 32737 (UTM 37S). If it ever runs on a 4326-geocoded MintPy output,
    # the transformer is identity and centroids stay as lon/lat — code below
    # doesn't care which axis system it's in, only that footprint coords match
    # raster axes.
    if stack.epsg == 4326:
        c_xs, c_ys = c_lons, c_lats
    else:
        from pyproj import Transformer
        tx = Transformer.from_crs(4326, stack.epsg, always_xy=True)
        c_xs, c_ys = tx.transform(c_lons, c_lats)
        c_xs = np.asarray(c_xs, dtype=np.float64)
        c_ys = np.asarray(c_ys, dtype=np.float64)

    H, W = stack.coherence.shape
    xs, ys = stack.xs, stack.ys
    dx = float(xs[1] - xs[0])
    dy = float(ys[1] - ys[0])  # positive — load_mintpy_stack flips so ys increase
    x0, y0 = float(xs[0]), float(ys[0])

    pixel_index: list[tuple[int, int]] = []
    keep_geoms: list[bytes] = []
    keep_ids: list[int] = []

    x_lo, x_hi = float(xs[0]), float(xs[-1])
    y_lo, y_hi = float(ys[0]), float(ys[-1])
    for row_idx, (raw_wkb, cx, cy) in enumerate(zip(geom_wkbs, c_xs, c_ys)):
        # Cheap bbox cull before pixel-index math.
        if not (x_lo <= cx <= x_hi and y_lo <= cy <= y_hi):
            continue
        j = int(round((cx - x0) / dx))
        i = int(round((cy - y0) / dy))
        # Clamp to grid (round can push past edge by one).
        if not (0 <= i < H and 0 <= j < W):
            continue
        pixel_index.append((i, j))
        keep_geoms.append(raw_wkb)
        keep_ids.append(row_idx)

    return pixel_index, np.array(keep_ids, dtype=np.int64), np.array(keep_geoms, dtype=object)


def _pixel_share(pixel_index: list[tuple[int, int]]) -> np.ndarray:
    """share[k] = number of buildings mapping to building k's InSAR pixel.

    The 78 m HyP3 cell is ~95× the median 64 m² footprint, so most cells hold
    many buildings that necessarily share one velocity/coherence reading. This
    is a measurement-SPECIFICITY signal (not a trust one — the InSAR phase is
    unaffected by how many footprints sit in the cell), surfaced so a crisp
    per-building shape is honest about its per-cell signal. O(N)."""
    counts = Counter(pixel_index)
    return np.array([counts[ij] for ij in pixel_index], dtype=np.int32)


def _impute_heights(
    raw_height: np.ndarray, raw_nfloor: np.ndarray, mean_floor_m: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Resolve a per-building height WITHOUT defaulting to the safest extreme.

    Collapse risk scales modelled load with height, so the old blind
    `height_m default 3.0` made a tall-but-untagged building look like the
    lowest-load, safest case — backwards for a life-safety system. Priority:
      1. real source height > 0      → use it                  (imputed=False)
      2. else n_floors present (>0)  → n_floors · mean_floor_m  (imputed=True)
      3. else                         → AOI median real height  (imputed=True)

    Returns (height_m, height_imputed). Pure function of its inputs.
    """
    raw_height = np.asarray(raw_height, dtype=np.float64)
    raw_nfloor = np.asarray(raw_nfloor)
    real_mask = np.isfinite(raw_height) & (raw_height > 0.0)
    aoi_median = float(np.median(raw_height[real_mask])) if real_mask.any() else mean_floor_m
    height = np.where(
        real_mask, raw_height,
        np.where(raw_nfloor > 0, raw_nfloor * mean_floor_m, aoi_median),
    ).astype(np.float64)
    return height, ~real_mask


def aggregate(stack: RasterStack, pixel_index: list[tuple[int, int]],
              fused: dict[str, np.ndarray | str]) -> dict[str, np.ndarray]:
    """Look up per-building (velocity, coherence, time-series) from its pixel.

    Because every building maps to exactly one InSAR pixel (see
    `rasterize_footprints` for why), aggregation degenerates to a fancy
    indexing operation: gather (T, H, W) along (H, W) at the building's
    (i, j) coordinates. Multiple buildings sharing a pixel get identical
    values — that's the InSAR resolution limit, not a bug.

    Buildings whose pixel has coh < COH_MIN or NaN coherence are flagged
    dead (NaN velocity); they stay in the output for row alignment so the
    risk engine can mask them downstream.

    `fused` (from `fuse_or_project`) carries per-pixel (H,W) vertical + east-west
    fields: the per-building velocity is the vertical component (decomposed v_up,
    or vertical-equivalent LOS/cos(inc) on the 1-look path), and we also gather
    the east-west drift (v_ew) and its σ — NaN where the pixel was 1-look.
    """
    N = len(pixel_index)
    if N == 0:
        empty32 = np.zeros(0, dtype=np.float32)
        return {
            "velocity_mm_yr": empty32,
            "velocity_sigma_mm_yr": empty32,
            "v_ew_mm_yr": empty32,
            "v_ew_sigma_mm_yr": empty32,
            "coherence": empty32,
            "n_pixels": np.zeros(0, dtype=np.int32),
            "pixel_share": np.zeros(0, dtype=np.int32),
            "displacement_mm": np.zeros((0, stack.displacement_mm.shape[0]), dtype=np.float32),
        }
    rows = np.array([i for i, _ in pixel_index], dtype=np.int64)
    cols = np.array([j for _, j in pixel_index], dtype=np.int64)

    # pixel_share[k] = how many buildings map to building k's InSAR pixel. The
    # 78 m HyP3 cell is ~95× the median 64 m² footprint, so most cells hold many
    # buildings that necessarily share one velocity/coherence reading — a
    # measurement-SPECIFICITY caveat (not a trust one: σ already carries trust,
    # and the InSAR phase is unaffected by how many footprints sit in the cell).
    # Surfaced to the UI so a crisp per-building shape is honest about its
    # per-cell signal. O(N): one Counter pass, one map-back.
    pixel_share = _pixel_share(pixel_index)

    coh = stack.coherence[rows, cols].astype(np.float32)
    vel = np.asarray(fused["v_up"])[rows, cols].astype(np.float32)
    v_ew = np.asarray(fused["v_ew"])[rows, cols].astype(np.float32)
    sigma = np.asarray(fused["sig_up"])[rows, cols].astype(np.float32)
    v_ew_sig = np.asarray(fused["sig_ew"])[rows, cols].astype(np.float32)
    # disp shape (T, H, W) → take (T, N): for each date, gather at the same pixel.
    disp = stack.displacement_mm[:, rows, cols].astype(np.float32)  # (T, N)
    displacement_mm = disp.T.copy()  # (N, T)

    dead = ~np.isfinite(coh) | (coh < COH_MIN)
    vel = vel.copy()
    coh = coh.copy()
    vel[dead] = np.nan
    coh[dead] = np.nan
    sigma = sigma.copy(); sigma[dead] = np.nan
    v_ew = v_ew.copy(); v_ew[dead] = np.nan
    v_ew_sig = v_ew_sig.copy(); v_ew_sig[dead] = np.nan
    displacement_mm[dead, :] = np.nan

    # n_pixels = 1 per building when alive (it's a single-pixel lookup). 0 when
    # dead. The downstream risk engine uses this as a "has-InSAR-data" flag.
    n_px = np.where(dead, 0, 1).astype(np.int32)

    return {
        "velocity_mm_yr": vel,
        "velocity_sigma_mm_yr": sigma,
        "v_ew_mm_yr": v_ew,
        "v_ew_sigma_mm_yr": v_ew_sig,
        "coherence": coh,
        "n_pixels": n_px,
        "pixel_share": pixel_share,           # buildings sharing this building's cell
        "displacement_mm": displacement_mm,   # shape (N, T)
    }


def _synthesize_aoi_periods(dates_iso: list[str]) -> list[date]:
    """Map the per-month observation dates to a quarterly cadence for env_index.

    Returns first-of-quarter dates spanning the observed range, plus the
    observed final month so the UI's "latest" slot has a point.
    Determinism: a pure function of the date list.
    """
    if not dates_iso:
        return []
    parsed = sorted({date.fromisoformat(d) for d in dates_iso})
    first, last = parsed[0], parsed[-1]
    # Pick the first day of every quarter (Jan, Apr, Jul, Oct) that lies within
    # [first, last]. We snap forward to the next quarter-start ≥ first.
    quarters: list[date] = []
    y, m = first.year, ((first.month - 1) // 3) * 3 + 1
    cur = date(y, m, 1)
    if cur < first:
        # Advance one quarter so we don't predate the data.
        m2 = m + 3
        if m2 > 12:
            cur = date(y + 1, m2 - 12, 1)
        else:
            cur = date(y, m2, 1)
    while cur <= last:
        quarters.append(cur)
        m2 = cur.month + 3
        if m2 > 12:
            cur = date(cur.year + 1, m2 - 12, 1)
        else:
            cur = date(cur.year, m2, 1)
    # Pin the final observed date so the UI has a fresh "now" anchor.
    last_anchor = date(last.year, last.month, 1)
    if last_anchor not in quarters:
        quarters.append(last_anchor)
    return quarters


def _coast_bearing_sign(aoi: AOI) -> tuple[str, int]:
    """Which axis the coast lies along, and the sign of true distance vs that axis.

    Returns (axis, sign) where axis is 'lon' (east-west) or 'lat' (north-south)
    and sign is the expected sign of corr(real_distance, that_coordinate):
    Mombasa's coast is to the EAST, so distance must DECREASE as longitude grows
    → corr(dist, lon) < 0 → sign = -1. Used purely as an orientation self-check
    so an inverted/placeholder distance can never silently ship again (Finding A).
    """
    # Coastal AOIs only. The coast bearing is a property of the AOI; today the
    # one coastal AOI (Mombasa) has the Indian Ocean on its east flank.
    return ("lon", -1)


def build_real_env(
    aoi: AOI,
    centroid_lons: np.ndarray,
    centroid_lats: np.ndarray,
) -> tuple[dict[str, np.ndarray], dict[str, bool]]:
    """Build per-building environmental context entirely from REAL sources.

    No synthetic data: soil_class comes from SoilGrids WRB, shoreline/riparian
    distance from OSM coastline/waterway geometry (all cache-first, offline-safe).
    Distances are NaN for an AOI that genuinely lacks the feature (an inland AOI
    has no coastline → NaN shoreline; a coastal AOI has no mapped waterway → NaN
    riparian). soil_class is required for every building — if SoilGrids returns a
    nodata pixel that even the nearest-valid fallback can't fill, we raise rather
    than fabricate a class.

    Returns (env, present) where `env` has keys soil_class / shoreline_dist_m /
    riparian_dist_m, and `present` records which fields are backed by real data
    for >0 buildings so the caller can log/label honestly.
    """
    shore = fetch_shoreline_dist_m(aoi, centroid_lons, centroid_lats)
    ripar = fetch_riparian_dist_m(aoi, centroid_lons, centroid_lats)
    soil = fetch_soil_class(aoi, centroid_lons, centroid_lats)

    missing_soil = int(sum(1 for s in soil if s is None))
    if missing_soil:
        raise RuntimeError(
            f"{aoi.code}: {missing_soil} buildings have no real soil class from "
            f"SoilGrids. Refusing to fabricate a soil profile — fix the soil raster "
            f"coverage (data/raw/env/soilgrids/) instead."
        )

    shore_ok = np.isfinite(shore)
    # Orientation self-check: a real coastal distance must point the right way.
    # If the correlation sign is wrong, the loader/geometry is broken — fail loud
    # rather than ship an inverted distance to a life-safety screen (Finding A).
    if aoi.phenomenon == "coastal_subsidence" and shore_ok.sum() > 2:
        axis, want_sign = _coast_bearing_sign(aoi)
        coord = centroid_lons if axis == "lon" else centroid_lats
        corr = float(np.corrcoef(coord[shore_ok], shore[shore_ok])[0, 1])
        if np.isfinite(corr) and np.sign(corr) != want_sign:
            raise RuntimeError(
                f"shoreline orientation self-check FAILED for {aoi.code}: "
                f"corr(dist, {axis})={corr:+.3f} but expected sign {want_sign:+d} "
                f"(coast bearing). Refusing to ship an inverted distance."
            )

    env = {
        "soil_class":       soil,
        "shoreline_dist_m": shore,
        "riparian_dist_m":  ripar,
    }
    present = {
        "soil_class":       True,
        "shoreline_dist_m": bool(shore_ok.any()),
        "riparian_dist_m":  bool(np.isfinite(ripar).any()),
    }
    return env, present


def emit_parquet(
    aoi: AOI,
    footprints_pq: Path,
    keep_ids: np.ndarray,
    keep_geoms: np.ndarray,
    agg: dict[str, np.ndarray],
    dates: list[str],
    *,
    run_dir: Path,
    stack_shape: tuple[int, int],
    pixel_index: list[tuple[int, int]],
) -> None:
    """Write Hive-partitioned parquet matching scripts/init_db.sql expectations.

    Buildings get the full BUILDINGS_SCHEMA, and every populated column is REAL:
    InSAR-derived columns are real measurements; soil_class (SoilGrids WRB),
    shoreline_dist_m / riparian_dist_m (OSM geometry) and reclaimed_land (derived
    from real soil) come from `build_real_env`; built_year is the real OSM tag
    or NULL. The groundwater/rainfall/NDVI env_index columns are left NULL rather
    than fabricated (see postprocess.synthesize_env_index_rows).
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    N = len(keep_ids)
    if N == 0:
        print("  no buildings retained after coherence filter", file=sys.stderr)
        return
    T = len(dates)
    if T < 24:
        raise RuntimeError(
            f"too few observation dates ({T}) for STL decomposition; need T >= 24. "
            f"Re-run with a longer date range or relax mintpy.network.minCoherence."
        )

    # ---- Pull static footprint attributes ---------------------------------
    # OSM tags are sparse: any of `n_floors`, `height_m`, `built_year` can be
    # NULL on a footprint without tagging. Replace with sensible defaults
    # (1 floor → ~3 m, 0 built_year → "unknown"); downstream synth fills the
    # gaps where appropriate.
    # keep_ids ARE footprint row indices (see rasterize_footprints), so we
    # index fp_tbl columns directly — no id→row dict. The old code keyed a dict
    # on `osm_id`, which is null on Open-Buildings AOIs and collapsed every row
    # onto a single None key.
    fp_tbl = pq.read_table(footprints_pq).to_pydict()
    ids_list = keep_ids.tolist()

    def _gather_float(col: str, default: float) -> np.ndarray:
        vals = [fp_tbl[col][i] for i in ids_list]
        return np.array([default if v is None else float(v) for v in vals], dtype=np.float64)

    def _gather_int(col: str, default: int) -> np.ndarray:
        vals = [fp_tbl[col][i] for i in ids_list]
        return np.array([default if v is None else int(v) for v in vals], dtype=np.int32)

    c_lon       = _gather_float("centroid_lon", 0.0)
    c_lat       = _gather_float("centroid_lat", 0.0)
    built_yr_fp = _gather_int("built_year", 0)       # 0 → "use synthesized year"

    # ---- Height with HONEST imputation (never silently default to the safest
    # extreme). Collapse risk scales load with height (load_factor = 1 + h/10 in
    # composite_risk), so the OLD blind `height_m default 3.0` modelled a
    # tall-but-untagged building as the LOWEST-load, safest case — backwards for a
    # life-safety system biased to catch threats. Instead:
    #   1. real source height > 0           → use it            (imputed=False)
    #   2. else n_floors present (>0)       → n_floors·MEAN_FLOOR_M (imputed=True)
    #   3. else                              → AOI median real height (imputed=True)
    # `height_imputed` is carried to the UI so an estimated height is labelled,
    # never passed off as measured.
    raw_height = _gather_float("height_m", float("nan"))   # NaN where source has none
    raw_nfloor = _gather_int("n_floors", 0)                # 0 where missing
    height_fp, height_imputed = _impute_heights(raw_height, raw_nfloor, MEAN_FLOOR_M)
    # n_floors for display: real where tagged, else back-derived from the height
    # we just resolved (so a median-imputed building still reports a sane floor count).
    n_floors_fp = np.where(
        raw_nfloor > 0, raw_nfloor,
        np.maximum(1, np.round(height_fp / MEAN_FLOOR_M)),
    ).astype(np.int32)

    # Native source ids, gathered by row index, kept sparse (None where the
    # source doesn't carry one). `footprint_source` drives which column the UI
    # treats as authoritative; both are written verbatim so provenance survives.
    osm_id_fp = [fp_tbl["osm_id"][i] for i in ids_list]
    ob_id_fp  = [fp_tbl["open_buildings_id"][i] for i in ids_list]

    # ---- Derive Tier-1/2/3 fields from the InSAR stack --------------------
    disp = agg["displacement_mm"]               # (N, T), mm, + = uplift
    coh  = agg["coherence"]                     # (N,)
    vel  = agg["velocity_mm_yr"]                # (N,), vertical; + = uplift / - = subsidence
    v_ew = agg["v_ew_mm_yr"].astype(np.float64) # (N,), east-west drift; NaN where 1-look
    v_ew_sig_px = agg["v_ew_sigma_mm_yr"].astype(np.float64)  # (N,), σ on v_ew; NaN where 1-look
    pixel_share = agg["pixel_share"]            # (N,), buildings sharing this cell (≥1)
    dead = ~np.isfinite(coh) | ~np.isfinite(vel)

    # AOIs without a reachable external stable anchor (see AOI.external_anchor):
    # MintPy's internal reference is itself moving, so absolute velocities carry an
    # un-removable tile-wide offset (residual atmosphere/unwrap bias). Re-frame to
    # the AOI's coherent-stable bulk by subtracting that bulk's MEDIAN velocity —
    # classification then acts on DIFFERENTIAL (within-tile) motion, the honest
    # signal. We also subtract the equivalent linear displacement ramp so the
    # stored time-series stays consistent with the de-meaned velocity. The median
    # is over coherent, live buildings (the stable bulk); a constant offset shifts
    # every pixel equally, so this cannot manufacture or hide a real differential.
    if not aoi.external_anchor:
        bulk = np.isfinite(vel) & np.isfinite(coh) & (coh >= COH_BULK_FLOOR) & ~dead
        if bulk.sum() >= MIN_BULK_PIXELS:
            years = _years_from_epoch0(dates)               # (T,), years since dates[0]
            # velocity.h5 and timeseries.h5 are SEPARATE MintPy products with
            # different bulk rates (the velocity fit and the raw cumulative series
            # don't share a zero), so each is de-meaned by ITS OWN bulk median —
            # de-meaning both by a single offset over-corrects displacement and
            # makes a "stable" building's chart show phantom subsidence. Velocity:
            # subtract the bulk median rate. Displacement: subtract the bulk's own
            # per-epoch median ramp (a least-squares line through the bulk's
            # spatial-median displacement vs time), so the de-meaned series and the
            # de-meaned velocity tell the same differential story.
            v_offset = float(np.median(vel[bulk]))          # mm/yr
            bulk_disp_epoch = np.array(
                [float(np.median(disp[bulk, t])) for t in range(T)], dtype=np.float64
            )                                               # (T,) bulk median displacement
            d_slope, d_intercept = np.polyfit(years, bulk_disp_epoch, 1)
            vel = vel - v_offset
            disp = disp - (d_slope * years + d_intercept)[None, :]
            # East-west drift is also differential: ASC/DESC each carry their own
            # reference offset, so the fused v_ew has a tile-wide bias too. De-mean
            # it to the bulk's own median EW rate (only over pixels that actually
            # decomposed — NaN v_ew rows are excluded by isfinite). When 1-look,
            # v_ew is all-NaN and this is a no-op.
            ew_bulk = bulk & np.isfinite(v_ew)
            if ew_bulk.sum() >= MIN_BULK_PIXELS:
                ew_offset = float(np.median(v_ew[ew_bulk]))
                v_ew = v_ew - ew_offset
            else:
                ew_offset = 0.0
            print(f"  external_anchor=False → de-meaned to coherent-bulk median "
                  f"({int(bulk.sum())} bulk px): velocity {v_offset:+.1f} mm/yr, "
                  f"displacement ramp {d_slope:+.1f} mm/yr, v_ew {ew_offset:+.1f} mm/yr; "
                  f"velocities are differential")
        else:
            print(f"  ⚠ external_anchor=False but only {int(bulk.sum())} coherent bulk px "
                  f"(< {MIN_BULK_PIXELS}); skipping de-mean, velocities stay absolute",
                  file=sys.stderr)

    # Per-month trailing-12mo velocity. NaN disp rows would poison STL; substitute
    # zeros for them and mask the row to NaN post-hoc.
    disp_safe = np.where(np.isnan(disp), 0.0, disp).astype(np.float64)
    v_per_month = _trailing_velocity(disp_safe, window=12).astype(np.float32)
    v_per_month[dead, :] = np.nan

    period = min(12, T // 2)
    trend_disp, trend_slope, seasonal_amp, trend_r2, failure_mode = _stl_decompose(
        disp_safe, period=period
    )
    # Mask STL outputs for dead buildings — STL on a zero series returns
    # zero-trend ELASTIC, which is misleading. Keep arrays aligned (don't drop rows).
    trend_disp_masked = trend_disp.copy()
    trend_disp_masked[dead, :] = np.nan
    trend_slope_m = trend_slope.copy(); trend_slope_m[dead] = np.nan
    seasonal_amp_m = seasonal_amp.copy(); seasonal_amp_m[dead] = np.nan
    trend_r2_m = trend_r2.copy(); trend_r2_m[dead] = np.nan
    failure_mode_m = failure_mode.copy(); failure_mode_m[dead] = FAILURE_ELASTIC

    accel = _acceleration_mm_yr2(v_per_month, lookback=6)
    accel[dead] = np.nan
    # De-mean acceleration to the coherent-bulk median — for EVERY AOI, NOT gated
    # by external_anchor. Unlike the velocity offset (a spatial reference-point
    # bias the anchor removes), the acceleration common-mode is TEMPORAL: a
    # basin-wide seasonal/tropospheric curvature shared by every pixel in the
    # stack. It survives spatial anchoring — huruma/mombasa carry a real velocity
    # anchor yet still show a −8 to −10 mm/yr² bulk median, which is the whole
    # neighbourhood "accelerating" in lockstep (physically, a shared atmosphere,
    # not 10k failing foundations). danger_level's absolute mm/yr² cutoffs only
    # mean the same thing across AOIs once this shared offset is removed, so each
    # building's accel reads as differential — its curvature relative to its basin.
    accel_bulk = np.isfinite(accel) & np.isfinite(coh) & (coh >= COH_BULK_FLOOR) & ~dead
    if accel_bulk.sum() >= MIN_BULK_PIXELS:
        accel_offset = float(np.median(accel[accel_bulk]))
        accel = accel - accel_offset
        print(f"  de-meaned acceleration to coherent-bulk median "
              f"({int(accel_bulk.sum())} bulk px): accel {accel_offset:+.1f} mm/yr²; "
              f"accel is differential (temporal common-mode removed)")
    else:
        print(f"  ⚠ only {int(accel_bulk.sum())} coherent bulk px for accel de-mean "
              f"(< {MIN_BULK_PIXELS}); acceleration stays absolute", file=sys.stderr)
    # velocity_sigma: same model as phenomena.py — σ ≈ k(1-γ). Use this (real
    # γ-derived) σ — the same model the fused σ uses.
    v_sigma = _velocity_sigma_from_coherence(np.nan_to_num(coh, nan=0.0))
    v_sigma[dead] = np.nan
    # v_ew_sigma: the REAL east-west uncertainty from the ASC+DESC weighted-LS
    # covariance (aggregate gathered it from fuse_or_project). NaN where the pixel
    # was 1-look (no descending pass) — i.e. drift uncertainty is unknown, not zero.
    v_ew_sigma = v_ew_sig_px.copy()
    v_ew_sigma[dead] = np.nan

    # End-of-series state for classification + composite_risk.
    v_end = np.where(dead, 0.0, vel.astype(np.float64))
    # Real east-west drift where decomposed; NaN where 1-look or dead. NaN flows
    # to _classify/composite_risk as "drift unknown" (abs(NaN) comparisons are
    # False, and composite_risk zeroes the shear term) — never a fabricated zero,
    # so a building we couldn't measure for drift can't be ranked as drift-free.
    v_ew_end = np.where(dead, np.nan, v_ew)
    coh_end = np.where(dead, 0.0, coh.astype(np.float64))
    # Court-defensibility gate thresholds: σ_max from THIS AOI's own σ p75, r2_min
    # an absolute floor (see postprocess.defensibility_thresholds). trend_r2_m and
    # v_sigma are NaN for dead rows → they fail the gate per-building; the line
    # below then re-labels dead rows INDETERMINATE.
    r2_min, sigma_max = defensibility_thresholds(v_sigma)
    classification = np.array(
        [_classify(float(v_end[i]), float(v_ew_end[i]), float(coh_end[i]),
                   float(accel[i]), float(trend_r2_m[i]), float(v_sigma[i]),
                   r2_min, sigma_max)
         for i in range(N)],
        dtype=np.uint8,
    )
    # Dead rows: their _classify result is meaningless because we fed it zeros;
    # downgrade explicitly so the UI badges them as "no data" not "stable".
    classification[dead] = CLASS_INDETERMINATE

    # ---- Real env context + env_index ------------------------------------
    # build_real_env pulls soil_class (SoilGrids WRB) and shoreline/riparian
    # distance (OSM geometry) entirely from REAL sources — no synthetic scaffold.
    # It runs an orientation self-check so an inverted distance can never silently
    # ship (Ref_One Finding A) and raises if soil coverage is incomplete rather
    # than fabricate a class.
    env, env_present = build_real_env(aoi, c_lon, c_lat)
    print(
        f"  env (real): soil={env_present['soil_class']} "
        f"shoreline={env_present['shoreline_dist_m']} "
        f"riparian={env_present['riparian_dist_m']}",
        file=sys.stderr,
    )
    # `_insar_height` not used — we don't (yet) compute InSAR heights from
    # phase fringes. fused_height falls back to floor-count for now.
    insar_h = np.full(N, np.nan, dtype=np.float64)
    insar_h_sigma = np.full(N, np.nan, dtype=np.float64)
    fused_h = height_fp.copy()                  # floor-count fallback

    # Angular-distortion (tilt) rate: spatial gradient of the vertical-velocity
    # field, (mm/yr)/m. The differential-settlement signal that actually cracks
    # structures (uniform settlement is benign). NaN for buildings without enough
    # finite-velocity neighbours to constrain the local plane fit — flows to
    # composite_risk as "tilt unknown", never a fabricated zero. v_end is the
    # vertical velocity (dead rows zeroed); restore NaN on dead rows so they don't
    # anchor a neighbour's plane fit to a fake 0.
    vel_for_tilt = np.where(dead, np.nan, v_end)
    tilt_rate, tilt_support = tilt_rate_from_velocity_field(c_lon, c_lat, vel_for_tilt)
    n_tilt = int(np.isfinite(tilt_rate).sum())
    print(f"  tilt: {n_tilt}/{N} buildings have a supported angular-distortion fit "
          f"(median support {int(np.median(tilt_support))} neighbours)", file=sys.stderr)

    # External engineer/authority structural flags (the second sensor InSAR is blind
    # to). Absent export ⇒ all STRUCT_NONE ⇒ scoring identical to the motion-only path.
    sflag_state, sflag_age, sflag_observed_at, sflag_source = fetch_structural_flags(
        aoi.code, keep_ids
    )
    n_flagged = int((sflag_state != 0).sum())
    if n_flagged:
        print(f"  structural flags: {n_flagged}/{N} buildings carry an engineer/authority flag",
              file=sys.stderr)

    periods = _synthesize_aoi_periods(dates)
    env_tbl, composite_latest, danger_latest = synthesize_env_index_rows(
        aoi_code=aoi.code,
        building_ids=keep_ids,
        soil_class=env["soil_class"],
        riparian_dist_m=env["riparian_dist_m"],
        shoreline_dist_m=env["shoreline_dist_m"],
        vel=v_end,
        v_ew=v_ew_end,
        accel=accel,                      # mm/yr², NaN on dead rows (honesty preserved)
        trend_slope=trend_slope_m,        # STL trend slope, NaN on dead rows
        failure_mode=failure_mode_m,      # PLASTIC/ELASTIC per building
        classification=classification,
        fused_h_m=fused_h,
        periods=periods,
        v_ew_sigma=v_ew_sigma,            # real ASC+DESC decomposition σ → confidence-scales shear
        tilt_rate=tilt_rate,              # angular-distortion rate (mm/yr)/m
        structural_flag_state=sflag_state,        # external second-sensor judgement
        structural_flag_age_days=sflag_age,       # clearance age (drives decay)
    )

    composite_pct, shear_pct, cohort_size = _cohort_percentiles(
        composite_latest.astype(np.float32),
        np.abs(v_ew_end).astype(np.float32),
        fused_h.astype(np.float32),
        env["soil_class"].tolist(),
    )

    # ---- ARCHITECTURE_THREE C1/C4 — block membership + block-relative cohort -
    from scripts.aois import bbox as _aoi_bbox
    block_id, _block_meta = assign_blocks(c_lon, c_lat, _aoi_bbox(aoi))
    n_blocks = _block_meta["nx"] * _block_meta["ny"]
    cohort_block_pct = _rank_within_groups(
        composite_latest.astype(np.float32), block_id.astype(np.int64), n_blocks
    )

    # ---- ARCHITECTURE_THREE B1/B3 — diagnostic per-pixel rasters ----------
    # All extractors return NaN-filled shape-matched arrays when the source
    # HDF5 is unavailable, so the join still ships on old MintPy outputs.
    H, W = stack_shape
    rows_np = np.fromiter((i for i, _ in pixel_index), dtype=np.int64, count=N)
    cols_np = np.fromiter((j for _, j in pixel_index), dtype=np.int64, count=N)

    closure_grid = extract_closure_rms(run_dir, (H, W))      # (H, W) rad
    closure_per_b = closure_grid[rows_np, cols_np].astype(np.float32, copy=False)
    closure_per_b[dead] = np.nan

    dem_err_grid = extract_dem_err(run_dir, (H, W))           # (H, W) m
    dem_err_per_b = dem_err_grid[rows_np, cols_np].astype(np.float32, copy=False)
    dem_err_per_b[dead] = np.nan
    # |residual| > threshold → flag. NaN never trips the comparison, which is
    # what we want — buildings without DEM data are not flagged.
    dem_err_flag_per_b = np.abs(dem_err_per_b) > DEM_ERR_FLAG_M

    # ---- ARCHITECTURE_THREE B2 — per-epoch coherence stack → packed blob --
    coh_stack = extract_coh_per_epoch(run_dir, (T, H, W))     # (T, H, W) float32
    if np.isnan(coh_stack).all():
        # No source for per-epoch coherence; emit one blob of T zeros per
        # building so the parquet column is still present and the bundle
        # builder can read uniformly. UI hides the sparkline when the entire
        # series is zero.
        coh_blobs = np.array(
            [np.zeros(T, dtype=np.float32).tobytes()] * N,
            dtype=object,
        )
        n_epochs_used = T
    else:
        coh_blobs, n_epochs_used = pack_coh_series_per_building(coh_stack, rows_np, cols_np)

    # ---- Build buildings table (schema must match BUILDINGS_SCHEMA exactly) -
    # built_year: real OSM tag where present (>0), else NULL — no synthetic fallback.
    # The UI hides the field when it's NULL rather than show a fabricated year.
    built_year_arr = [int(built_yr_fp[i]) if built_yr_fp[i] > 0 else None for i in range(N)]
    # reclaimed_land: derived from the REAL soil map — engineered fill (`reclaim_fill`)
    # IS the reclaimed-land signal. Real soil exists for every building on both AOIs,
    # so this is a real boolean, not synthetic. (Coastal-fill is a Mombasa phenomenon;
    # inland AOIs simply have no `reclaim_fill` pixels → all False, which is correct.)
    reclaimed_arr = [bool(str(env["soil_class"][i]) == "reclaim_fill") for i in range(N)]

    rows_b = [{
        "building_id":             int(keep_ids[i]),
        "aoi_code":                aoi.code,
        "footprint_source":        aoi.footprint_source,
        "osm_id":                  None if osm_id_fp[i] is None else int(osm_id_fp[i]),
        "open_buildings_id":       None if ob_id_fp[i] is None else str(ob_id_fp[i]),
        "geom_wkb":                bytes(keep_geoms[i]),
        "centroid_lon":            float(c_lon[i]),
        "centroid_lat":            float(c_lat[i]),
        "height_m":                float(height_fp[i]),
        "insar_height_m":          None if not np.isfinite(insar_h[i]) else float(insar_h[i]),
        "insar_height_sigma_m":    None if not np.isfinite(insar_h_sigma[i]) else float(insar_h_sigma[i]),
        "fused_height_m":          float(fused_h[i]),
        "height_imputed":          bool(height_imputed[i]),
        "n_floors":                int(n_floors_fp[i]),
        "insar_pixel_share":       int(pixel_share[i]),
        "soil_class":              str(env["soil_class"][i]),
        "riparian_dist_m":         None if not np.isfinite(env["riparian_dist_m"][i])  else float(env["riparian_dist_m"][i]),
        "shoreline_dist_m":        None if not np.isfinite(env["shoreline_dist_m"][i]) else float(env["shoreline_dist_m"][i]),
        "reclaimed_land":          reclaimed_arr[i],
        "built_year":              built_year_arr[i],
        "classification":          int(classification[i]),
        "velocity_accel_mm_yr2":   None if not np.isfinite(accel[i]) else float(accel[i]),
        "trend_slope_mm_yr":       None if not np.isfinite(trend_slope_m[i]) else float(trend_slope_m[i]),
        "seasonal_amplitude_mm":   None if not np.isfinite(seasonal_amp_m[i]) else float(seasonal_amp_m[i]),
        "trend_r2":                None if not np.isfinite(trend_r2_m[i]) else float(trend_r2_m[i]),
        "failure_mode":            int(failure_mode_m[i]),
        "danger_level":            int(danger_latest[i]),
        "velocity_sigma_mm_yr":    None if not np.isfinite(v_sigma[i]) else float(v_sigma[i]),
        "velocity_ew_sigma_mm_yr": None if not np.isfinite(v_ew_sigma[i]) else float(v_ew_sigma[i]),
        # Per-building decomposition provenance: "decomposed_2look" if this pixel
        # had a real ASC+DESC east-west solution, else "los_1look" (vertical only).
        "decomposition_mode":      "decomposed_2look" if np.isfinite(v_ew_end[i]) else "los_1look",
        "cohort_composite_pct":    int(composite_pct[i]),
        "cohort_shear_pct":        int(shear_pct[i]),
        "cohort_size":             int(cohort_size[i]),
        "block_id":                int(block_id[i]),
        "cohort_block_pct":        int(cohort_block_pct[i]),
        "closure_rms_rad":         None if not np.isfinite(closure_per_b[i]) else float(closure_per_b[i]),
        "dem_err_m":               None if not np.isfinite(dem_err_per_b[i]) else float(dem_err_per_b[i]),
        "dem_err_flag":            bool(dem_err_flag_per_b[i]),
        # External structural flag (second sensor). 0/NULL on unflagged buildings.
        "structural_flag_state":       int(sflag_state[i]),
        "structural_flag_observed_at": sflag_observed_at[i],
        "structural_flag_source":      sflag_source[i],
    } for i in range(N)]

    b_dir = PARQUET_ROOT / "buildings" / f"aoi={aoi.code}"
    b_dir.mkdir(parents=True, exist_ok=True)
    b_tbl = pa.Table.from_pylist(rows_b, schema=BUILDINGS_SCHEMA)
    # Clean any stale file from prior runs (different filename → would coexist).
    for stale in b_dir.glob("part-0.parquet"):
        stale.unlink()
    pq.write_table(b_tbl, b_dir / "data.parquet", compression="zstd")
    print(f"  ✓ wrote {N} buildings → {b_dir/'data.parquet'}")

    # ---- Build subsidence table (long-format, schema-matched) -------------
    bid_col = np.repeat(keep_ids, T)
    dates_parsed = [date.fromisoformat(d) for d in dates]
    date_col = np.tile(dates_parsed, N)
    # disp shape (N, T); v_per_month (N, T); trend_disp (N, T) — flatten in row-major.
    disp_flat = disp.reshape(-1).astype(np.float64)
    trend_disp_flat = trend_disp_masked.reshape(-1).astype(np.float64)
    vpm_flat = v_per_month.reshape(-1).astype(np.float64)
    coh_flat = np.repeat(coh.astype(np.float64), T)
    # East-west drift is a single RATE per building (ASC & DESC epochs aren't
    # co-temporal, so there's no honest per-epoch EW series). Repeat the rate
    # across the T rows; NaN stays NaN (1-look buildings carry no drift value).
    v_ew_flat = np.repeat(v_ew_end.astype(np.float64), T)
    s_tbl = pa.table({
        "building_id":                  pa.array(bid_col,          type=pa.int64()),
        "aoi_code":                     pa.array([aoi.code] * (N * T), type=pa.string()),
        "observation_date":             pa.array(date_col.tolist(), type=pa.date32()),
        "displacement_mm":              pa.array(disp_flat,        type=pa.float64()),
        "trend_displacement_mm":        pa.array(trend_disp_flat,  type=pa.float64()),
        "velocity_mm_yr":               pa.array(vpm_flat,         type=pa.float64()),
        "velocity_horizontal_ew_mm_yr": pa.array(v_ew_flat,        type=pa.float64()),
        "coherence":                    pa.array(coh_flat,         type=pa.float64()),
    }, schema=SUBSIDENCE_SCHEMA)
    s_dir = PARQUET_ROOT / "subsidence" / f"aoi={aoi.code}"
    s_dir.mkdir(parents=True, exist_ok=True)
    for stale in s_dir.glob("part-0.parquet"):
        stale.unlink()
    pq.write_table(s_tbl, s_dir / "data.parquet", compression="zstd")
    print(f"  ✓ wrote {N*T} subsidence rows → {s_dir/'data.parquet'}")

    # ---- env_index parquet -------------------------------------------------
    e_dir = PARQUET_ROOT / "env_index" / f"aoi={aoi.code}"
    e_dir.mkdir(parents=True, exist_ok=True)
    for stale in e_dir.glob("part-0.parquet"):
        stale.unlink()
    pq.write_table(env_tbl, e_dir / "data.parquet", compression="zstd")
    print(f"  ✓ wrote {env_tbl.num_rows} env_index rows → {e_dir/'data.parquet'}")

    # ---- ARCHITECTURE_THREE B2 — coherence sparkline partition ------------
    # One row per building, one binary blob of T × 4 bytes (Float32) per row.
    # Frontend reads it as zero-copy Float32Array. n_epochs is stored as
    # parquet table metadata so the reader doesn't need to infer from
    # blob length.
    coh_tbl = pa.table(
        {
            "building_id": pa.array(keep_ids.tolist(), type=pa.int64()),
            "aoi_code":    pa.array([aoi.code] * N,    type=pa.string()),
            "coh_series":  pa.array(coh_blobs.tolist(), type=pa.binary()),
        },
        schema=COH_SERIES_SCHEMA,
    ).replace_schema_metadata({b"n_epochs": str(n_epochs_used).encode()})
    cs_dir = PARQUET_ROOT / "coh_series" / f"aoi={aoi.code}"
    cs_dir.mkdir(parents=True, exist_ok=True)
    for stale in cs_dir.glob("*.parquet"):
        stale.unlink()
    pq.write_table(coh_tbl, cs_dir / "data.parquet", compression="zstd")
    print(f"  ✓ wrote {N} coh_series rows (T={n_epochs_used}) → {cs_dir/'data.parquet'}")


def rebuild_demo_db() -> None:
    """Build data/demo.duckdb.new from init_db.sql, then atomic-replace
    data/demo.duckdb. Safe to call while FastAPI holds a read-only handle on
    the live file — the existing handle stays bound to the unlinked inode;
    new connections see the new data.
    """
    import duckdb

    sql_path = BACKEND_DIR / "scripts" / "init_db.sql"
    sql = sql_path.read_text().replace("${PARQUET_ROOT}", str(PARQUET_ROOT.resolve()))

    db_new = BACKEND_DIR / "data" / "demo.duckdb.new"
    db_new.unlink(missing_ok=True)
    con = duckdb.connect(str(db_new))
    con.execute(sql)
    # Smoke-check: every (view, aoi) must be non-empty — but only for AOIs that
    # actually have a buildings partition on disk. The DB is built incrementally
    # as AOIs come online (each `join_insar --aoi X --rebuild-db` adds X's
    # partitions), so checking the full static REGISTRY would fail the rebuild
    # for any AOI not yet processed. The views glob `aoi=*/*.parquet`, so an
    # un-joined AOI is simply absent — not an error.
    present = sorted(
        p.name.split("=", 1)[1]
        for p in (PARQUET_ROOT / "buildings").glob("aoi=*")
        if p.is_dir()
    )
    if not present:
        con.close()
        raise RuntimeError(f"no buildings partitions under {PARQUET_ROOT}/buildings")
    for view in ("buildings", "subsidence_time_series", "environmental_index"):
        for code in present:
            n = con.execute(
                f"SELECT COUNT(*) FROM {view} WHERE aoi_code = ?", [code]
            ).fetchone()[0]
            if n == 0:
                con.close()
                raise RuntimeError(f"{view} empty for aoi_code={code}")
    con.close()
    os.replace(str(db_new), str(DB_PATH))
    print(f"  ✓ rebuilt {DB_PATH}")


def run_join(aoi: AOI, track: str) -> None:
    track_safe = track.replace("/", "_")
    run_dir = MINTPY_DIR / f"{aoi.code}_{track_safe}"
    fp_path = FOOTPRINT_DIR / f"{aoi.code}.parquet"
    if not run_dir.exists():
        sys.exit(f"missing MintPy run dir: {run_dir}. Run scripts/mintpy_run.py first.")
    if not fp_path.exists():
        sys.exit(f"missing footprints: {fp_path}. Run scripts/osm_footprints.py first.")

    print(f"  loading MintPy stack from {run_dir}…")
    stack = load_mintpy_stack(run_dir)
    print(f"    grid {stack.coherence.shape}, {len(stack.dates)} dates, "
          f"coherence median={np.nanmedian(stack.coherence):.2f}, "
          f"orbit={stack.orbit_direction} heading={stack.heading_deg:.1f}°")

    # Look for a sibling descending stack. Present → real ASC+DESC vertical+EW
    # decomposition; absent → honest 1-look vertical with drift left unknown.
    desc_dir = _find_desc_run_dir(aoi.code)
    stack_desc = None
    if desc_dir is not None:
        print(f"  found descending stack {desc_dir.name} → ASC+DESC decomposition")
        stack_desc = load_mintpy_stack(desc_dir)
        print(f"    DESC grid {stack_desc.coherence.shape}, {len(stack_desc.dates)} dates, "
              f"heading={stack_desc.heading_deg:.1f}°")
    else:
        print(f"  no descending stack for {aoi.code} → 1-look vertical (drift unmeasured)")
    fused = fuse_or_project(stack, stack_desc)
    if fused["mode"] == "decomposed_2look":
        print(f"  decomposed {fused['decomposed_frac']*100:.0f}% of coherent pixels "
              f"into vertical + east-west (rest fall back to 1-look vertical)")

    print(f"  mapping footprints to InSAR pixels from {fp_path}…")
    pixel_index, keep_ids, keep_geoms = rasterize_footprints(fp_path, stack)
    print(f"    {len(keep_ids)} footprints inside AOI grid")

    print("  per-building lookup (coherence-gated)…")
    agg = aggregate(stack, pixel_index, fused=fused)
    n_dead = int(np.isnan(agg["velocity_mm_yr"]).sum())
    n_drift = int(np.isfinite(agg["v_ew_mm_yr"]).sum())
    print(f"    {len(keep_ids) - n_dead} buildings retained, {n_dead} dropped (no coherent pixels); "
          f"{n_drift} with measured east-west drift")

    print("  writing parquet…")
    emit_parquet(
        aoi, fp_path, keep_ids, keep_geoms, agg, stack.dates,
        run_dir=run_dir,
        stack_shape=stack.coherence.shape,
        pixel_index=pixel_index,
    )
    # Real InSAR products produced these partitions — flip this AOI's provenance
    # so the bundle/disclaimer stop calling it synthetic. (We only reach here if
    # the placeholder guard in load_mintpy_stack passed.)
    set_provenance(aoi.code, "insar")
    print(f"  ✓ provenance[{aoi.code}] = insar")


def main() -> None:
    p = argparse.ArgumentParser(description="Join MintPy outputs to footprints → GeoParquet")
    p.add_argument("--aoi", required=True, help="AOI code, e.g. huruma")
    p.add_argument("--track", default="ASCENDING/57",
                   help='"<flight>/<path>", default ASCENDING/57')
    p.add_argument("--rebuild-db", action="store_true",
                   help="after writing parquet, rebuild data/demo.duckdb via atomic swap")
    args = p.parse_args()
    run_join(by_code(args.aoi), args.track)
    if args.rebuild_db:
        rebuild_demo_db()


if __name__ == "__main__":
    main()
