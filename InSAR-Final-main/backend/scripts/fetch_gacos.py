"""
GACOS tropospheric-delay fetcher (ARCHITECTURE_THREE A1).

Pulls per-acquisition Zenith Total Delay grids from the GACOS portal for every
Sentinel-1 acquisition date in our HyP3 working set, caches them under
`data/raw/env/gacos/<aoi>/`, and lets MintPy's gacos step subtract them at
inversion time.

Why this matters: in tropical Nairobi/Mombasa, atmospheric water-vapour delay
is the *dominant* source of velocity error after coherence — typically several
mm/yr of bias if uncorrected. GACOS reduces that by ~50–70 %, which is the
single biggest credibility lever in Phase A.

Portal mechanics:
  1. POST a form to gacos.net with: bbox, date list, email, mode=time-series.
  2. Portal queues the job, emails a download URL when ready (typically
     minutes to hours, depending on queue depth).
  3. Job result is a `.tar.gz` (or `.zip`) of `YYYYMMDD.ztd.tif` GeoTIFF grids
     (we request the GeoTIFF output format — MintPy 1.6 reads it natively via
     GDAL, no binary-grid endianness handling) plus a `_preview.jpg` per date.
     We unpack only the `*.ztd.tif` grids into the AOI cache dir under the
     names MintPy expects (YYYYMMDD.ztd.tif).

This script does not handle the email step automatically — that requires SMTP
plumbing nobody wants. Instead it supports these modes:
  - `submit`: post the request, print the portal job ID, exit.
  - `ingest`: take one manually-downloaded archive (.tar.gz or .zip) and
    unpack its `*.ztd.tif` grids into the AOI cache dir.
  - `ingest-dir`: ingest every archive in a directory, auto-routing each one
    to the correct AOI by the GeoTIFF's geographic centre (no --aoi needed).
  - `status`: coverage report against the HyP3 acquisition dates.

All modes are idempotent. `ingest`/`ingest-dir` skip files already present.

Performance:
  - All file I/O is streamed (no buffer-in-memory of the zip).
  - Date extraction is a single regex pass over HyP3 job-dir names.
  - No per-date Python loop overhead; bulk operations everywhere.

Usage:
    # 1. Submit a GACOS job for an AOI (one HTTPS POST):
    python -m scripts.fetch_gacos submit --aoi huruma --email me@example.com

    # 2. After portal emails you a download URL, ingest the zip:
    python -m scripts.fetch_gacos ingest --aoi huruma --zip ~/Downloads/gacos_xxx.zip
"""

from __future__ import annotations

import argparse
import re
import sys
import tarfile
import zipfile
from datetime import date
from pathlib import Path

import requests

from scripts.aois import AOI, REGISTRY, by_code, processing_bbox


BACKEND_DIR = Path(__file__).resolve().parents[1]
WORK_DIR = BACKEND_DIR / "data" / "hyp3_work"
GACOS_CACHE_DIR = BACKEND_DIR / "data" / "raw" / "env" / "gacos"

# HyP3 job dir names: `h-A57-240606240618` (Huruma), `m-A159-240601240613`
# (Mombasa) — first char is the AOI prefix from hyp3_pipeline.py. Capture both
# ref and sec dates so we cover the full acquisition list.
_JOB_NAME_RE = re.compile(r"^[a-z]-[AD]\d+-(\d{6})(\d{6})$")

# GACOS portal endpoint. The web form is the only documented entry point;
# there is no rate-limited public API. We post the same fields the form posts.
GACOS_PORTAL_URL = "http://www.gacos.net/M/action_page.php"

# Portal hard-limit: a submission with more than this many dates is silently
# rejected — the server returns its landing page (a chunk of googletagmanager
# JS) instead of queueing a job, and no email is ever sent. We therefore chunk
# the date list into batches of at most this size, one POST per batch.
GACOS_MAX_DATES_PER_JOB = 20


def _yymmdd_to_date(s: str) -> date:
    """`240606` → date(2024, 6, 6). Two-digit year assumes 20xx (S1 launched 2014)."""
    return date(2000 + int(s[:2]), int(s[2:4]), int(s[4:6]))


