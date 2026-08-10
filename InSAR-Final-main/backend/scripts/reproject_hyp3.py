"""
Stage 2.5: reproject HyP3 GAMMA products from UTM to geographic (EPSG:4326).

Why this stage exists
---------------------
HyP3 InSAR_GAMMA delivers every raster projected to **UTM** (37S / EPSG:32737
for our Nairobi + Mombasa AOIs), in metres. MintPy loads that natively and most
steps are CRS-agnostic — but `correct_troposphere` with GACOS is NOT. MintPy's
`tropo_gacos.py` maps the timeseries pixel box to "geo" coords and then indexes
the GACOS zenith-delay grid (which is EPSG:4326, degrees). On a UTM stack the
first map returns metres and the second misreads them as degrees, producing an
out-of-range pixel box:

    ValueError: Input box (314788957, -11835408336, ...) is NOT within the data
    size range (0, 0, 2413, 2425)!

GACOS is the single largest correctable error source in tropical InSAR and is a
hard requirement for shipping honest data (the join's dry-run guard refuses a
small velocity grid unless tropo == gacos). So we reproject the stack to 4326
*before* MintPy loads it, into a parallel mirror tree, leaving the original UTM
downloads untouched.

This is a build-time stage. Run it once per AOI, then point `mintpy_run` at the
mirror via `HYP3_WORK_DIR`:

    python -m scripts.reproject_hyp3 --aoi huruma
    HYP3_WORK_DIR=data/hyp3_work_4326 python -m scripts.mintpy_run --aoi huruma

Reprojection runs through `conda run -n mintpy gdalwarp` so PROJ's data dir
resolves from the mintpy env (a bare gdalwarp on PATH fails to open proj.db).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from scripts.aois import by_code
from scripts.clip_to_common_grid import hyp3_meta_txt

BACKEND_DIR = Path(__file__).resolve().parents[1]
SRC_WORK_DIR = BACKEND_DIR / "data" / "hyp3_work"
DST_WORK_DIR = BACKEND_DIR / "data" / "hyp3_work_4326"

MINTPY_PREFIX = Path(os.environ.get("MINTPY_PREFIX", str(Path.home() / "miniforge3"))).resolve()
MINTPY_ENV = os.environ.get("MINTPY_ENV", "mintpy")

TARGET_EPSG = "EPSG:4326"
# ~0.0007° ≈ 78 m at the equator — matches the native 80 m HyP3 GAMMA look size.
# Going finer would oversample and imply precision the data doesn't carry.
TARGET_RES = "0.0007"

# The five raster types MintPy's load step needs, and the resampling kernel each
# wants. Phase/coherence/DEM/incidence are continuous → bilinear. The water mask
# is categorical (0/1) → nearest, so we never invent fractional land/water.
RESAMPLE = {
    "_unw_phase.tif": "bilinear",
    "_corr.tif": "bilinear",
    "_dem.tif": "bilinear",
    "_inc_map.tif": "bilinear",
    "_water_mask.tif": "near",
}


def _conda_gdalwarp(src: Path, dst: Path, resample: str) -> None:
    """Reproject one raster UTM → 4326 via the mintpy env's gdalwarp.

    `-srcnodata 0 -dstnodata 0`: HyP3's NO_DATA_VALUE is 0, and the products are
    0-padded outside the burst footprint. Without explicit nodata, bilinear
    blends that 0-fill border into real phase at the edges. The nodata flag keeps
    the fill out of the interpolation.
    """
    conda_bin = MINTPY_PREFIX / "bin" / "conda"
    if not conda_bin.exists():
        raise FileNotFoundError(
            f"conda not found at {conda_bin}. Run scripts/setup_mintpy_env.sh first."
        )
    cmd = [
        str(conda_bin), "run", "-n", MINTPY_ENV, "gdalwarp",
        "-overwrite", "-q",
        "-t_srs", TARGET_EPSG,
        "-tr", TARGET_RES, TARGET_RES,
        "-r", resample,
        "-srcnodata", "0", "-dstnodata", "0",
        str(src), str(dst),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"gdalwarp failed for {src.name} (rc={proc.returncode}):\n{proc.stderr}"
        )


def _pair_is_current(src_pair: Path, dst_pair: Path) -> bool:
    """True iff every needed output already exists and is newer than its source.

    Lets re-runs skip finished pairs (idempotent). A missing or stale output for
    any raster type, or a missing .txt sidecar, forces the pair to re-run.
    """
    txt = hyp3_meta_txt(src_pair)
    if txt is None or not (dst_pair / txt.name).exists():
        return False
    for suffix in RESAMPLE:
        src = next(src_pair.glob(f"*{suffix}"), None)
        if src is None:
            continue  # source lacks this type; nothing to (re)produce
        dst = dst_pair / src.name
        if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
            return False
    return True


def reproject_pair(src_pair: Path, dst_pair: Path) -> int:
    """Reproject all raster types in one pair dir; copy the .txt sidecar.

    Returns the number of rasters warped. `prep_hyp3` re-derives georeferencing
    from each GeoTIFF header (not from .rsc), so we deliberately do NOT copy the
    stale UTM .rsc — MintPy writes a fresh 4326 one at load time. We DO copy the
    {product}.txt metadata file: prep_hyp3 reads it for orbit/baseline and aborts
    the whole load if it's missing.
    """
    dst_pair.mkdir(parents=True, exist_ok=True)
    warped = 0
    for suffix, resample in RESAMPLE.items():
        src = next(src_pair.glob(f"*{suffix}"), None)
        if src is None:
            print(f"    ! {src_pair.name}: no {suffix} — skipping type", file=sys.stderr)
            continue
        _conda_gdalwarp(src, dst_pair / src.name, resample)
        warped += 1

    txt = hyp3_meta_txt(src_pair)
    if txt is None:
        raise FileNotFoundError(
            f"{src_pair.name}: missing *.txt metadata sidecar (prep_hyp3 requires it)"
        )
    shutil.copy2(txt, dst_pair / txt.name)
    return warped


def reproject_aoi(aoi_code: str, force: bool = False, src_root: Path | None = None) -> Path:
    """Reproject every pair for one AOI into the 4326 mirror tree.

    `src_root` overrides the default `data/hyp3_work` source — pass the Stage A
    clipped tree (`data/hyp3_work_clipped`) so the pipeline reprojects the
    common-grid pairs rather than the raw full frames.

    O(#pairs × #raster-types) gdalwarp calls; each warp is GDAL-vectorized over
    pixels (no per-pixel Python). Returns the mirror AOI dir.
    """
    src_aoi = (src_root or SRC_WORK_DIR) / aoi_code
    dst_aoi = DST_WORK_DIR / aoi_code
    if not src_aoi.is_dir():
        raise FileNotFoundError(f"no HyP3 products for AOI '{aoi_code}' at {src_aoi}")

    pairs = sorted(p for p in src_aoi.iterdir() if p.is_dir())
    if not pairs:
        raise FileNotFoundError(f"no pair dirs under {src_aoi}")

    print(f"  reprojecting {len(pairs)} pair(s) for '{aoi_code}' → {dst_aoi}")
    done = skipped = 0
    for src_pair in pairs:
        dst_pair = dst_aoi / src_pair.name
        if not force and _pair_is_current(src_pair, dst_pair):
            skipped += 1
            continue
        reproject_pair(src_pair, dst_pair)
        done += 1
    print(f"  ✓ reprojected {done}, skipped {skipped} up-to-date")
    return dst_aoi


def main() -> None:
    p = argparse.ArgumentParser(
        description="Reproject HyP3 GAMMA products UTM → EPSG:4326 for MintPy+GACOS"
    )
    p.add_argument("--aoi", required=True, help="AOI code, e.g. huruma")
    p.add_argument("--force", action="store_true",
                   help="re-warp even if outputs look up-to-date")
    p.add_argument("--src", default=None,
                   help="source work tree (default data/hyp3_work); pass "
                        "data/hyp3_work_clipped to reproject the Stage A clipped pairs")
    args = p.parse_args()

    by_code(args.aoi)  # validate AOI code early
    src_root = (BACKEND_DIR / args.src).resolve() if args.src else None
    dst = reproject_aoi(args.aoi, force=args.force, src_root=src_root)
    print(f"\n  mirror tree: {dst}")
    print(f"  next: HYP3_WORK_DIR={DST_WORK_DIR.relative_to(BACKEND_DIR)} "
          f"python -m scripts.mintpy_run --aoi {args.aoi} --track ASCENDING/57")


if __name__ == "__main__":
    main()
