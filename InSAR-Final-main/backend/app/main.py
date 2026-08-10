"""
FastAPI service for the infra-proptech MVP.

Reads from a single DuckDB file produced by `python -m scripts.seed_synthetic`
(or, eventually, the real HyP3+MintPy pipeline). No live network calls.

Run:
    cd backend
    uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import hashlib
import json
import struct
from contextlib import asynccontextmanager
from pathlib import Path

import duckdb
import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from scripts.postprocess import _block_grid_meta, aggregate_blocks, defensibility_thresholds
from scripts.provenance import get_provenance
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from starlette.background import BackgroundTask

from app import config
from app.ratelimit import rate_limit
from app.usage_meter import meter_bundle_fetch, usage_metering_enabled

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "demo.duckdb"


class _DB:
    con: duckdb.DuckDBPyConnection | None = None
    aoi_codes: set[str] = set()
    bundles: dict[str, bytes] = {}        # AOI bundle, precomputed at startup, served as-is
    bundle_etags: dict[str, str] = {}     # content-hash ETag per AOI; computed once

    def cursor(self) -> duckdb.DuckDBPyConnection:
        if self.con is None:
            raise RuntimeError("DB not initialized")
        return self.con.cursor()


db = _DB()


# ---------- bundle builder (runs once at startup; serving is O(1) bytes) ----

def _build_bundle(con: duckdb.DuckDBPyConnection, aoi_code: str) -> bytes:
    """Build one binary payload per AOI:

        [u32 header_len][header_json_utf8][packed binary arrays]

    Header JSON contains AOI metadata + offsets/lengths/dtype for each array
    in the binary section. Frontend reads the response as ArrayBuffer once and
    slices typed-array views into it with zero copies.
    """
    # --- AOI metadata
    meta_row = con.execute(
        """
        SELECT aoi_code, name, center_lon, center_lat, side_m, phenomenon,
               footprint_source, narrative,
               bbox_minlon, bbox_minlat, bbox_maxlon, bbox_maxlat,
               reference_lon, reference_lat, reference_note
        FROM aoi_registry WHERE aoi_code = ?
        """,
        [aoi_code],
    ).fetchone()
    if meta_row is None:
        raise RuntimeError(f"AOI {aoi_code!r} not in registry")

    # --- Static buildings: id + footprint polygon (lon/lat ring) + static attrs
    # ST_AsGeoJSON keeps coordinates as nested arrays; we serialize once into
    # flat [n_buildings] of variable-length rings via a flat ring_coords array
    # + ring_offsets for O(1) lookup by building index.
    cur = con.execute(
        """
        SELECT building_id, ST_AsGeoJSON(geom),
               height_m, insar_height_m, insar_height_sigma_m, fused_height_m,
               height_imputed, insar_pixel_share,
               n_floors, soil_class,
               riparian_dist_m, shoreline_dist_m, reclaimed_land,
               classification, velocity_accel_mm_yr2,
               trend_slope_mm_yr, seasonal_amplitude_mm, trend_r2, failure_mode,
               danger_level,
               velocity_sigma_mm_yr, velocity_ew_sigma_mm_yr,
               cohort_composite_pct, cohort_shear_pct, cohort_size,
               block_id, cohort_block_pct,
               closure_rms_rad, dem_err_m, dem_err_flag,
               structural_flag_state
        FROM buildings WHERE aoi_code = ?
        ORDER BY building_id
        """,
        [aoi_code],
    )
    bids: list[int] = []
    heights: list[float] = []
    insar_heights: list[float] = []
    insar_height_sigmas: list[float] = []
    fused_heights: list[float] = []
    height_imputed: list[int] = []
    insar_pixel_share: list[int] = []
    n_floors_list: list[int] = []
    soil_classes: list[str] = []
    riparian_dist: list[float] = []
    shoreline_dist: list[float] = []
    reclaimed_land: list[bool] = []
    classifications: list[int] = []
    velocity_accel: list[float] = []
    trend_slope: list[float] = []
    seasonal_amp: list[float] = []
    trend_r2_list: list[float] = []
    failure_mode: list[int] = []
    danger_level: list[int] = []
    velocity_sigma: list[float] = []
    velocity_ew_sigma: list[float] = []
    cohort_composite_pct: list[int] = []
    cohort_shear_pct: list[int] = []
    cohort_size: list[int] = []
    # ARCHITECTURE_THREE C1/C4 — block membership + block-relative cohort pct
    block_ids: list[int] = []
    cohort_block_pct: list[int] = []
    # ARCHITECTURE_THREE B1/B3 — diagnostic columns
    closure_rms: list[float] = []
    dem_err: list[float] = []
    dem_err_flag: list[int] = []
    # External structural flag: 0=NONE 1=CLEARED 2=UNSAFE 3=AUTH_UNSAFE. Served so the
    # sidebar can show a "ground-verified" provenance badge (a human assessed this
    # building) — distinct from the motion-threat `classification`. 0/NULL = unflagged.
    structural_flag_state: list[int] = []
    ring_coords: list[float] = []   # flat [lon,lat,lon,lat,...]
    ring_offsets: list[int] = [0]   # [n_buildings + 1]; ring_offsets[i+1]-ring_offsets[i] = vertex count * 2

    for row in cur.fetchall():
        (bid, geojson, height_m, insar_h, insar_sigma, fused_h,
         h_imp, px_share,
         nf, soil, ripa, shore, recl, cls, accel,
         t_slope, s_amp, t_r2, fmode, dlevel,
         v_sig, v_ew_sig, c_comp_pct, c_shear_pct, c_size,
         blk_id, c_block_pct,
         closure_r, dem_e, dem_f, sflag_state) = row
        bids.append(bid)
        heights.append(height_m if height_m is not None else 0.0)
        # NaN (not 0.0) when there is no InSAR height — the real path never
        # computes one (the inversion is unbuilt), so a finite 0.0 would be a lie
        # the frontend can't distinguish from a real measurement. NaN lets the UI
        # gate the InSAR height line/disagreement check off cleanly.
        insar_heights.append(insar_h if insar_h is not None else float("nan"))
        insar_height_sigmas.append(insar_sigma if insar_sigma is not None else float("nan"))
        # Fused is the source-of-truth for 3D rendering; fall back to floor
        # estimate so the map never sees a zero-height building.
        fused_heights.append(
            fused_h if fused_h is not None else (height_m if height_m is not None else 0.0)
        )
        height_imputed.append(1 if h_imp else 0)
        insar_pixel_share.append(int(px_share) if px_share is not None else 1)
        n_floors_list.append(nf if nf is not None else 0)
        soil_classes.append(soil or "")
        riparian_dist.append(ripa if ripa is not None else -1.0)
        shoreline_dist.append(shore if shore is not None else -1.0)
        reclaimed_land.append(bool(recl) if recl is not None else False)
        classifications.append(int(cls) if cls is not None else 0)
        velocity_accel.append(float(accel) if accel is not None else 0.0)
        trend_slope.append(float(t_slope) if t_slope is not None else 0.0)
        seasonal_amp.append(float(s_amp) if s_amp is not None else 0.0)
        trend_r2_list.append(float(t_r2) if t_r2 is not None else 0.0)
        failure_mode.append(int(fmode) if fmode is not None else 0)
        danger_level.append(int(dlevel) if dlevel is not None else 0)
        velocity_sigma.append(float(v_sig) if v_sig is not None else 0.0)
        velocity_ew_sigma.append(float(v_ew_sig) if v_ew_sig is not None else 0.0)
        cohort_composite_pct.append(int(c_comp_pct) if c_comp_pct is not None else 50)
        cohort_shear_pct.append(int(c_shear_pct) if c_shear_pct is not None else 50)
        cohort_size.append(int(c_size) if c_size is not None else 1)
        block_ids.append(int(blk_id) if blk_id is not None else 0)
        cohort_block_pct.append(int(c_block_pct) if c_block_pct is not None else 50)
        # NaN sentinel (-1.0) — frontend treats negative as "not available";
        # closure_rms_rad is physically in [0, π], so -1 is unambiguous.
        closure_rms.append(float(closure_r) if closure_r is not None else -1.0)
        dem_err.append(float(dem_e) if dem_e is not None else 0.0)
        dem_err_flag.append(1 if dem_f else 0)
        # NULL (unflagged) → 0 (STRUCT_NONE), the same default the column uses.
        structural_flag_state.append(int(sflag_state) if sflag_state is not None else 0)
        coords = json.loads(geojson)["coordinates"][0]  # outer ring
        # drop the closing duplicate vertex; deck.gl expects open rings
        if coords and coords[0] == coords[-1]:
            coords = coords[:-1]
        for lon, lat in coords:
            ring_coords.append(lon)
            ring_coords.append(lat)
        ring_offsets.append(len(ring_coords))

    n_buildings = len(bids)

    # --- Time series: dense [n_buildings, n_months] matrices
    months_rows = con.execute(
        """
        SELECT DISTINCT observation_date FROM subsidence_time_series
        WHERE aoi_code = ? ORDER BY observation_date
        """,
        [aoi_code],
    ).fetchall()
    dates_iso = [str(r[0]) for r in months_rows]
    n_months = len(dates_iso)

    # Pull whole matrix in one shot using DuckDB's native arrow output
    arr = con.execute(
        """
        SELECT s.building_id, s.observation_date,
               s.displacement_mm, s.trend_displacement_mm, s.velocity_mm_yr,
               s.velocity_horizontal_ew_mm_yr, s.coherence
        FROM subsidence_time_series s
        WHERE s.aoi_code = ?
        ORDER BY s.building_id, s.observation_date
        """,
        [aoi_code],
    ).fetchnumpy()

    # Build a dense ordering identical to bids list. Because we ORDERed BY
    # building_id and bids is sorted by building_id too, reshape is direct — but
    # a silent drift (a building missing a month, an extra date, a re-ordered
    # bids list) would misalign every velocity row against the wrong footprint on
    # a life-safety screen. Guard the reshape with per-row id alignment, not just
    # a total-count check. All checks are vectorized O(n_buildings · n_months).
    assert len(arr["building_id"]) == n_buildings * n_months, (
        f"row-count mismatch: expected {n_buildings * n_months} "
        f"({n_buildings} buildings × {n_months} months), got {len(arr['building_id'])} "
        f"— a building is missing months or has a duplicate observation_date."
    )
    bid_matrix = np.asarray(arr["building_id"], dtype=np.int64).reshape(n_buildings, n_months)
    bids_expected = np.asarray(bids, dtype=np.int64)
    # Each row must be a single building repeated across all months…
    assert (bid_matrix == bid_matrix[:, :1]).all(), (
        "time-series reshape misaligned: a row spans >1 building_id — the "
        "(building_id, observation_date) grid is ragged, not dense."
    )
    # …and row i must be building bids[i], so velocity[i] ↔ footprint[i] hold.
    assert (bid_matrix[:, 0] == bids_expected).all(), (
        "time-series order ≠ buildings order: ORDER BY building_id diverged "
        "between the buildings and subsidence queries — velocity rows would map "
        "to the wrong footprints."
    )
    displacement      = np.asarray(arr["displacement_mm"],              dtype=np.float32).reshape(n_buildings, n_months)
    trend_displacement = np.asarray(arr["trend_displacement_mm"],       dtype=np.float32).reshape(n_buildings, n_months)
    velocity          = np.asarray(arr["velocity_mm_yr"],               dtype=np.float32).reshape(n_buildings, n_months)
    velocity_ew       = np.asarray(arr["velocity_horizontal_ew_mm_yr"], dtype=np.float32).reshape(n_buildings, n_months)
    coherence         = np.asarray(arr["coherence"],                    dtype=np.float32).reshape(n_buildings, n_months)

    # --- Composite risk: one value per building (most recent quarter)
    risk_rows = con.execute(
        """
        WITH e AS (
            SELECT building_id, composite_risk,
                   ROW_NUMBER() OVER (PARTITION BY building_id ORDER BY period_start DESC) AS rn
            FROM environmental_index WHERE aoi_code = ?
        )
        SELECT building_id, composite_risk FROM e WHERE rn = 1 ORDER BY building_id
        """,
        [aoi_code],
    ).fetchall()
    risk_map = {r[0]: r[1] for r in risk_rows}
    composite_risk = np.array([risk_map.get(b, 0.0) for b in bids], dtype=np.float32)

    # --- ARCHITECTURE_THREE C1 — fixed-grid block aggregation -----------------
    # Blocks are a pure function of building centroids; per-building block_id was
    # assigned at join/seed time (scripts.postprocess.assign_blocks) from the AOI
    # bbox. Reconstruct the SAME grid here from the bbox so n_blocks matches, then
    # roll buildings up per block. Aggregation is O(n_buildings), recomputed once
    # at startup alongside the rest of the bundle.
    block_ids_np = np.asarray(block_ids, dtype=np.uint16)
    cohort_block_pct_np = np.asarray(cohort_block_pct, dtype=np.uint8)
    aoi_bbox = (meta_row[8], meta_row[9], meta_row[10], meta_row[11])
    grid_meta = _block_grid_meta(aoi_bbox)
    n_blocks = grid_meta["nx"] * grid_meta["ny"]
    # End-of-series velocity per building (most-negative drives "worst").
    vel_end = velocity[:, -1] if n_months > 0 else np.zeros(n_buildings, dtype=np.float32)
    block_agg = aggregate_blocks(
        block_ids_np.astype(np.int64), n_blocks,
        vel_end.astype(np.float32), composite_risk,
        np.asarray(classifications, dtype=np.uint8),
    )

    # --- ARCHITECTURE_THREE B2 — coherence sparkline: one packed Float32 stream
    # per AOI of shape (n_buildings × n_epochs). Read all blobs in one shot,
    # concatenate to a single contiguous bytes object, view as Float32Array on
    # the frontend with shape (n_buildings, n_epochs).
    # Coh_series parquet may not exist on older pipeline runs — treat absence
    # as "feature unavailable" rather than failing startup.
    try:
        coh_rows = con.execute(
            """
            SELECT building_id, coh_series FROM coh_series
            WHERE aoi_code = ? ORDER BY building_id
            """,
            [aoi_code],
        ).fetchall()
    except duckdb.Error:
        coh_rows = []

    if coh_rows:
        coh_by_bid = {r[0]: bytes(r[1]) for r in coh_rows}
        # Every blob in a partition has the same length (T × 4). Infer T from
        # the first blob — saves a parquet metadata read and is correct
        # because the writer enforces uniformity.
        sample_blob = next(iter(coh_by_bid.values()))
        n_coh_epochs = len(sample_blob) // 4
        # Buildings without a coh_series row get a zero blob — keeps the
        # frontend matrix dense, sentinel 0 means "no sample".
        zero_blob = bytes(n_coh_epochs * 4)
        coh_series_flat = b"".join(coh_by_bid.get(b, zero_blob) for b in bids)
        coh_series_np = np.frombuffer(coh_series_flat, dtype=np.float32).copy()
    else:
        n_coh_epochs = 0
        coh_series_np = np.zeros(0, dtype=np.float32)

    # --- Pack binary section
    bids_np            = np.asarray(bids,                dtype=np.int32)
    heights_np         = np.asarray(heights,             dtype=np.float32)
    insar_h_np         = np.asarray(insar_heights,       dtype=np.float32)
    insar_sigma_np     = np.asarray(insar_height_sigmas, dtype=np.float32)
    fused_h_np         = np.asarray(fused_heights,       dtype=np.float32)
    height_imputed_np  = np.asarray(height_imputed,      dtype=np.uint8)
    insar_pixel_share_np = np.asarray(insar_pixel_share, dtype=np.uint16)
    n_floors_np        = np.asarray(n_floors_list,       dtype=np.int16)
    riparian_np        = np.asarray(riparian_dist,       dtype=np.float32)
    shoreline_np       = np.asarray(shoreline_dist,      dtype=np.float32)
    reclaimed_np       = np.asarray(reclaimed_land,      dtype=np.uint8)
    classification_np  = np.asarray(classifications,     dtype=np.uint8)
    velocity_accel_np  = np.asarray(velocity_accel,      dtype=np.float32)
    trend_slope_np     = np.asarray(trend_slope,         dtype=np.float32)
    seasonal_amp_np    = np.asarray(seasonal_amp,        dtype=np.float32)
    trend_r2_np        = np.asarray(trend_r2_list,       dtype=np.float32)
    failure_mode_np    = np.asarray(failure_mode,        dtype=np.uint8)
    danger_level_np    = np.asarray(danger_level,        dtype=np.uint8)
    velocity_sigma_np  = np.asarray(velocity_sigma,      dtype=np.float32)
    velocity_ew_sigma_np = np.asarray(velocity_ew_sigma, dtype=np.float32)
    # Defensibility gate thresholds for THIS AOI, recomputed from the same σ array
    # the pipeline used (one source of truth: defensibility_thresholds). The
    # frontend ConfidencePill tints each building's σ against sigma_max so the
    # colour bands track each AOI's own distribution (Huruma ≠ Mombasa ≠ real InSAR).
    r2_min_aoi, sigma_max_aoi = defensibility_thresholds(velocity_sigma_np)
    cohort_comp_pct_np = np.asarray(cohort_composite_pct, dtype=np.uint8)
    cohort_shear_pct_np = np.asarray(cohort_shear_pct,   dtype=np.uint8)
    cohort_size_np     = np.asarray(cohort_size,         dtype=np.uint16)
    closure_rms_np     = np.asarray(closure_rms,         dtype=np.float32)
    dem_err_np         = np.asarray(dem_err,             dtype=np.float32)
    dem_err_flag_np    = np.asarray(dem_err_flag,        dtype=np.uint8)
    structural_flag_state_np = np.asarray(structural_flag_state, dtype=np.uint8)
    ring_coords_np     = np.asarray(ring_coords,         dtype=np.float32)
    ring_offsets_np    = np.asarray(ring_offsets,        dtype=np.int32)

    sections = [
        ("building_id",                 bids_np),
        ("height_m",                    heights_np),
        ("insar_height_m",              insar_h_np),
        ("insar_height_sigma_m",        insar_sigma_np),
        ("fused_height_m",              fused_h_np),
        ("height_imputed",              height_imputed_np),
        ("insar_pixel_share",           insar_pixel_share_np),
        ("n_floors",                    n_floors_np),
        ("riparian_dist_m",             riparian_np),
        ("shoreline_dist_m",            shoreline_np),
        ("reclaimed_land",              reclaimed_np),
        ("classification",              classification_np),
        ("velocity_accel_mm_yr2",       velocity_accel_np),
        ("trend_slope_mm_yr",           trend_slope_np),
        ("seasonal_amplitude_mm",       seasonal_amp_np),
        ("trend_r2",                    trend_r2_np),
        ("failure_mode",                failure_mode_np),
        ("danger_level",                danger_level_np),
        ("velocity_sigma_mm_yr",        velocity_sigma_np),
        ("velocity_ew_sigma_mm_yr",     velocity_ew_sigma_np),
        ("cohort_composite_pct",        cohort_comp_pct_np),
        ("cohort_shear_pct",            cohort_shear_pct_np),
        ("cohort_size",                 cohort_size_np),
        # ARCHITECTURE_THREE C1/C4 — per-building block membership + block cohort
        ("block_id",                    block_ids_np),
        ("cohort_block_pct",            cohort_block_pct_np),
        # ARCHITECTURE_THREE C1 — per-block aggregates (len = n_blocks)
        ("block_count",                 block_agg["count"]),
        ("block_worst_velocity",        block_agg["worst_velocity"]),
        ("block_mean_risk",             block_agg["mean_risk"]),
        ("block_max_risk",              block_agg["max_risk"]),
        ("block_confirmed",             block_agg["confirmed"]),
        # ARCHITECTURE_THREE B1/B3
        ("closure_rms_rad",             closure_rms_np),
        ("dem_err_m",                   dem_err_np),
        ("dem_err_flag",                dem_err_flag_np),
        # External structural-flag state (ground-verified provenance badge).
        ("structural_flag_state",       structural_flag_state_np),
        ("ring_coords",                 ring_coords_np),
        ("ring_offsets",                ring_offsets_np),
        ("displacement_mm",             displacement.reshape(-1)),
        ("trend_displacement_mm",       trend_displacement.reshape(-1)),
        ("velocity_mm_yr",              velocity.reshape(-1)),
        ("velocity_horizontal_ew",      velocity_ew.reshape(-1)),
        ("coherence",                   coherence.reshape(-1)),
        # ARCHITECTURE_THREE B2 — packed (n_buildings × n_coh_epochs) Float32
        ("coh_series",                  coh_series_np),
        ("composite_risk",              composite_risk),
    ]

    body = bytearray()
    arrays_meta: list[dict] = []
    # Each section is padded to an 8-byte boundary so the browser can build
    # Int32Array / Float32Array / (future) Float64Array views directly. Without
    # this, a u1 or i2 array shifts every following section by 1/2 bytes and
    # `new Int32Array(buf, offset, n)` throws "start offset should be a multiple of 4".
    ALIGN = 8
    for name, ndarr in sections:
        pad = (-len(body)) % ALIGN
        if pad:
            body.extend(b"\x00" * pad)
        offset = len(body)
        contig = np.ascontiguousarray(ndarr)
        body.extend(contig.tobytes())
        arrays_meta.append({
            "name":   name,
            "dtype":  contig.dtype.str.lstrip("<>=|"),   # e.g. 'f4', 'i4', 'i2', 'u1'
            "byteOrder": "<",                            # numpy default on x86; FE will use DataView little-endian
            "shape":  list(contig.shape),
            "offset": offset,
            "length": contig.nbytes,
        })

    header = {
        "aoi": {
            "code": meta_row[0], "name": meta_row[1],
            "center_lon": meta_row[2], "center_lat": meta_row[3],
            "side_m": meta_row[4], "phenomenon": meta_row[5],
            "footprint_source": meta_row[6], "narrative": meta_row[7],
            "bbox": [meta_row[8], meta_row[9], meta_row[10], meta_row[11]],
            # ARCHITECTURE_THREE B4 — InSAR reference point. Frontend renders
            # this as the ⚓ pin on the map; tooltip = reference_note.
            "reference": {
                "lon":  meta_row[12],
                "lat":  meta_row[13],
                "note": meta_row[14],
            },
        },
        "n_buildings":   n_buildings,
        "n_months":      n_months,
        # Per-AOI defensibility gate: σ ≤ sigma_max is the trustworthy band the
        # ConfidencePill tints green; r2_min is the linear-fit floor (here for
        # parity / future use). inf σ_max (empty AOI) serialises as null.
        "sigma_max":     None if not np.isfinite(sigma_max_aoi) else float(sigma_max_aoi),
        "r2_min":        float(r2_min_aoi),
        "n_coh_epochs":  n_coh_epochs,  # B2 — coh_series array reshapes to (n_buildings, n_coh_epochs)
        "dates":         dates_iso,
        "soil_classes":  soil_classes,   # variable-length strings stay in JSON header
        # ARCHITECTURE_THREE C1/C3 — fixed-grid block descriptor. The block
        # aggregate arrays (block_count, block_worst_velocity, …) are dense over
        # [0, n_blocks); the frontend reconstructs each block's polygon from this
        # grid: block id b → ix=b%nx, iy=b//nx → cell [minlon+ix*dlon, minlat+iy*dlat].
        "block_grid": {
            "nx":           grid_meta["nx"],
            "ny":           grid_meta["ny"],
            "n_blocks":     n_blocks,
            "minlon":       grid_meta["minlon"],
            "minlat":       grid_meta["minlat"],
            "cell_lon_deg": grid_meta["cell_lon_deg"],
            "cell_lat_deg": grid_meta["cell_lat_deg"],
        },
        # ARCHITECTURE_THREE — 'synthetic' (plausible seed) or 'insar' (real
        # MintPy join). Drives the sidebar disclaimer copy. See scripts/provenance.py.
        "data_provenance": get_provenance(meta_row[0]),
        "arrays":        arrays_meta,
    }
    header_bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")
    # Pad the header so the body starts at an 8-byte boundary inside the
    # response buffer. The frontend adds `4 + headerLen` to each in-header
    # `offset`, so this also has to be aligned for the per-section offsets
    # to land on aligned addresses. JSON ignores trailing whitespace.
    HEADER_ALIGN = 8
    pad = (-(4 + len(header_bytes))) % HEADER_ALIGN
    if pad:
        header_bytes = header_bytes + b" " * pad
    out = bytearray()
    out += struct.pack("<I", len(header_bytes))
    out += header_bytes
    out += body
    return bytes(out)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Fail-closed boot guard: in production the data API MUST be authenticated. The auth
    # dependency is intentionally inert when no public key is configured (so it can deploy
    # ahead of key provisioning) — but that same inertness would silently serve the entire
    # risk dataset to the world if the key env were ever forgotten in prod. Refuse to start
    # instead. Dev is unaffected (auth stays optional there).
    if config.is_production() and not config.auth_enabled():
        raise RuntimeError(
            "INSAR_ENV=production but no telemetry public key is configured "
            "(set INSAR_JWT_PUBLIC_KEY or INSAR_JWT_PUBLIC_KEY_PATH). Refusing to start "
            "with the data API unauthenticated."
        )
    if not DB_PATH.exists():
        raise RuntimeError(
            f"DuckDB file not found at {DB_PATH}. "
            "Run: cd backend && python -m scripts.seed_synthetic"
        )
    con = duckdb.connect(str(DB_PATH), read_only=True)
    con.execute("LOAD spatial;")
    db.con = con
    db.aoi_codes = {row[0] for row in con.execute("SELECT aoi_code FROM aoi_registry").fetchall()}
    # Precompute every AOI bundle at startup so /aoi/{code}/bundle is a memcpy.
    # Also hash each one once to use as a strong ETag. Pairs with the
    # `Cache-Control: no-cache` we return: the browser must revalidate but
    # on a clean hit it serves from its own cache (cheap 304).
    for code in sorted(db.aoi_codes):
        payload = _build_bundle(con, code)
        db.bundles[code] = payload
        db.bundle_etags[code] = '"' + hashlib.blake2b(payload, digest_size=16).hexdigest() + '"'
        print(f"[startup] bundle '{code}': {len(payload) / 1024:.1f} KiB  etag={db.bundle_etags[code]}")
    try:
        yield
    finally:
        con.close()
        db.con = None
        db.aoi_codes = set()
        db.bundles.clear()
        db.bundle_etags.clear()


app = FastAPI(title="infra-proptech MVP", version="0.2.0", lifespan=lifespan)
# Origin-locked CORS. In prod set INSAR_ALLOWED_ORIGINS to the real InSAR frontend origin(s)
# (see app/config.py). The data fetch is a cross-origin GET carrying an Authorization header,
# which is a non-simple request — the browser preflights OPTIONS, and Starlette answers it for
# any allow-listed origin. No cookies are used (the token is a Bearer header), so
# allow_credentials stays False and Authorization is listed explicitly.
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.allowed_origins(),
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "If-None-Match"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Add HSTS in production. Only meaningful behind TLS (the header tells browsers to
    pin HTTPS for max-age), so it's prod-gated — in dev over http it would be ignored
    anyway, and asserting it there would just be noise. Cheap: one header per response."""
    response = await call_next(request)
    if config.is_production():
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