def discover_acquisition_dates(aoi: AOI) -> list[date]:
    """Walk WORK_DIR/<aoi>/ and pull every Sentinel-1 acquisition date from
    HyP3 job names. Returns a deduped, sorted list."""
    aoi_dir = WORK_DIR / aoi.code
    if not aoi_dir.is_dir():
        raise FileNotFoundError(f"no HyP3 work dir for {aoi.code}: {aoi_dir}")

    dates: set[date] = set()
    for entry in aoi_dir.iterdir():
        if not entry.is_dir():
            continue
        m = _JOB_NAME_RE.match(entry.name)
        if not m:
            continue
        dates.add(_yymmdd_to_date(m.group(1)))
        dates.add(_yymmdd_to_date(m.group(2)))
    if not dates:
        raise RuntimeError(
            f"no HyP3 job dirs matched expected pattern under {aoi_dir} — "
            f"is the Stage-2 download complete?"
        )
    return sorted(dates)


def _aoi_cache(aoi: AOI) -> Path:
    d = GACOS_CACHE_DIR / aoi.code
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cached_date_codes(cache: Path) -> set[str]:
    """`YYYYMMDD` codes already present in the cache dir.

    GACOS GeoTIFFs land as `YYYYMMDD.ztd.tif`; `Path.stem` would leave the
    `.ztd`, so we split on the first dot to recover the bare date code.
    """
    return {p.name.split(".", 1)[0] for p in cache.glob("*.ztd.tif")}


def _missing_dates(aoi: AOI, dates: list[date]) -> list[date]:
    """Subset of `dates` for which we don't already have a `.ztd.tif` cached."""
    have = _cached_date_codes(_aoi_cache(aoi))
    return [d for d in dates if d.strftime("%Y%m%d") not in have]


# Pad the union box so the GACOS grid strictly CONTAINS the MintPy load extent.
# MintPy's tropo_gacos samples the grid over the stack's pixel box and hard-errors
# ("Input box … NOT within the data size range") if the grid stops even one pixel
# short — which is exactly what bit huruma DESC on 2026-06-12: the grid was
# requested over the display box but MintPy loads the (much wider) processing box.
# 0.02° ≈ 2.2 km, comfortably more than one GACOS/ERA5 cell, costs nothing.
_GACOS_BBOX_BUFFER_DEG = 0.02


def _combined_bbox(aois: list[AOI]) -> tuple[float, float, float, float]:
    """Smallest (minlon, minlat, maxlon, maxlat) box enclosing every AOI's
    PROCESSING box, plus a buffer.

    GACOS's ERA5 grid is ~11 km, so a handful of adjacent Nairobi AOIs (a few km
    apart) fit comfortably in one box — one job covers them all. We request the
    union extent and later fan the resulting grids into each AOI's cache.

    IMPORTANT: use `processing_bbox`, NOT the display `bbox`. MintPy clips/loads
    each stack to the processing box (wide enough to hold the reference anchor),
    and the GACOS troposphere step samples the grid over THAT extent. Requesting
    the narrower display box leaves the grid short on AOIs whose processing box is
    larger (e.g. huruma's 10 km box vs its 2 km tile), crashing the tropo step.
    """
    boxes = [processing_bbox(a) for a in aois]
    b = _GACOS_BBOX_BUFFER_DEG
    return (
        min(x[0] for x in boxes) - b,
        min(x[1] for x in boxes) - b,
        max(x[2] for x in boxes) + b,
        max(x[3] for x in boxes) + b,
    )


def _looks_like_bounce(body: str) -> bool:
    """Heuristic: did the portal HARD-reject the submission?

    History: we once keyed on the `googletagmanager` marker as a "rejected"
    tell. That marker is present on the SUCCESS response too, so it produced
    confident false alarms — on 2026-06-12 it flagged a batch "REJECTED, no
    email will arrive" when the emails in fact arrived and ingested fine. The
    portal documents no success schema and returns a 200 landing-ish page in
    both cases, so acceptance is NOT programmatically confirmable here.

    We therefore only report a bounce on signals that genuinely mean failure:
    an explicit error/invalid marker in the body. Absence of those is reported
    as "unconfirmed", never as "rejected" (see submit()). The reliable guard
    against the real documented rejection cause (>20 dates) is the ≤20 chunking
    in submit(), not this function.
    """
    b = body.lower()
    return any(m in b for m in ("invalid date", "error", "exceed", "too many", "not valid"))


