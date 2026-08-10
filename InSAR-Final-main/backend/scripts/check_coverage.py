"""
Coverage audit — is the InSAR pipeline holding ALL the data each AOI needs to run
at full effectiveness (ASC + DESC decomposition, with troposphere correction)?

Read-only. Prints, per AOI:
  - ASC / DESC raw HyP3 pair counts on disk (data/hyp3_work/<aoi>/)
  - whether a MintPy run dir exists for each track (data/mintpy/<aoi>_<DIR>_<path>/)
  - GACOS troposphere coverage: how many acquisition dates have a .ztd.tif grid
  - a per-AOI verdict: ✅ decomposition-ready (both tracks present + tropo covered)
    vs ⚠️ what's missing.

"Full effectiveness across the whole system" = every AOI shows ✅. Use this as
the baseline before an overnight download and as proof afterwards.

Run from backend/:  python -m scripts.check_coverage
Exit code is 0 when every AOI is ✅, else 1 (so it doubles as a CI/övernight gate).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from scripts.aois import REGISTRY, AOI

BACKEND_DIR = Path(__file__).resolve().parents[1]
WORK_DIR = BACKEND_DIR / "data" / "hyp3_work"
MINTPY_DIR = BACKEND_DIR / "data" / "mintpy"
GACOS_DIR = BACKEND_DIR / "data" / "raw" / "env" / "gacos"

# Job dirs look like "h-A57-240606240618": <letter>-<A|D><path>-<ref6><sec6>.
# Group 1 is the flight letter (A=ASCENDING, D=DESCENDING), group 2 the path.
_JOB_RE = re.compile(r"^[a-z]-([AD])(\d+)-(\d{6})(\d{6})$")
# A *canonical* MintPy run dir is "<aoi>_<ASCENDING|DESCENDING>_<path>" with no
# trailing _bak/_audit/_crashed suffix (those are excluded by join_insar too).
_MINTPY_CANON_RE = re.compile(r"^(?P<aoi>.+)_(ASCENDING|DESCENDING)_(?P<path>\d+)$")
_FLIGHT = {"A": "ASCENDING", "D": "DESCENDING"}


def _raw_tracks(aoi: AOI) -> dict[str, int]:
    """{'ASCENDING': n_pairs, 'DESCENDING': n_pairs} from raw HyP3 pair dirs."""
    counts = {"ASCENDING": 0, "DESCENDING": 0}
    aoi_dir = WORK_DIR / aoi.code
    if not aoi_dir.is_dir():
        return counts
    for entry in aoi_dir.iterdir():
        # is_dir() follows symlinks → cross-AOI-reused (linked) pairs count too.
        if not entry.is_dir():
            continue
        m = _JOB_RE.match(entry.name)
        if m:
            counts[_FLIGHT[m.group(1)]] += 1
    return counts


def _mintpy_runs(aoi: AOI) -> set[str]:
    """Set of flight directions with a CANONICAL MintPy run dir for this AOI."""
    out: set[str] = set()
    if not MINTPY_DIR.is_dir():
        return out
    for entry in MINTPY_DIR.iterdir():
        if not entry.is_dir():
            continue
        m = _MINTPY_CANON_RE.match(entry.name)
        if m and m.group("aoi") == aoi.code:
            out.add(entry.name.split("_")[-2])  # ASCENDING / DESCENDING
    return out


def _acq_dates(aoi: AOI) -> set[str]:
    """All Sentinel-1 acquisition YYYYMMDD codes across this AOI's raw pairs
    (both tracks). Empty set if nothing is downloaded yet."""
    codes: set[str] = set()
    aoi_dir = WORK_DIR / aoi.code
    if not aoi_dir.is_dir():
        return codes
    for entry in aoi_dir.iterdir():
        if not entry.is_dir():
            continue
        m = _JOB_RE.match(entry.name)
        if not m:
            continue
        # YYMMDD → YYYYMMDD (all data is 2024+, century is unambiguous here).
        for grp in (m.group(3), m.group(4)):
            codes.add("20" + grp)
    return codes


def _gacos_codes(aoi: AOI) -> set[str]:
    cache = GACOS_DIR / aoi.code
    if not cache.is_dir():
        return set()
    return {p.name.split(".", 1)[0] for p in cache.glob("*.ztd.tif")}


def audit_aoi(aoi: AOI) -> tuple[bool, list[str]]:
    """Return (ready, lines). `ready` is True iff the AOI has both tracks of raw
    data AND every acquisition date has a GACOS grid."""
    raw = _raw_tracks(aoi)
    runs = _mintpy_runs(aoi)
    acq = _acq_dates(aoi)
    gac = _gacos_codes(aoi)
    missing_gacos = sorted(acq - gac)

    lines = [f"  raw pairs:   ASC={raw['ASCENDING']:3d}   DESC={raw['DESCENDING']:3d}"]
    lines.append(f"  mintpy runs: {', '.join(sorted(runs)) or '(none)'}")
    if acq:
        covered = len(acq) - len(missing_gacos)
        lines.append(f"  gacos:       {covered}/{len(acq)} acquisition dates have a grid"
                     + (f"  (missing {len(missing_gacos)})" if missing_gacos else ""))
    else:
        lines.append("  gacos:       n/a (no raw data yet)")

    problems = []
    if raw["ASCENDING"] == 0:
        problems.append("no ASCENDING raw")
    if raw["DESCENDING"] == 0:
        problems.append("no DESCENDING raw (can't decompose east-west drift)")
    if missing_gacos:
        problems.append(f"{len(missing_gacos)} dates lack GACOS troposphere grids")

    ready = not problems
    verdict = "✅ decomposition-ready" if ready else "⚠️  " + "; ".join(problems)
    lines.append(f"  verdict:     {verdict}")
    return ready, lines


def main() -> None:
    print("InSAR coverage audit — target: every AOI ✅ (ASC+DESC raw + full GACOS)\n")
    all_ready = True
    for aoi in REGISTRY:
        ready, lines = audit_aoi(aoi)
        all_ready &= ready
        print(f"{aoi.code}")
        for ln in lines:
            print(ln)
        print()
    print("=" * 60)
    print("ALL AOIs decomposition-ready ✅" if all_ready
          else "Some AOIs are NOT ready ⚠️  — see above")
    sys.exit(0 if all_ready else 1)


if __name__ == "__main__":
    main()