def _require_aoi(aoi: str) -> None:
    if aoi not in db.aoi_codes:
        raise HTTPException(404, f"unknown aoi '{aoi}'. known: {sorted(db.aoi_codes)}")


# ---------- response models ----------

class AOIReference(BaseModel):
    lon: float
    lat: float
    note: str


class AOI(BaseModel):
    aoi_code: str
    name: str
    center_lon: float
    center_lat: float
    side_m: float
    phenomenon: str
    footprint_source: str
    narrative: str
    bbox: list[float] = Field(..., description="[minlon, minlat, maxlon, maxlat]")
    # ARCHITECTURE_THREE B4 — InSAR reference point exposed for the ⚓ pin
    reference: AOIReference


class Building(BaseModel):
    building_id: int
    aoi_code: str
    geometry: dict
    height_m: float | None
    soil_class: str | None
    riparian_dist_m: float | None
    shoreline_dist_m: float | None
    reclaimed_land: bool | None
    latest_velocity_mm_yr: float | None
    latest_displacement_mm: float | None
    latest_coherence: float | None
    latest_composite_risk: float | None


class BuildingAtDate(BaseModel):
    building_id: int
    aoi_code: str
    geometry: dict
    height_m: float | None
    soil_class: str | None
    displacement_mm: float | None
    velocity_mm_yr: float | None
    coherence: float | None