def submit(aois: list[AOI], email: str) -> None:
    """POST one time-series job per ≤20-date batch over the AOIs' combined bbox.

    Accepts one *or more* AOIs. Their acquisition dates are unioned and the
    GACOS request box is the union of their bboxes, so nearby AOIs (e.g. the
    inland Nairobi cluster) are fetched in a single job. The portal caps a
    submission at GACOS_MAX_DATES_PER_JOB dates, so the date list is chunked and
    posted one batch at a time. The portal emails a download URL per batch when
    each job completes (queue depth varies — minutes to hours).

    Ingest is then per-AOI (`ingest --aoi <code> --archive <zip>`), which keeps
    every grid regardless of location — so one combined archive feeds all AOIs.
    """
    # Union the missing dates across AOIs: a date cached for one AOI but not
    # another still needs fetching, and the combined box covers them all.
    label = "+".join(a.code for a in aois)
    all_dates: set[date] = set()
    missing_set: set[date] = set()
    for aoi in aois:
        dates = discover_acquisition_dates(aoi)
        all_dates.update(dates)
        missing_set.update(_missing_dates(aoi, dates))
    if not missing_set:
        print(f"  ✓ all {len(all_dates)} dates already cached for {label}; nothing to submit")
        return
    missing = sorted(missing_set)

    minlon, minlat, maxlon, maxlat = _combined_bbox(aois)
    box = {
        "seq": "OSM Map",                 # hidden field the form posts; absent → portal bounces
        "N": f"{maxlat:.4f}",
        "S": f"{minlat:.4f}",
        "W": f"{minlon:.4f}",
        "E": f"{maxlon:.4f}",
        "H": "0",                         # acquisition hour (UTC), integer 0–23. The form has
        "M": "0",                         # SEPARATE hour (H) and minute (M) selects — an earlier
                                          # combined "00:00" in H was rejected. GACOS rounds to the
                                          # daily ERA5 grid, so 00:00 is fine for S1 IW (~05:30 UTC).
        "type": "2",                      # "2" = time-series mode (not single-epoch)
        "email": email,
    }

    n_batches = (len(missing) + GACOS_MAX_DATES_PER_JOB - 1) // GACOS_MAX_DATES_PER_JOB
    print(f"  → submitting {len(missing)} dates for {label} in {n_batches} batch(es) "
          f"(≤{GACOS_MAX_DATES_PER_JOB}/batch); bbox "
          f"W={minlon:.4f} S={minlat:.4f} E={maxlon:.4f} N={maxlat:.4f}")

    bounced = 0
    for i in range(n_batches):
        chunk = missing[i * GACOS_MAX_DATES_PER_JOB:(i + 1) * GACOS_MAX_DATES_PER_JOB]
        # Portal expects newline-separated `YYYYMMDD` strings (no hyphens) —
        # matches the eventual `YYYYMMDD.ztd` GACOS output naming. Earlier code
        # sent `YYYY-MM-DD`; the portal rejected those as "wrong date format".
        payload = {**box, "date": "\n".join(d.strftime("%Y%m%d") for d in chunk)}
        print(f"  → batch {i + 1}/{n_batches} ({len(chunk)} dates)")
        try:
            r = requests.post(GACOS_PORTAL_URL, data=payload, timeout=60)
            r.raise_for_status()
        except Exception as e:
            print(f"  ❌ batch {i + 1}/{n_batches} submission failed: {e}", file=sys.stderr)
            raise

        body = r.text or ""
        snippet = body.replace("\n", " ")[:200]
        if _looks_like_bounce(body):
            bounced += 1
            print(f"  ❌ batch {i + 1}/{n_batches} REJECTED — the response contains an "
                  f"explicit error marker: {snippet}", file=sys.stderr)
        else:
            # The portal gives no machine-readable ack, so a clean 200 means
            # "submitted, unconfirmed" — NOT a guaranteed accept. Don't overstate it.
            print(f"  → batch {i + 1}/{n_batches} submitted (HTTP {r.status_code}; portal "
                  f"sends no ack — confirm via the email): {snippet}")

    if bounced:
        print(f"\n  ❌ {bounced}/{n_batches} batch(es) returned an explicit error — those "
              f"definitely did NOT queue. Check the bbox/date format and re-run them.",
              file=sys.stderr)
    print(f"\n  next: wait for the GACOS email(s) (the only reliable confirmation), then "
          f"ingest the archive into EACH AOI:")
    for aoi in aois:
        print(f"    python -m scripts.fetch_gacos ingest --aoi {aoi.code} --archive <downloaded.zip>")


def _iter_archive_members(archive: Path):
    """Yield (member_name, size, open_stream_callable) for a .tar.gz or .zip.

    Abstracts over tarfile/zipfile so the extraction loop is identical for
    both. `open_stream_callable()` returns a binary file-like for streaming.
    """
    name = archive.name.lower()
    if name.endswith((".tar.gz", ".tgz", ".tar")):
        tf = tarfile.open(archive, "r:*")
        try:
            for m in tf.getmembers():
                if not m.isfile():
                    continue
                yield m.name, m.size, (lambda mm=m: tf.extractfile(mm))
        finally:
            # Caller fully consumes the generator before we close.
            tf.close()
    elif name.endswith(".zip"):
        zf = zipfile.ZipFile(archive)
        try:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                yield info.filename, info.file_size, (lambda i=info: zf.open(i))
        finally:
            zf.close()
    else:
        raise ValueError(f"unsupported archive type: {archive} (expected .tar.gz/.tgz/.zip)")


