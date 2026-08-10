"""
Stage-4 dry run: fabricate a MintPy-shaped output directory and exercise
`join_insar.run_join` end-to-end against the real OSM footprints we already
have on disk. Catches schema/path/dtype bugs while the HyP3 watcher is still
downloading the real GAMMA products.

Throwaway script — not part of the production pipeline. Delete after Stage 4
is validated against real data.

Run from backend/:
    python -m scripts._dryrun_stage4 --aoi huruma
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import h5py
import numpy as np

from scripts.aois import by_code, bbox
from scripts.join_insar import MINTPY_DIR, PARQUET_ROOT, run_join

# Match the planned MintPy run: ~80 m pixels, 24 months at ~12 d cadence.
PIXEL_M = 80.0
N_DATES = 60


def fabricate(aoi_code: str, track: str = "ASCENDING/57") -> Path:
    """Write a MintPy-shaped run dir into data/mintpy/<aoi>_<track>/geo/.

    Coherence is high near the AOI centre and falls toward the edges — exercises
    the COH_MIN filter. Velocity ramps spatially in mm/yr so we can sanity-check
    sign conventions in the parquet output.
    """
    aoi = by_code(aoi_code)
    minlon, minlat, maxlon, maxlat = bbox(aoi)

    # Build the grid. Step in degrees ≈ 80 m / 111_320 (lon scaled by cos(lat)).
    import math
    dlat = PIXEL_M / 111_320.0
    dlon = PIXEL_M / (111_320.0 * math.cos(math.radians(aoi.center_lat)))
    W = int((maxlon - minlon) / dlon)
    H = int((maxlat - minlat) / dlat)
    print(f"  grid {H}x{W} over [{minlon:.4f},{minlat:.4f},{maxlon:.4f},{maxlat:.4f}]")

    # Coherence: peak 0.85 at centre, falls to ~0.2 at corners. Realistic for
    # urban Huruma (corrugated-iron roofs are noisy).
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    cy, cx = H / 2.0, W / 2.0
    r = np.sqrt(((yy - cy) / cy) ** 2 + ((xx - cx) / cx) ** 2)
    coh = (0.85 - 0.65 * r).clip(0.05, 1.0).astype(np.float32)

    # Velocity (LOS m/yr). Sign convention: MintPy emits LOS positive *toward*
    # satellite, so a subsiding pixel is negative. Make a SE→NW gradient from
    # +0.003 to -0.008 m/yr LOS so the join's sign flip produces a recognizable
    # subsidence pattern (positive mm/yr after conversion).
    vel = (0.003 - 0.011 * (xx / W + yy / H) / 2.0).astype(np.float32)

    # Timeseries: cumulative LOS displacement (metres). Linearly grow with t,
    # scaled by per-pixel velocity. T x H x W. Use float32 to keep it small.
    t_years = np.linspace(0.0, 2.0, N_DATES, dtype=np.float32)
    ts = (vel[None, :, :] * t_years[:, None, None]).astype(np.float32)

    # Incidence angle: ~40° across the scene with a small east-west ramp.
    inc = (40.0 + 2.0 * (xx / W - 0.5)).astype(np.float32)

    # Dates: monthly from 2024-06 forward.
    dates_iso = []
    y, m = 2024, 6
    for _ in range(N_DATES):
        dates_iso.append(f"{y}{m:02d}15")
        m += 1
        if m > 12:
            m -= 12
            y += 1
    date_arr = np.array(dates_iso, dtype="S8")

    # MintPy geocoded attrs. Y_FIRST = north edge with negative step.
    attrs = {
        "X_FIRST": minlon,
        "Y_FIRST": maxlat,
        "X_STEP": dlon,
        "Y_STEP": -dlat,
        "WIDTH": W,
        "LENGTH": H,
    }

    track_safe = track.replace("/", "_")
    run_dir = MINTPY_DIR / f"{aoi.code}_{track_safe}"
    geo = run_dir / "geo"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    geo.mkdir(parents=True)

    with h5py.File(geo / "geo_timeseries.h5", "w") as f:
        f.create_dataset("timeseries", data=ts)
        f.create_dataset("date", data=date_arr)
        for k, v in attrs.items():
            f.attrs[k] = v

    with h5py.File(geo / "geo_velocity.h5", "w") as f:
        f.create_dataset("velocity", data=vel)
        for k, v in attrs.items():
            f.attrs[k] = v

    with h5py.File(geo / "geo_temporalCoherence.h5", "w") as f:
        f.create_dataset("temporalCoherence", data=coh)
        for k, v in attrs.items():
            f.attrs[k] = v

    with h5py.File(geo / "geo_geometryRadar.h5", "w") as f:
        f.create_dataset("incidenceAngle", data=inc)
        for k, v in attrs.items():
            f.attrs[k] = v

    print(f"  ✓ fabricated {run_dir}")
    return run_dir


def main() -> None:
    p = argparse.ArgumentParser(description="Fabricate fake MintPy output and run Stage 4 join")
    p.add_argument("--aoi", default="huruma")
    p.add_argument("--track", default="ASCENDING/57")
    p.add_argument("--keep", action="store_true",
                   help="don't delete the fake run dir on success")
    args = p.parse_args()

    aoi = by_code(args.aoi)
    fabricate(args.aoi, args.track)
    print(f"\n  running join_insar.run_join({aoi.code!r}, {args.track!r})…")
    run_join(aoi, args.track)

    # Spot-check the parquet output.
    import pyarrow.parquet as pq
    b = pq.read_table(PARQUET_ROOT / "buildings" / f"aoi={aoi.code}" / "part-0.parquet")
    s = pq.read_table(PARQUET_ROOT / "subsidence" / f"aoi={aoi.code}" / "part-0.parquet")
    print(f"\n  buildings:  {b.num_rows} rows × {len(b.column_names)} cols")
    print(f"    velocity_mm_yr: min={min(b['velocity_sigma_mm_yr'].drop_null().to_pylist() or [None]):.3f}")
    vels = [v for v in b["coherence_mean"].to_pylist() if v is not None and v == v]
    print(f"    coherence_mean live: {len(vels)}/{b.num_rows}, mean={sum(vels)/max(len(vels),1):.3f}")
    print(f"  subsidence: {s.num_rows} rows (expected {b.num_rows} × dates)")

    if not args.keep:
        # Clean up the fake parquet so it doesn't poison the real run later.
        shutil.rmtree(PARQUET_ROOT / "buildings" / f"aoi={aoi.code}", ignore_errors=True)
        shutil.rmtree(PARQUET_ROOT / "subsidence" / f"aoi={aoi.code}", ignore_errors=True)
        shutil.rmtree(MINTPY_DIR / f"{aoi.code}_{args.track.replace('/', '_')}", ignore_errors=True)
        print("  cleaned up fake outputs")


if __name__ == "__main__":
    main()