class TimeSeriesPoint(BaseModel):
    date: str
    displacement_mm: float
    velocity_mm_yr: float | None
    coherence: float | None


class BuildingTimeSeries(BaseModel):
    building_id: int
    aoi_code: str
    soil_class: str | None
    riparian_dist_m: float | None
    shoreline_dist_m: float | None
    series: list[TimeSeriesPoint]


# ---------- endpoints ----------

@app.get("/health")
async def health() -> dict:
    return {"ok": True, "aois": sorted(db.aoi_codes)}


@app.get("/aoi/{code}/bundle", dependencies=[Depends(rate_limit)])
async def aoi_bundle(code: str, request: Request) -> Response:
    """Binary bundle: header(JSON) + packed typed-array payload. Served from RAM.

    Caching: strong ETag derived from the payload bytes. `Cache-Control: no-cache`
    forces the browser to revalidate every load, but a matching `If-None-Match`
    short-circuits to a 5-byte 304. This avoids the trap of `immutable`, which
    would let a stale (pre-alignment-fix) bundle live in the browser cache
    forever and silently break parseBundle.
    """
    payload = db.bundles.get(code)
    if payload is None:
        raise HTTPException(404, f"unknown aoi '{code}'")
    etag = db.bundle_etags[code]
    if request.headers.get("if-none-match") == etag:
        # 304 revalidation: NOT metered. A legit frontend revalidates on every AOI re-open,
        # so a cached hit is the signature of normal use — only a fresh 200 (what a scraper
        # always gets) counts toward the §8 company-detection signal.
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})

    # Meter the full pull (only when a Weespas sink is wired). Runs AFTER the response is
    # sent (BackgroundTask), so the map never waits on it. This is what makes a direct
    # curl-the-bundle scraper visible to company-detection, not just frontend clicks.
    background = None
    if usage_metering_enabled():
        background = BackgroundTask(
            meter_bundle_fetch, request.headers.get("authorization"), code
        )
    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={
            "ETag": etag,
            "Cache-Control": "no-cache",
            "Content-Length": str(len(payload)),
        },
        background=background,
    )