def _grid_covers_centroid(bounds, aoi: AOI) -> bool:
    """True iff the GACOS grid's geographic extent contains the AOI centroid.

    GACOS time-series jobs are submitted over a *combined* box spanning several
    nearby AOIs (see `submit`/`_combined_bbox`), so one archive's grids
    legitimately blanket every AOI in that cluster — yet a grid from a different
    region (e.g. a Mombasa tile mixed into a Nairobi submission) never reaches a
    Nairobi centroid. "Extent contains the centroid" is therefore the exact,
    region-safe membership test: it fans one combined archive into all its AOIs
    while structurally blocking cross-region leakage. `bounds` is a rasterio
    BoundingBox (left/bottom/right/top in WGS84).
    """
    return (bounds.left <= aoi.center_lon <= bounds.right
            and bounds.bottom <= aoi.center_lat <= bounds.top)


def _extract_ztd_tif(archive: Path, aoi: AOI) -> tuple[int, int, int]:
    """Stream every `*.ztd.tif` whose extent covers `aoi`'s centroid into the AOI
    cache. Returns (new, skipped, dropped).

    Only the GeoTIFF delay grids are kept — `_preview.jpg` thumbnails and any
    other members are ignored. Each grid is geo-checked against the AOI centroid
    (`_grid_covers_centroid`), so a wrong-region tile bundled into the archive is
    dropped rather than cached — this is the guard that keeps a Mombasa grid out
    of a Nairobi cache even on a manual single-AOI `ingest`. Idempotent: skips a
    file already present with the same byte size. Grids are tiny (~23×23 px), so
    we read each fully into memory to test its extent and write it.
    """
    from rasterio.io import MemoryFile  # importing rasterio.io registers GDAL drivers

    cache = _aoi_cache(aoi)
    n_new = n_skipped = n_dropped = 0
    for member_name, _size, opener in _iter_archive_members(archive):
        leaf = Path(member_name).name
        if not leaf.endswith(".ztd.tif"):
            continue
        src = opener()
        if src is None:  # tar can return None for odd member types
            continue
        with src:
            data = src.read()
        with MemoryFile(data) as mf, mf.open() as ds:
            if not _grid_covers_centroid(ds.bounds, aoi):
                n_dropped += 1
                continue
        out = cache / leaf
        if out.exists() and out.stat().st_size == len(data):
            n_skipped += 1
            continue
        out.write_bytes(data)
        n_new += 1
    return n_new, n_skipped, n_dropped


def _archive_covered_aois(archive: Path) -> list[AOI]:
    """AOIs whose centroid the archive's grids cover — the caches a combined-box
    submission fans into. All grids in one submission share a box, so the first
    grid's extent determines the whole archive's destinations: a Nairobi archive
    returns the Nairobi cluster, a Mombasa archive returns only Mombasa, and an
    archive over no registered AOI returns []. (Per-grid filtering still happens
    in `_extract_ztd_tif`, so a hypothetical mixed archive is handled correctly.)
    """
    from rasterio.io import MemoryFile  # importing rasterio.io registers GDAL drivers

    for member_name, _size, opener in _iter_archive_members(archive):
        if not Path(member_name).name.endswith(".ztd.tif"):
            continue
        src = opener()
        if src is None:
            continue
        with src:
            data = src.read()
        with MemoryFile(data) as mf, mf.open() as ds:
            bounds = ds.bounds
        return [a for a in REGISTRY if _grid_covers_centroid(bounds, a)]
    return []


def _report_coverage(aoi: AOI, cache: Path) -> None:
    """Spot-check cached coverage against the HyP3 acquisition list."""
    expected = {d.strftime("%Y%m%d") for d in discover_acquisition_dates(aoi)}
    have = _cached_date_codes(cache)
    missing = sorted(expected - have)
    if missing:
        print(f"  ⚠ {aoi.code}: still missing {len(missing)} dates: "
              f"{missing[:5]}{'…' if len(missing) > 5 else ''}")
        print(f"     resubmit those via `submit` or accept reduced coverage (MintPy will skip them)")
    else:
        print(f"  ✓ {aoi.code}: all {len(expected)} HyP3 acquisition dates have GACOS coverage")


