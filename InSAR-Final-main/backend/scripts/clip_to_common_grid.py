"""
Stage A: clip all HyP3 pairs to one common AOI grid.

Why this stage exists
---------------------
HyP3 InSAR_GAMMA delivers each pair as a full Sentinel-1 frame in UTM (37S /
EPSG:32737 for Nairobi + Mombasa). Across the 58 Huruma pairs those frames span
**10 distinct grid sizes** — only 30 share the dominant 3675×2963. MintPy's
`load_data` requires byte-identical dimensions and **silently drops** any pair
that doesn't match, so a naive load keeps 30 of 58 and throws away nearly half
the temporal density without warning.

The fix is not to reproject (that's `reproject_hyp3.py`, the next stage) but to
**crop** every pair to one fixed extent: the AOI's processing box (`processing_bbox`,
wide enough to contain the reference anchor — see scripts/aois.py). We snap that
box to a whole-pixel UTM grid at the native ~80 m HyP3 look size and warp every
raster onto exactly that grid. After this stage all pairs are identical (W,H), so
MintPy keeps all 58.

Pipeline order (Stage A runs FIRST):

    python -m scripts.clip_to_common_grid --aoi huruma
    python -m scripts.reproject_hyp3 --aoi huruma --src data/hyp3_work_clipped
    HYP3_WORK_DIR=data/hyp3_work_4326 python -m scripts.mintpy_run --aoi huruma

Clipping runs through `conda run -n mintpy gdalwarp` so PROJ's data dir resolves
from the mintpy env (a bare gdalwarp on PATH fails to open proj.db) — same reason
as reproject_hyp3.

Complexity: O(#pairs × #raster-types) gdalwarp calls; each warp is GDAL-vectorized
over pixels (no per-pixel Python). The output grid is small (~126×125 for Huruma),
so this is far cheaper than the full-frame reproject that would otherwise run.
"""

from __future__ import annotations

import argparse
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

from scripts.aois import AOI, by_code, processing_bbox

BACKEND_DIR = Path(__file__).resolve().parents[1]
SRC_WORK_DIR = BACKEND_DIR / "data" / "hyp3_work"
DST_WORK_DIR = BACKEND_DIR / "data" / "hyp3_work_clipped"

MINTPY_PREFIX = Path(os.environ.get("MINTPY_PREFIX", str(Path.home() / "miniforge3"))).resolve()
MINTPY_ENV = os.environ.get("MINTPY_ENV", "mintpy")

# HyP3 GAMMA products for our AOIs are UTM 37S. We clip *within* that CRS (no
# reprojection here) so the warp is a pure crop+regrid, leaving the UTM→4326 step
# to reproject_hyp3. Both AOIs fall in 37S; if a future AOI needs a different zone
# this constant is the single place to revisit.
TARGET_EPSG = "EPSG:32737"
# Native HyP3 GAMMA look size. Going finer oversamples and implies precision the
# data doesn't carry; coarser throws away resolution we have.
TARGET_RES_M = 80.0

# Same five raster types and resampling kernels as reproject_hyp3: continuous
# fields → bilinear, the categorical 0/1 water mask → nearest (never invent
# fractional land/water).
RESAMPLE = {
    "_unw_phase.tif": "bilinear",
    "_corr.tif": "bilinear",
    "_dem.tif": "bilinear",
    "_inc_map.tif": "bilinear",
    "_water_mask.tif": "near",
}


def hyp3_meta_txt(pair_dir: Path) -> Path | None:
    """The real HyP3 product metadata sidecar in `pair_dir`, or None.

    A pair dir can contain TWO `*.txt` files: the product metadata
    `{product}.txt` (which `prep_hyp3` reads for orbit/baseline) AND a
    `{product}.README.md.txt` doc that some HyP3 deliveries include. A bare
    `glob("*.txt")` returns them in arbitrary order, so it would non-
    deterministically copy the README and leave MintPy unable to find the
    metadata (FileNotFoundError mid-load). Select the metadata explicitly by
    excluding the README variant. ~37% of the Kileleshwa pairs carry the README;
    South C carried none, which is why this only surfaced now.
    """
    return next(
        (p for p in sorted(pair_dir.glob("*.txt"))
         if not p.name.endswith(".README.md.txt")),
        None,
    )


def common_grid(aoi: AOI) -> tuple[float, float, float, float, int, int]:
    """The one UTM extent + pixel size every pair is clipped to.

    Transforms the AOI processing bbox (lon/lat) corners into TARGET_EPSG, snaps
    the extent outward to a whole-TARGET_RES_M grid, and derives integer (W, H).
    Deterministic from the AOI alone, so every pair lands on a byte-identical grid.

    Returns (minx, miny, maxx, maxy, W, H) in TARGET_EPSG metres.
    """
    from pyproj import Transformer

    minlon, minlat, maxlon, maxlat = processing_bbox(aoi)
    t = Transformer.from_crs("EPSG:4326", TARGET_EPSG, always_xy=True)
    xs: list[float] = []
    ys: list[float] = []
    for lon in (minlon, maxlon):
        for lat in (minlat, maxlat):
            x, y = t.transform(lon, lat)
            xs.append(x)
            ys.append(y)
    minx = math.floor(min(xs) / TARGET_RES_M) * TARGET_RES_M
    miny = math.floor(min(ys) / TARGET_RES_M) * TARGET_RES_M
    maxx = math.ceil(max(xs) / TARGET_RES_M) * TARGET_RES_M
    maxy = math.ceil(max(ys) / TARGET_RES_M) * TARGET_RES_M
    W = int(round((maxx - minx) / TARGET_RES_M))
    H = int(round((maxy - miny) / TARGET_RES_M))
    return minx, miny, maxx, maxy, W, H