@app.get("/aois", response_model=list[AOI], dependencies=[Depends(rate_limit)])
async def list_aois() -> list[AOI]:
    cur = db.cursor()
    rows = cur.execute(
        """
        SELECT aoi_code, name, center_lon, center_lat, side_m, phenomenon,
               footprint_source, narrative,
               bbox_minlon, bbox_minlat, bbox_maxlon, bbox_maxlat,
               reference_lon, reference_lat, reference_note
        FROM aoi_registry
        ORDER BY aoi_code
        """
    ).fetchall()
    return [
        AOI(
            aoi_code=r[0], name=r[1],
            center_lon=r[2], center_lat=r[3], side_m=r[4],
            phenomenon=r[5], footprint_source=r[6], narrative=r[7],
            bbox=[r[8], r[9], r[10], r[11]],
            reference=AOIReference(lon=r[12], lat=r[13], note=r[14]),
        )
        for r in rows
    ]


@app.get("/buildings", response_model=list[Building], dependencies=[Depends(rate_limit)])
async def buildings_in_bbox(
    aoi: str = Query(..., description="AOI code; see /aois"),
    minlon: float = Query(...),
    minlat: float = Query(...),
    maxlon: float = Query(...),
    maxlat: float = Query(...),
    limit: int = Query(5000, le=20000),
) -> list[Building]:
    _require_aoi(aoi)
    cur = db.cursor()
    bbox_wkt = (
        f"POLYGON(({minlon} {minlat}, {maxlon} {minlat}, "
        f"{maxlon} {maxlat}, {minlon} {maxlat}, {minlon} {minlat}))"
    )
    rows = cur.execute(
        """
        SELECT
            v.building_id, v.aoi_code,
            ST_AsGeoJSON(v.geom),
            v.height_m, v.soil_class,
            v.riparian_dist_m, v.shoreline_dist_m, v.reclaimed_land,
            v.latest_velocity_mm_yr, v.latest_displacement_mm,
            v.latest_coherence, v.latest_composite_risk
        FROM v_building_latest v
        WHERE v.aoi_code = ?
          AND ST_Intersects(v.geom, ST_GeomFromText(?))
        LIMIT ?
        """,
        [aoi, bbox_wkt, limit],
    ).fetchall()
    return [
        Building(
            building_id=r[0], aoi_code=r[1],
            geometry=json.loads(r[2]),
            height_m=r[3], soil_class=r[4],
            riparian_dist_m=r[5], shoreline_dist_m=r[6], reclaimed_land=r[7],
            latest_velocity_mm_yr=r[8], latest_displacement_mm=r[9],
            latest_coherence=r[10], latest_composite_risk=r[11],
        )
        for r in rows
    ]


