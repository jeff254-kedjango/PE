"""
Stage 3 driver: MintPy SBAS time-series inversion.

Takes the HyP3 GAMMA products downloaded in Stage 2 and runs MintPy's
`smallbaselineApp.py` to produce a coherence-weighted time-series of vertical
displacement per pixel.

Why this is its own module rather than living in hyp3_pipeline.py:
  - MintPy needs its own conda environment (heavy GDAL/ISCE2/h5py deps).
  - We invoke it as a subprocess from the standard backend venv. Crossing the
    process boundary keeps the dependency graphs separate.

Run from backend/ (regular venv):
    python -m scripts.mintpy_run --aoi huruma --track ASCENDING/57

The wrapper:
  1. Renders `mintpy_config.tmpl` → `data/mintpy/<aoi>-<track>/config.cfg`
  2. Calls the mintpy conda env's smallbaselineApp.py as a subprocess
  3. Streams stdout/stderr to a log file and the terminal
  4. Returns the path to the geocoded velocity + timeseries HDF5

The conda env name and prefix are configurable via env vars (MINTPY_ENV,
MINTPY_PREFIX). Default is the env created by `setup_mintpy_env.sh`.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from scripts.aois import AOI, by_code, processing_bbox

BACKEND_DIR = Path(__file__).resolve().parents[1]
# Where Stage 2 extracted the HyP3 products MintPy loads. Overridable via
# HYP3_WORK_DIR so we can point at the reprojected 4326 mirror tree
# (scripts/reproject_hyp3.py) without touching the originals — GACOS needs a
# geographic stack. Relative values resolve under BACKEND_DIR.
_work_env = os.environ.get("HYP3_WORK_DIR")
WORK_DIR = (
    (Path(_work_env) if Path(_work_env).is_absolute() else BACKEND_DIR / _work_env)
    if _work_env else BACKEND_DIR / "data" / "hyp3_work"
)
MINTPY_DIR = BACKEND_DIR / "data" / "mintpy"
GACOS_DIR = BACKEND_DIR / "data" / "raw" / "env" / "gacos"
TEMPLATE_PATH = Path(__file__).resolve().parent / "mintpy_config.tmpl"

MINTPY_PREFIX = Path(os.environ.get("MINTPY_PREFIX", str(Path.home() / "miniforge3"))).resolve()
MINTPY_ENV = os.environ.get("MINTPY_ENV", "mintpy")


@dataclass(frozen=True)
class MintpyOutputs:
    """Paths to the geocoded products MintPy emits. Stage 4 consumes these."""
    run_dir: Path
    velocity_h5: Path        # geo_velocity.h5 — per-pixel LOS velocity (m/yr → convert to mm/yr)
    timeseries_h5: Path      # geo_timeseries.h5 — per-pixel cumulative disp (m → mm)
    coherence_h5: Path       # geo_temporalCoherence.h5 — fit quality 0-1
    geometry_h5: Path        # geo_geometryRadar.h5 — lat/lon/inc/azi for each pixel


@dataclass(frozen=True)
class RenderResult:
    """What `render_config` chose, so `run_mintpy` can drive the reference-point
    self-heal loop without re-deriving the decision.

    `source` is how `reference_lalo` was picked:
      - "snapped" — a data-derived coherent+stable+clustered anchor (the goal;
        velocities are relative to a documented stable zero).
      - "fixed"   — the AOI's declared anchor (e.g. Karura); used until products
        exist to snap from. May land on a masked-out pixel on a cold AOI.
      - "auto"    — MintPy picks the max-coherence pixel itself (the fixed anchor
        was out-of-subset or masked-out).
    """
    cfg: Path
    reference_lalo: str
    source: str


# Upper bound on render→run passes in run_mintpy's self-heal loop. The loop also
# self-terminates structurally (the masked-anchor crash can recur at most once,
# and the snap-upgrade is attempted at most once), so this is a safety backstop,
# not the primary terminator. Worst real case is 3 passes (fixed→crash,
# auto→full, snapped→full); 4 leaves one slot of headroom.
MAX_REFERENCE_PASSES = 4


def _sar_dates(aoi: AOI) -> set[str]:
    """Distinct YYYYMMDD acquisition dates present in this AOI's HyP3 products.

    Each pair dir is named like `h-A57-240606240618` and emits
    `*_unw_phase.tif`; the two 6-digit halves are ref+sec acquisition dates.
    We read the filenames (O(#pairs), no raster opens) and expand to 8-digit
    dates. Used to diff against GACOS grids so we exclude only dates that truly
    lack a correction grid.
    """
    dates: set[str] = set()
    for tif in (WORK_DIR / aoi.code).glob("*/*_unw_phase.tif"):
        for token in re.findall(r"\d{8}", tif.name):
            dates.add(token)
    return dates


def _gacos_dates(aoi_gacos: Path) -> set[str]:
    """YYYYMMDD dates that have a GACOS `.ztd.tif` zenith-delay grid on disk."""
    return {
        m.group(0)
        for f in aoi_gacos.glob("*.ztd.tif")
        if (m := re.match(r"\d{8}", f.name))
    }


def _missing_gacos_dates(aoi: AOI, aoi_gacos: Path) -> list[str]:
    """SAR dates with no matching GACOS grid — must be excluded so the gacos
    troposphere step doesn't error on the gap. Empty list ⇒ render `auto`."""
    return sorted(_sar_dates(aoi) - _gacos_dates(aoi_gacos))


def _anchor_is_masked_out(run_dir: Path, lat: float, lon: float) -> bool:
    """True iff the fixed anchor pixel is masked OUT in this run's maskConnComp.h5.

    MintPy hard-errors ("reference point is in masked OUT area") when an explicit
    `reference.lalo` lands on a masked pixel, instead of falling back to auto. On
    the bootstrap pass the mask exists (load+coherence ran) but the snap has no
    velocity.h5 yet, so a masked fixed anchor would dead-end the run. Detecting it
    here lets the caller emit `auto` for that one pass; the snap then pins a stable
    anchor on re-run. Returns False (anchor kept) if the mask/h5py is unavailable
    or the pixel is out of bounds — never blocks a run that could otherwise pin.
    """
    mask_path = run_dir / "maskConnComp.h5"
    if not mask_path.exists():
        return False
    try:
        import h5py
        with h5py.File(mask_path, "r") as f:
            key = "mask" if "mask" in f else list(f.keys())[0]
            m = f[key][:]
            a = f.attrs
            y = round((lat - float(a["Y_FIRST"])) / float(a["Y_STEP"]))
            x = round((lon - float(a["X_FIRST"])) / float(a["X_STEP"]))
    except Exception:
        return False
    if not (0 <= y < m.shape[0] and 0 <= x < m.shape[1]):
        return False
    # Treat the anchor as masked-out if its OWN pixel OR any 8-neighbour is masked.
    # Rationale: MintPy and this code can resolve a lat/lon to pixels that differ
    # by one (different geotransform rounding); on huruma DESC ours mapped to a
    # valid pixel (48,8) while MintPy used the masked (47,8) right beside it, so a
    # single-pixel test passed but MintPy still hard-errored. Near a ragged mask
    # edge a 1-pixel-off anchor is not a trustworthy zero anyway — requiring the
    # whole 3×3 block to be valid makes the verdict robust to that off-by-one.
    y0, y1 = max(0, y - 1), min(m.shape[0], y + 2)
    x0, x1 = max(0, x - 1), min(m.shape[1], x + 2)
    return not bool(m[y0:y1, x0:x1].all())


def _reference_lalo(aoi: AOI, minlon: float, minlat: float,
                    maxlon: float, maxlat: float,
                    run_dir: Path | None = None) -> str:
    """Reference-point value for the template.

    Emits the explicit `lat,lon` anchor only when it lies INSIDE the subset
    bbox (so MintPy can actually pin to it); otherwise `auto` — an out-of-subset
    anchor would make MintPy warn and silently fall back to auto anyway, so we
    pick auto explicitly rather than ship a contradictory line. Phase B widens
    the subset to contain the anchor, at which point this returns the explicit
    coordinate with no other change.

    A second `auto` case: the anchor is inside the subset but falls on a pixel
    masked OUT by `run_dir`'s maskConnComp.h5. MintPy errors hard on that rather
    than auto-recovering, so we pre-empt it — the snap pins a real anchor next pass.
    """
    inside = (minlat <= aoi.reference_lat <= maxlat
              and minlon <= aoi.reference_lon <= maxlon)
    if inside and not (run_dir is not None
                       and _anchor_is_masked_out(run_dir, aoi.reference_lat, aoi.reference_lon)):
        return f"{aoi.reference_lat:.5f},{aoi.reference_lon:.5f}"
    return "auto"


def _snap_reference_lalo(
    run_dir: Path,
    *,
    coh_floor: float = 0.7,
    bulk_tol_m_yr: float = 0.002,        # 2 mm/yr — half-width of the "stable bulk"
    min_tight_frac: float = 0.5,         # ≥half the cluster must lie within bulk_tol of its median
) -> str | None:
    """Snap the reference point to the median-velocity pixel of the coherent
    stable cluster, reading `avgSpatialCoh.h5` + `velocity.h5` from a
    MintPy run dir. Returns ``"lat,lon"`` or ``None``.

    Two-pass by design: on the FIRST run these products don't exist yet, so this
    returns ``None`` and the caller falls back to the AOI's fixed anchor. On a
    RE-RUN (smallbaselineApp is idempotent) the products are present and we pin a
    data-derived reference.

    We reference to the MEDIAN velocity of the coherent, contiguous-stable cluster
    — NOT to the pixel of smallest absolute velocity. The distinction is critical:
    on the bootstrap pass the field is referenced to an arbitrary pixel, so
    "absolute velocity" is meaningless and the min-|v| rule picks an OUTLIER (the
    pixel most negative relative to that arbitrary zero). Re-anchoring there stamps
    a tile-wide offset onto every other pixel — e.g. South C's 2 km tile read a
    uniform +19 mm/yr of false threat because the snap pinned its single lowest
    pixel. The cluster median is reference-invariant (a constant LOS offset from
    residual atmosphere/unwrap bias shifts all pixels equally, so it cancels), so
    anchoring to the pixel nearest the median yields a zero-centred field where
    only genuine differential movers stand out. We require the pixel to sit inside
    a contiguous coherent patch (4-neighbour erosion) so the anchor is not a lone
    lucky pixel, and require the cluster to be tight (`bulk_tol`) so a median over a
    truly noisy tile isn't trusted as a stable zero.

    The chosen pixel comes from `np.where` on the raster's own arrays, so its
    lat/lon (from the raster geotransform) is in-grid by construction — we do NOT
    re-test it against the nominal AOI box. A clipped grid is snapped OUTWARD to
    whole pixels by gdalwarp, so its edge pixels sit a fraction of a pixel beyond
    the nominal box; testing against that box spuriously rejected legitimate
    edge-of-tile anchors on small AOIs (e.g. South C's 26×25 grid).
    """
    import numpy as np  # local: keeps the module importable without numpy present
    try:
        import h5py
    except Exception:
        return None

    coh_path = run_dir / "avgSpatialCoh.h5"
    vel_path = run_dir / "velocity.h5"
    if not (coh_path.exists() and vel_path.exists()):
        return None
    try:
        with h5py.File(coh_path, "r") as f:
            key = "coherence" if "coherence" in f else list(f.keys())[0]
            coh = f[key][:]
        with h5py.File(vel_path, "r") as f:
            vel = f["velocity"][:]
            a = f.attrs
            y0 = float(a["Y_FIRST"]); x0 = float(a["X_FIRST"])
            dy = float(a["Y_STEP"]); dx = float(a["X_STEP"])
    except Exception:
        return None
    if coh.shape != vel.shape:
        return None

    # Coherent + contiguous: keep only coherent pixels whose 4-neighbours are also
    # coherent, so the anchor sits inside a contiguous patch (not a lone pixel).
    # Note: NO absolute-velocity gate here — velocity is relative to an arbitrary
    # bootstrap reference, so |v| is not yet meaningful (see docstring).
    coherent = (coh >= coh_floor) & np.isfinite(vel) & np.isfinite(coh)
    core = coherent.copy()
    core[1:, :]  &= coherent[:-1, :]
    core[:-1, :] &= coherent[1:, :]
    core[:, 1:]  &= coherent[:, :-1]
    core[:, :-1] &= coherent[:, 1:]
    if not core.any():
        return None

    # Reference to the cluster MEDIAN (reference-invariant): the median is the
    # stable-bulk LOS level; a tile-wide offset shifts it without changing which
    # pixel is nearest it. Refuse if the bulk isn't actually tight — a median over
    # a noisy cluster is not a trustworthy zero.
    vel_core = vel[core]
    v_med = float(np.median(vel_core))
    tight_frac = float(np.mean(np.abs(vel_core - v_med) <= bulk_tol_m_yr))
    if tight_frac < min_tight_frac:
        return None

    # Pick the clustered pixel nearest the median velocity; tie-break max coherence.
    ys, xs = np.where(core)
    ry, rx = min(zip(ys, xs),
                 key=lambda yx: (abs(float(vel[yx]) - v_med), -float(coh[yx])))
    ry, rx = int(ry), int(rx)
    lat = y0 + ry * dy
    lon = x0 + rx * dx
    return f"{lat:.5f},{lon:.5f}"


def render_config(aoi: AOI, track: str, dest: Path, *, multilook: int = 1) -> RenderResult:
    """Render `mintpy_config.tmpl` into the run directory with substitutions.

    The template intentionally uses {{double-brace}} tokens — Python str.format
    is too eager (would choke on legitimate `{` in MintPy syntax).

    `multilook` is the N×N load-time downsample factor (1 = full-res). It maps to
    mintpy.multilook.ystep/xstep in the template.

    Returns a `RenderResult` carrying the written config path plus which reference
    point was chosen and how (`source`), so the caller can drive the self-heal
    loop in `run_mintpy`.
    """
    if multilook < 1:
        raise ValueError(f"multilook must be >= 1, got {multilook}")
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"missing template: {TEMPLATE_PATH}")
    # Use the PROCESSING box, not the display tile: it's wide enough to contain
    # the reference anchor (so reference.lalo resolves to the real coordinate
    # rather than `auto`) and it matches the Stage A clip extent the rasters were
    # cropped to. The 2 km display tile (bbox) stays for the UI/bundle only.
    minlon, minlat, maxlon, maxlat = processing_bbox(aoi)

    # Stage 2 extracts each HyP3 product into WORK_DIR/<aoi>/<job_name>/.
    # Job names are prefixed with the track letter+path (e.g. "h-A57-…"). An AOI
    # can hold multiple tracks at once (kileleshwa/kilimani carry both A57 and
    # D79); MintPy refuses to mix geometries, so the load globs below are scoped
    # to one track via {{track_glob}}, derived from `track` further down.
    text = TEMPLATE_PATH.read_text()
    # ARCHITECTURE_THREE A1+A3 — GACOS dir + explicit reference plumbed in.
    # Ensure the GACOS dir exists so MintPy's gacos step can list it without
    # error even when no grids are present yet (the template's fallback
    # comment covers the empty-dir case).
    aoi_gacos = GACOS_DIR / aoi.code
    aoi_gacos.mkdir(parents=True, exist_ok=True)

    # GACOS dates with no grid → excludeDate; "auto" when every date has one.
    missing = _missing_gacos_dates(aoi, aoi_gacos)
    exclude_dates = ",".join(missing) if missing else "auto"
    if missing:
        print(f"  excluding {len(missing)} date(s) with no GACOS grid: "
              f"{','.join(missing)}")

    # Prefer a data-derived stable anchor snapped from this run's own products
    # (coherent AND low-velocity AND clustered). On the first pass the products
    # don't exist yet → None → fall back to the AOI's fixed anchor; a re-run then
    # pins the snapped reference (smallbaselineApp re-fit is idempotent).
    snapped = _snap_reference_lalo(dest)
    if snapped is not None:
        reference_lalo = snapped
        source = "snapped"
        print(f"  reference.lalo = {reference_lalo}  (snapped: coherent+stable+clustered)")
    else:
        reference_lalo = _reference_lalo(aoi, minlon, minlat, maxlon, maxlat, run_dir=dest)
        source = "auto" if reference_lalo == "auto" else "fixed"
        label = "auto" if source == "auto" else "fixed anchor"
        print(f"  reference.lalo = {reference_lalo}  ({label}; no snapped anchor yet)")

    # Restrict the MintPy load to this track's pair dirs. Job names embed the
    # track as "-<A|D><path>-" (e.g. l-A57-…, l-D79-…); the per-AOI letter prefix
    # varies for cross-AOI-reused pairs, so we match on the track segment only.
    # "ASCENDING/57" → "*-A57-*". Mixed ASC+DESC in one AOI dir (kileleshwa,
    # kilimani) would otherwise make MintPy refuse to invert.
    flight, _, path = track.partition("/")
    track_glob = f"*-{flight[:1].upper()}{path}-*"

    subs = {
        "work": str(WORK_DIR),
        "aoi": aoi.code,
        "track_glob": track_glob,
        "minlat": f"{minlat:.5f}",
        "maxlat": f"{maxlat:.5f}",
        "minlon": f"{minlon:.5f}",
        "maxlon": f"{maxlon:.5f}",
        "reference_lalo": reference_lalo,
        "exclude_dates": exclude_dates,
        "gacos_dir": str(aoi_gacos),
        "multilook": str(multilook),
    }
    for k, v in subs.items():
        text = text.replace("{{" + k + "}}", v)

    dest.mkdir(parents=True, exist_ok=True)
    cfg = dest / "config.cfg"
    cfg.write_text(text)
    return RenderResult(cfg=cfg, reference_lalo=reference_lalo, source=source)


def _conda_run_cmd(args: list[str]) -> list[str]:
    """Wrap a command to run inside the mintpy conda env without sourcing
    activate scripts (which require a login shell)."""
    conda_bin = MINTPY_PREFIX / "bin" / "conda"
    if not conda_bin.exists():
        raise FileNotFoundError(
            f"conda not found at {conda_bin}. Run scripts/setup_mintpy_env.sh first."
        )
    return [str(conda_bin), "run", "-n", MINTPY_ENV, "--no-capture-output", *args]


def _run_smallbaseline(cfg: Path, run_dir: Path, log_path: Path) -> int:
    """Invoke MintPy's smallbaselineApp.py once, teeing output to terminal + a
    per-pass log. Returns the subprocess exit code (does not raise on nonzero —
    the caller's self-heal loop decides whether a nonzero code is the recoverable
    masked-anchor crash or a real failure)."""
    cmd = _conda_run_cmd(["smallbaselineApp.py", str(cfg), "--dir", str(run_dir)])
    with open(log_path, "w") as logf:
        proc = subprocess.Popen(
            cmd, cwd=run_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            logf.write(line)
        return proc.wait()


def run_mintpy(
    aoi: AOI, track: str = "ASCENDING/57", *, multilook: int = 1, run_suffix: str = ""
) -> MintpyOutputs:
    """Run smallbaselineApp end-to-end for one (aoi, track).

    Idempotent: MintPy re-uses cached intermediate HDF5s if the config and
    inputs haven't changed. Safe to interrupt and re-run.

    Self-healing reference point: MintPy hard-errors if the fixed anchor lands on
    a masked-out pixel, but the mask that decides this is only created *inside*
    the subprocess (the reference_point step), so `render_config` can't see it on
    a cold AOI. We therefore drive a bounded render→run loop:

      pass 1  fixed anchor → if it's masked-out, MintPy crashes AFTER writing
              maskConnComp.h5;
      pass 2  re-render now sees the mask → emits `auto` → full run produces
              velocity.h5 + avgSpatialCoh.h5;
      pass 3  re-render snaps a coherent+stable+clustered anchor from those
              products → final run pins velocities to a documented stable zero.

    The masked-anchor crash is identified structurally (explicit lalo written AND
    the anchor is masked-out in the now-existing mask), so any *other* nonzero
    exit re-raises immediately and never loops. The snap-upgrade is attempted at
    most once; if no stable cluster exists we accept `auto` with a warning rather
    than loop. Bounded by MAX_REFERENCE_PASSES as a backstop.

    `multilook` (N) downsamples the stack N×N at load time for fast iteration.
    `run_suffix`, when set, isolates this run into a sibling dir
    `<aoi>_<track>_<suffix>` so a trial run never overwrites the canonical
    products (the live join/provenance key off the un-suffixed dir).
    """
    track_safe = track.replace("/", "_")
    base = f"{aoi.code}_{track_safe}"
    run_dir = MINTPY_DIR / (f"{base}_{run_suffix}" if run_suffix else base)
    run_dir.mkdir(parents=True, exist_ok=True)
    if multilook > 1:
        print(f"  multilook: {multilook}×{multilook} downsample")

    canonical_log = run_dir / "smallbaselineApp.log"
    tried_mask_retry = tried_snap_upgrade = False
    for attempt in range(1, MAX_REFERENCE_PASSES + 1):
        r = render_config(aoi, track, run_dir, multilook=multilook)
        print(f"  config: {r.cfg}  (pass {attempt}, reference source: {r.source})")
        pass_log = run_dir / f"smallbaselineApp.pass{attempt}.log"
        print(f"  running smallbaselineApp.py in conda env '{MINTPY_ENV}' (log: {pass_log})")
        rc = _run_smallbaseline(r.cfg, run_dir, pass_log)
        # Keep the canonical log pointing at the latest pass for downstream tools.
        canonical_log.write_bytes(pass_log.read_bytes())

        if rc == 0:
            if r.source == "snapped":
                break                                   # defensible stable anchor pinned — done
            have_products = ((run_dir / "velocity.h5").exists()
                             and (run_dir / "avgSpatialCoh.h5").exists())
            if not tried_snap_upgrade and have_products:
                tried_snap_upgrade = True               # one more pass: next render snaps
                print("  ↻ products exist — re-running to pin a snapped stable anchor")
                continue
            if r.source != "snapped":
                print(f"  ⚠ accepting reference source '{r.source}' — no snapped stable "
                      f"anchor available (velocities are differential, not anchored)")
            break

        # Nonzero exit: retry ONLY the masked-anchor crash. Identify it two ways,
        # either sufficient — we only acted on an explicit lalo, so a false retry
        # can't mask an unrelated bug (it just re-runs once as auto):
        #   (a) MintPy's own error string in the pass log — authoritative, and
        #       independent of reproducing MintPy's exact pixel arithmetic (the
        #       huruma DESC failure mode: our guard mapped the anchor to a valid
        #       neighbour pixel and said "fine", but MintPy rejected it anyway);
        #   (b) our structural check on the now-existing mask, as a fallback.
        wrote_explicit_anchor = "," in r.reference_lalo
        try:
            mintpy_said_masked = (
                "masked OUT area" in pass_log.read_text(errors="ignore")
            )
        except OSError:
            mintpy_said_masked = False
        masked_anchor_crash = wrote_explicit_anchor and (
            mintpy_said_masked
            or _anchor_is_masked_out(run_dir, aoi.reference_lat, aoi.reference_lon)
        )
        if masked_anchor_crash and not tried_mask_retry:
            tried_mask_retry = True
            print("  ↻ fixed anchor was masked-out (mask now exists) — re-rendering as auto")
            continue
        raise RuntimeError(f"smallbaselineApp.py exited {rc}; see {pass_log}")
    else:
        raise RuntimeError(
            f"reference selection did not converge in {MAX_REFERENCE_PASSES} passes; "
            f"see {canonical_log}"
        )

    # MintPy writes geocoded outputs to two different places depending on input
    # coordinate system: a `geo/` subdir when geocoding from radar coords (e.g.
    # ISCE2 inputs), or flat in the run dir when inputs were already in GEO
    # (HyP3 GAMMA — what we use). Probe both, prefer whichever exists.
    geo_subdir = run_dir / "geo"
    if (geo_subdir / "geo_velocity.h5").exists():
        out = MintpyOutputs(
            run_dir=run_dir,
            velocity_h5=geo_subdir / "geo_velocity.h5",
            timeseries_h5=geo_subdir / "geo_timeseries.h5",
            coherence_h5=geo_subdir / "geo_temporalCoherence.h5",
            geometry_h5=geo_subdir / "geo_geometryRadar.h5",
        )
    else:
        out = MintpyOutputs(
            run_dir=run_dir,
            velocity_h5=run_dir / "velocity.h5",
            timeseries_h5=run_dir / "timeseries.h5",
            coherence_h5=run_dir / "temporalCoherence.h5",
            geometry_h5=run_dir / "inputs" / "geometryGeo.h5",
        )
    missing = [p for p in (out.velocity_h5, out.timeseries_h5) if not p.exists()]
    if missing:
        raise RuntimeError(f"MintPy finished but expected outputs missing: {missing}")
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="Run MintPy SBAS for one AOI/track")
    p.add_argument("--aoi", required=True, help="AOI code, e.g. huruma")
    p.add_argument("--track", default="ASCENDING/57",
                   help='"<flight>/<path>", e.g. "ASCENDING/57"')
    p.add_argument("--multilook", type=int, default=1,
                   help="N×N load-time downsample for fast iteration; "
                        "default 1 = full-res")
    p.add_argument("--run-suffix", default="",
                   help="append to the run-dir name to isolate a trial run "
                        "(e.g. 'clipped'); default writes the canonical dir")
    args = p.parse_args()

    aoi = by_code(args.aoi)
    out = run_mintpy(aoi, args.track, multilook=args.multilook,
                     run_suffix=args.run_suffix)
    print(f"\n  ✓ velocity:   {out.velocity_h5}")
    print(f"  ✓ timeseries: {out.timeseries_h5}")
    print(f"  ✓ coherence:  {out.coherence_h5}")
    print(f"  ✓ geometry:   {out.geometry_h5}")


if __name__ == "__main__":
    main()
