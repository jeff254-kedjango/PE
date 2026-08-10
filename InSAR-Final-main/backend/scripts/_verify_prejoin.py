"""Phase-A pre-join gates: assert the MintPy run produced honest, joinable data.

Run from backend/:  python -m scripts._verify_prejoin --aoi huruma --track ASCENDING/57

Exits non-zero (and prints what failed) unless every gate passes:
  - troposphericDelay.method == gacos   → clears the join's dry-run guard + honesty
  - velocity grid is real                → not all-zero / not all-NaN
  - epochs >= 24                         → emit_parquet's hard floor
  - velocity distribution is sane        → single-to-low-double-digit mm/yr, subsidence negative

This is a gate, not a transform: it never writes. It exists so we never join a
fallback (height_correlation) or degenerate velocity field by accident.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import h5py
import numpy as np

from scripts.aois import by_code

BACKEND_DIR = Path(__file__).resolve().parents[1]
MINTPY_DIR = BACKEND_DIR / "data" / "mintpy"


def verify(run_dir: Path) -> list[str]:
    """Return a list of failure messages (empty ⇒ all gates pass)."""
    fails: list[str] = []
    vel_path = run_dir / "velocity.h5"
    ts_path = run_dir / "timeseries.h5"

    if not vel_path.exists():
        return [f"velocity.h5 missing in {run_dir} — MintPy did not finish"]

    with h5py.File(vel_path) as f:
        tropo = str(f.attrs.get("mintpy.troposphericDelay.method", "")).lower()
        epsg = int(f.attrs.get("EPSG", 4326))
        vel = np.asarray(f["velocity"], dtype=np.float32)

    if tropo != "gacos":
        fails.append(f"tropo method = '{tropo or 'unset'}' (expected 'gacos' — "
                     f"GACOS fell back; join guard will refuse this)")
    if epsg != 4326:
        fails.append(f"EPSG = {epsg} (expected 4326 — reprojection did not take)")

    finite = np.isfinite(vel)
    if not finite.any():
        fails.append("velocity is entirely NaN")
    elif np.allclose(vel[finite], 0.0):
        fails.append("velocity is entirely zero (degenerate inversion)")
    else:
        mm = 1e3 * vel[finite]
        med = float(np.median(mm))
        p5, p95 = (float(x) for x in np.percentile(mm, [5, 95]))
        print(f"  velocity {vel.shape}: median {med:+.2f} mm/yr, p5/p95 {p5:+.2f}/{p95:+.2f}")
        if not (abs(med) < 50 and abs(p5) < 200 and abs(p95) < 200):
            fails.append(f"velocity magnitudes implausible "
                         f"(median {med:+.2f}, p5/p95 {p5:+.2f}/{p95:+.2f} mm/yr)")

    if ts_path.exists():
        with h5py.File(ts_path) as f:
            n_epoch = int(f["date"].shape[0])
        print(f"  epochs: {n_epoch}")
        if n_epoch < 24:
            fails.append(f"only {n_epoch} epochs (emit_parquet requires >= 24)")
    else:
        fails.append("timeseries.h5 missing")

    return fails


def main() -> None:
    p = argparse.ArgumentParser(description="Phase-A pre-join verification gates")
    p.add_argument("--aoi", required=True)
    p.add_argument("--track", default="ASCENDING/57")
    args = p.parse_args()

    aoi = by_code(args.aoi)
    run_dir = MINTPY_DIR / f"{aoi.code}_{args.track.replace('/', '_')}"
    print(f"  checking {run_dir}")
    fails = verify(run_dir)
    if fails:
        print("\n  ✗ PRE-JOIN GATES FAILED:", file=sys.stderr)
        for m in fails:
            print(f"    - {m}", file=sys.stderr)
        sys.exit(1)
    print("\n  ✓ all pre-join gates passed — safe to join")


if __name__ == "__main__":
    main()