def _conda_gdalwarp(
    src: Path, dst: Path, resample: str, grid: tuple[float, float, float, float, int, int]
) -> None:
    """Crop+regrid one raster onto the common grid via the mintpy env's gdalwarp.

    `-te <minx miny maxx maxy>` with `-ts W H` pins the *exact same* output grid
    for every pair regardless of its input frame, which is what makes MintPy keep
    all 58. `-srcnodata 0 -dstnodata 0`: HyP3's NO_DATA is 0 and products are
    0-padded outside the burst — without explicit nodata, bilinear blends that
    0-fill into real phase at the edges (same reasoning as reproject_hyp3).
    """
    minx, miny, maxx, maxy, W, H = grid
    conda_bin = MINTPY_PREFIX / "bin" / "conda"
    if not conda_bin.exists():
        raise FileNotFoundError(
            f"conda not found at {conda_bin}. Run scripts/setup_mintpy_env.sh first."
        )
    cmd = [
        str(conda_bin), "run", "-n", MINTPY_ENV, "gdalwarp",
        "-overwrite", "-q",
        "-t_srs", TARGET_EPSG,
        "-te", str(minx), str(miny), str(maxx), str(maxy),
        "-ts", str(W), str(H),
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
    """True iff every needed output exists and is newer than its source.

    Lets re-runs skip finished pairs (idempotent). A missing/stale output for any
    raster type, or a missing .txt sidecar, forces the pair to re-run. Mirrors
    reproject_hyp3._pair_is_current.
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


def clip_pair(
    src_pair: Path, dst_pair: Path, grid: tuple[float, float, float, float, int, int]
) -> int:
    """Clip all raster types in one pair dir onto the common grid; copy the .txt.

    Returns the number of rasters warped. As in reproject_hyp3 we copy the
    {product}.txt metadata sidecar (prep_hyp3 reads it for orbit/baseline and
    aborts the load if it's missing) but NOT the stale .rsc — georeferencing is
    re-derived from each clipped GeoTIFF header downstream.
    """
    dst_pair.mkdir(parents=True, exist_ok=True)
    warped = 0
    for suffix, resample in RESAMPLE.items():
        src = next(src_pair.glob(f"*{suffix}"), None)
        if src is None:
            print(f"    ! {src_pair.name}: no {suffix} — skipping type", file=sys.stderr)
            continue
        _conda_gdalwarp(src, dst_pair / src.name, resample, grid)
        warped += 1

    txt = hyp3_meta_txt(src_pair)
    if txt is None:
        raise FileNotFoundError(
            f"{src_pair.name}: missing *.txt metadata sidecar (prep_hyp3 requires it)"
        )
    shutil.copy2(txt, dst_pair / txt.name)
    return warped


def clip_aoi(aoi_code: str, force: bool = False) -> Path:
    """Clip every pair for one AOI onto its common grid. Returns the mirror dir."""
    aoi = by_code(aoi_code)
    src_aoi = SRC_WORK_DIR / aoi_code
    dst_aoi = DST_WORK_DIR / aoi_code
    if not src_aoi.is_dir():
        raise FileNotFoundError(f"no HyP3 products for AOI '{aoi_code}' at {src_aoi}")

    pairs = sorted(p for p in src_aoi.iterdir() if p.is_dir())
    if not pairs:
        raise FileNotFoundError(f"no pair dirs under {src_aoi}")

    grid = common_grid(aoi)
    minx, miny, maxx, maxy, W, H = grid
    print(f"  clipping {len(pairs)} pair(s) for '{aoi_code}' → {dst_aoi}")
    print(f"  common grid: {W}×{H} px @ {TARGET_RES_M:.0f} m in {TARGET_EPSG} "
          f"te=({minx:.0f} {miny:.0f} {maxx:.0f} {maxy:.0f})")
    done = skipped = 0
    for src_pair in pairs:
        dst_pair = dst_aoi / src_pair.name
        if not force and _pair_is_current(src_pair, dst_pair):
            skipped += 1
            continue
        clip_pair(src_pair, dst_pair, grid)
        done += 1
    print(f"  ✓ clipped {done}, skipped {skipped} up-to-date")
    return dst_aoi


def main() -> None:
    p = argparse.ArgumentParser(
        description="Clip HyP3 GAMMA products to one common AOI grid for MintPy"
    )
    p.add_argument("--aoi", required=True, help="AOI code, e.g. huruma")
    p.add_argument("--force", action="store_true",
                   help="re-warp even if outputs look up-to-date")
    args = p.parse_args()

    dst = clip_aoi(args.aoi, force=args.force)
    print(f"\n  clipped tree: {dst}")
    print(f"  next: python -m scripts.reproject_hyp3 --aoi {args.aoi} "
          f"--src {DST_WORK_DIR.relative_to(BACKEND_DIR)}")


if __name__ == "__main__":
    main()
