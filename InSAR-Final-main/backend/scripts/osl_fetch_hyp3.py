#!/usr/bin/env python3
"""
osl_fetch_hyp3.py — re-download the AOI's HyP3 InSAR products *on OpenSARLab*.

Why this exists
---------------
The HyP3 GAMMA interferograms for both AOIs total ~16.5 GB on the laptop. Dragging
that into a JupyterLab browser upload is slow and flaky. But HyP3 products live in
ASF's cloud and are keyed by deterministic job names, so the same Earthdata account
that submitted them can pull them straight down onto OpenSARLab — same datacentre,
fast, no laptop egress. This is the recommended transport for section 2 of
docs/opensarlab_runbook.md.

This script is intentionally self-contained (only stdlib + hyp3_sdk) so it can be
copied to the lab and run with nothing else from the repo present.

Usage (on OpenSARLab, in a MintPy/InSAR profile terminal)
---------------------------------------------------------
    pip install --quiet hyp3_sdk          # usually already present
    export EARTHDATA_USER=<your_login>
    export EARTHDATA_PASS=<your_password> # or let hyp3_sdk prompt interactively
    python osl_fetch_hyp3.py --aoi huruma  --dest ~/work/huruma/hyp3_work/huruma
    python osl_fetch_hyp3.py --aoi mombasa --dest ~/work/mombasa/hyp3_work/mombasa

The job-name prefix per AOI matches scripts/hyp3_pipeline.py::_job_name:
    huruma  → "h-A57-"      mombasa → "m-A159-"
so we select exactly the jobs that built the laptop's products — no guessing.

Idempotent: a product dir that already has *_unw_phase.tif + *_corr.tif is skipped.
Downloads run on a small thread pool; products are 50-290 MB pre-signed S3 objects.
"""
from __future__ import annotations

import argparse
import os
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# AOI → (job-name prefix). Mirrors hyp3_pipeline._AOI_LETTER + path numbers so the
# selection here is identical to what the laptop pipeline submitted.
AOI_JOB_PREFIX = {
    "huruma": "h-A57-",
    "mombasa": "m-A159-",
}


def _connect():
    from hyp3_sdk import HyP3

    user = os.environ.get("EARTHDATA_USER", "").strip()
    pw = os.environ.get("NAS_PASS", "").strip()
    if user and pw:
        return HyP3(username=user, password=pw)
    # No env creds → hyp3_sdk falls back to ~/.netrc or an interactive prompt,
    # which is the normal OpenSARLab experience.
    print("  EARTHDATA_USER/PASS not set — relying on .netrc / interactive prompt")
    return HyP3()


def _already_extracted(dest: Path, name: str) -> bool:
    d = dest / name
    if not d.is_dir():
        return False
    return any(d.glob("*_unw_phase.tif")) and any(d.glob("*_corr.tif"))


def _unzip(zip_path: Path, into: Path) -> None:
    """Extract a HyP3 product zip, flattening its single top-level dir."""
    into.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        members = zf.namelist()
        top = os.path.commonpath(members).rstrip("/") + "/" if members else ""
        for m in members:
            if m.endswith("/"):
                continue
            rel = m[len(top):] if top and m.startswith(top) else m
            out = into / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(m) as src, open(out, "wb") as dst:
                while True:
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    dst.write(chunk)


def fetch(aoi: str, dest: Path, workers: int) -> None:
    prefix = AOI_JOB_PREFIX.get(aoi)
    if prefix is None:
        sys.exit(f"unknown aoi {aoi!r}; known: {sorted(AOI_JOB_PREFIX)}")

    hyp3 = _connect()
    print(f"  querying HyP3 for INSAR_GAMMA jobs…")
    # find_jobs returns every InSAR job on the account; filter to this AOI by the
    # deterministic name prefix. succeeded() guards against partials/failures.
    all_jobs = hyp3.find_jobs(job_type="INSAR_GAMMA")
    jobs = [j for j in all_jobs if (j.name or "").startswith(prefix) and j.succeeded()]
    if not jobs:
        sys.exit(
            f"  no succeeded jobs with prefix {prefix!r} found on this account.\n"
            f"  Are you signed in with the SAME Earthdata login that ran the pipeline?"
        )
    dest.mkdir(parents=True, exist_ok=True)

    todo = [j for j in jobs if not _already_extracted(dest, j.name)]
    skip = len(jobs) - len(todo)
    print(f"  {len(jobs)} {aoi} products in cloud — downloading {len(todo)} (skipping {skip} already on disk)")

    def _one(job):
        files = job.download_files(dest, create=True)
        for f in files:
            _unzip(Path(f), dest / job.name)
            Path(f).unlink(missing_ok=True)
        return job.name

    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_one, j): j.name for j in todo}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                fut.result()
                print(f"    ✓ {name}")
            except Exception as e:  # one bad product shouldn't sink the batch
                failures.append(name)
                print(f"    ✗ {name}: {e}", file=sys.stderr)

    if failures:
        sys.exit(f"  {len(failures)} downloads failed: {failures}. Re-run to retry (idempotent).")
    print(f"  ✓ {aoi}: all products present under {dest}")


def main() -> None:
    p = argparse.ArgumentParser(description="Re-download HyP3 products on OpenSARLab")
    p.add_argument("--aoi", required=True, choices=sorted(AOI_JOB_PREFIX))
    p.add_argument("--dest", required=True, type=Path,
                   help="product root, e.g. ~/work/huruma/hyp3_work/huruma")
    p.add_argument("--workers", type=int, default=6)
    args = p.parse_args()
    fetch(args.aoi, args.dest.expanduser(), args.workers)


if __name__ == "__main__":
    main()