@app.get("/buildings/at-date", response_model=list[BuildingAtDate], dependencies=[Depends(rate_limit)])
async def buildings_at_date(
    aoi: str = Query(...),
    minlon: float = Query(...),
    minlat: float = Query(...),
    maxlon: float = Query(...),
    maxlat: float = Query(...),
    obs_date: str = Query(..., description="YYYY-MM-DD; nearest <= will be used"),
    limit: int = Query(5000, le=20000),
) -> list[BuildingAtDate]:
    """Per-building displacement / velocity at the slider date. Drives the time animation."""
    _require_aoi(aoi)
    cur = db.cursor()
    bbox_wkt = (
        f"POLYGON(({minlon} {minlat}, {maxlon} {minlat}, "
        f"{maxlon} {maxlat}, {minlon} {maxlat}, {minlon} {minlat}))"
    )
    rows = cur.execute(
        """
        WITH at_date AS (
            SELECT s.building_id, s.aoi_code,
                   s.displacement_mm, s.velocity_mm_yr, s.coherence,
                   ROW_NUMBER() OVER (PARTITION BY s.aoi_code, s.building_id
                                      ORDER BY s.observation_date DESC) AS rn
            FROM subsidence_time_series s
            WHERE s.aoi_code = ?
              AND s.observation_date <= CAST(? AS DATE)
        )
        SELECT b.building_id, b.aoi_code, ST_AsGeoJSON(b.geom),
               b.height_m, b.soil_class,
               a.displacement_mm, a.velocity_mm_yr, a.coherence
        FROM buildings b
        JOIN at_date a
          ON a.building_id = b.building_id
         AND a.aoi_code   = b.aoi_code
         AND a.rn         = 1
        WHERE b.aoi_code = ?
          AND ST_Intersects(b.geom, ST_GeomFromText(?))
        LIMIT ?
        """,
        [aoi, obs_date, aoi, bbox_wkt, limit],
    ).fetchall()
    return [
        BuildingAtDate(
            building_id=r[0], aoi_code=r[1],
            geometry=json.loads(r[2]),
            height_m=r[3], soil_class=r[4],
            displacement_mm=r[5], velocity_mm_yr=r[6], coherence=r[7],
        )
        for r in rows
    ]