def ingest(aoi: AOI, archive_path: Path) -> None:
    """Unpack one GACOS archive (.tar.gz or .zip) into the AOI cache dir.

    MintPy reads `YYYYMMDD.ztd.tif` GeoTIFFs from `mintpy.troposphericDelay.gacosDir`
    directly. We keep only those grids whose extent covers this AOI's centroid;
    previews and wrong-region tiles are dropped (see `_extract_ztd_tif`).
    """
    if not archive_path.exists():
        raise FileNotFoundError(archive_path)
    cache = _aoi_cache(aoi)
    n_new, n_skipped, n_dropped = _extract_ztd_tif(archive_path, aoi)
    drop_note = f", {n_dropped} dropped (outside {aoi.code})" if n_dropped else ""
    print(f"  ✓ ingested {n_new} GACOS grids into {cache}  ({n_skipped} already cached{drop_note})")
    _report_coverage(aoi, cache)


def ingest_dir(archive_dir: Path) -> None:
    """Ingest every archive in a directory, fanning each into the AOI caches it
    covers.

    The GACOS portal caps a submission at 20 dates, so each cluster's window
    arrives as several archives — and each is requested over a *combined* box
    (`submit`/`_combined_bbox`) that blankets every AOI in the cluster. So one
    archive feeds ALL the AOIs whose centroid its grids cover, not just one. We
    read the first grid's extent to find those AOIs, then extract per-AOI with
    the centroid guard (so a wrong-region archive feeds nobody).
    """
    if not archive_dir.is_dir():
        raise NotADirectoryError(archive_dir)
    archives = sorted(
        p for p in archive_dir.iterdir()
        if p.name.lower().endswith((".tar.gz", ".tgz", ".tar", ".zip"))
    )
    if not archives:
        print(f"  ⚠ no .tar.gz/.zip archives found in {archive_dir}")
        return

    touched: set[str] = set()
    for archive in archives:
        aois = _archive_covered_aois(archive)
        if not aois:
            print(f"  ⚠ {archive.name}: grids cover no registered AOI — skipped")
            continue
        for aoi in aois:
            n_new, n_skipped, n_dropped = _extract_ztd_tif(archive, aoi)
            print(f"  ✓ {archive.name} → {aoi.code}: {n_new} new, {n_skipped} skipped")
            touched.add(aoi.code)

    print()
    for code in sorted(touched):
        _report_coverage(by_code(code), _aoi_cache(by_code(code)))


def status(aoi: AOI) -> None:
    """One-shot report: how many dates have GACOS coverage."""
    dates = discover_acquisition_dates(aoi)
    cache = _aoi_cache(aoi)
    have = _cached_date_codes(cache)
    n_total = len(dates)
    n_have = sum(1 for d in dates if d.strftime("%Y%m%d") in have)
    print(f"  {aoi.code}: {n_have}/{n_total} dates covered  ({cache})")
    if n_have < n_total:
        first_missing = next(d for d in dates if d.strftime("%Y%m%d") not in have)
        print(f"    first missing: {first_missing.isoformat()}")


def main() -> None:
    p = argparse.ArgumentParser(description="GACOS tropospheric-delay fetcher")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_sub = sub.add_parser("submit", help="POST a GACOS time-series job")
    p_sub.add_argument("--aoi", required=True, nargs="+",
                       help="one or more AOI codes; multiple are unioned into one job "
                            "(combined bbox + deduped dates), e.g. south_c kileleshwa kilimani")
    p_sub.add_argument("--email", required=True, help="portal sends download URL here")

    p_ing = sub.add_parser("ingest", help="unpack one GACOS archive (.tar.gz/.zip) into the cache")
    p_ing.add_argument("--aoi", required=True)
    p_ing.add_argument("--archive", dest="archive_path", required=True, type=Path,
                       help="path to the GACOS .tar.gz or .zip")

    p_idir = sub.add_parser("ingest-dir",
                            help="ingest every archive in a dir, auto-routing each to its AOI")
    p_idir.add_argument("--dir", dest="archive_dir", required=True, type=Path)

    p_st = sub.add_parser("status", help="coverage report")
    p_st.add_argument("--aoi", required=True)

    args = p.parse_args()
    if args.cmd == "submit":
        submit([by_code(code) for code in args.aoi], args.email)
    elif args.cmd == "ingest":
        ingest(by_code(args.aoi), args.archive_path)
    elif args.cmd == "ingest-dir":
        ingest_dir(args.archive_dir)
    elif args.cmd == "status":
        status(by_code(args.aoi))


if __name__ == "__main__":
    main()