@app.get("/buildings/{building_id}/timeseries", response_model=BuildingTimeSeries,
         dependencies=[Depends(rate_limit)])
async def building_timeseries(building_id: int, aoi: str = Query(...)) -> BuildingTimeSeries:
    _require_aoi(aoi)
    cur = db.cursor()
    meta = cur.execute(
        """
        SELECT soil_class, riparian_dist_m, shoreline_dist_m
        FROM buildings WHERE building_id = ? AND aoi_code = ?
        """,
        [building_id, aoi],
    ).fetchone()
    if meta is None:
        raise HTTPException(404, "building not found")
    rows = cur.execute(
        """
        SELECT observation_date, displacement_mm, velocity_mm_yr, coherence
        FROM subsidence_time_series
        WHERE building_id = ? AND aoi_code = ?
        ORDER BY observation_date
        """,
        [building_id, aoi],
    ).fetchall()
    return BuildingTimeSeries(
        building_id=building_id, aoi_code=aoi,
        soil_class=meta[0], riparian_dist_m=meta[1], shoreline_dist_m=meta[2],
        series=[
            TimeSeriesPoint(
                date=str(r[0]), displacement_mm=r[1],
                velocity_mm_yr=r[2], coherence=r[3],
            )
            for r in rows
        ],
    )


@app.get("/risk-summary", dependencies=[Depends(rate_limit)])
async def risk_summary(aoi: str = Query(...)) -> dict:
    _require_aoi(aoi)
    cur = db.cursor()
    row = cur.execute(
        """
        SELECT
            COUNT(*),
            AVG(latest_velocity_mm_yr),
            QUANTILE_CONT(latest_velocity_mm_yr, 0.05),
            SUM(CASE WHEN latest_velocity_mm_yr < -10 THEN 1 ELSE 0 END),
            AVG(latest_composite_risk),
            AVG(latest_coherence)
        FROM v_building_latest
        WHERE aoi_code = ?
        """,
        [aoi],
    ).fetchone()
    return {
        "aoi": aoi,
        "n_buildings": row[0],
        "avg_velocity_mm_yr": row[1],
        "p05_velocity_mm_yr": row[2],   # most-negative tail (worst subsiders)
        "n_severe_subsidence": row[3],
        "avg_composite_risk": row[4],
        "avg_coherence": row[5],
    }
