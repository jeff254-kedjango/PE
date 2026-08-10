"""
Build-time InSAR pipeline.

This runs OFFLINE relative to the demo: kick it off ahead of time, let it bake,
commit the resulting GeoParquet/DuckDB seed into the repo. The demo app never
reaches out to ASF.

Pipeline:
    1. asf_search    → discover S1 SLC scenes covering the AOI over the 24-month window
    2. ASF HyP3      → submit InSAR_GAMMA jobs for sequential scene pairs
    3. poll          → wait for completion, download zips
    4. MintPy SBAS   → smallbaselineApp.py on the stack to get velocities & cumulative displ.
    5. join          → spatial-join MintPy points to OSM building footprints
    6. emit          → write GeoParquet + load into DuckDB

This file is a SKELETON. Each step contains the calls you need; comments mark the
spots that require credentials, real disk space, and time. Don't run this on a
laptop you also need for slides — MintPy is RAM-hungry.

Credentials needed (set via env or ~/.netrc):
    EARTHDATA_USER, EARTHDATA_PASS   (NASA Earthdata Login)
    HYP3 uses the same credentials.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from scripts.aois import AOI, REGISTRY, by_code, bbox

START = date(2024, 6, 1)
END   = date(2026, 5, 31)
WORK_DIR = Path(__file__).resolve().parents[1] / "data" / "hyp3_work"
PARQUET_DIR = Path(__file__).resolve().parents[1] / "data" / "parquet"

# HyP3 enforces a 20-character cap on `name`. Job names are the only resume
# handle, so they MUST be deterministic from (aoi, flight, path, scene-pair).
# We pack: <aoi[:4]>-<dir[0]><path>-<ref8>-<sec8>, e.g. "huru-A57-20240606-20240618"
# would be 28 chars — too long. Compress to ref/sec as YYMMDD: "huru-A57-240606240618" = 21.
# Drop the inner dash → "huru-A57-240606-240618" = 22. Still over.
# Final format: "h-A57-240606240618" = 18 chars. Single-letter AOI code prefix.
_AOI_LETTER = {"huruma": "h", "mombasa": "m", "kileleshwa": "l", "kilimani": "i",
               "south_c": "s"}

# Network errors that mean "the link is flaky / down right now", as opposed to a
# real bug. We retry these forever; anything else propagates immediately so a
# genuine error (bad credentials, KeyError, etc.) still fails fast. Matched by
# class name too, so we don't have to import every SDK/urllib3 exception type.
_TRANSIENT_EXC_NAMES = frozenset({
    "ConnectionError", "ConnectTimeout", "ReadTimeout", "Timeout", "Timeout Error",
    "ChunkedEncodingError", "ProtocolError", "IncompleteRead", "RemoteDisconnected",
    "ConnectionResetError", "ConnectionAbortedError", "BrokenPipeError",
    "SSLError", "SSLEOFError", "NewConnectionError", "MaxRetryError",
    "TimeoutError", "socket.timeout", "HTTPError", "RequestException",
})


def _is_transient(exc: BaseException) -> bool:
    """True if `exc` (or any cause in its chain) looks like a transient network
    failure we should wait out. We check the whole __cause__/__context__ chain
    because SDKs wrap the underlying urllib3/socket error."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        names = {type(cur).__name__} | {b.__name__ for b in type(cur).__mro__}
        if names & _TRANSIENT_EXC_NAMES:
            return True
        # OSError covers most socket-level drops (ECONNRESET, ETIMEDOUT, EHOSTUNREACH…)
        if isinstance(cur, OSError):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def _retry_forever(fn, *, what: str, base: float = 10.0, cap: float = 600.0,
                   log=print, _sleep=time.sleep):
    """Call `fn()` and return its result; on a TRANSIENT network error, sleep
    with capped exponential backoff + jitter and retry **indefinitely**.

    This is the "don't fail on low internet" guarantee: a dropped Wi-Fi, a laptop
    sleep that kills the socket, or ASF being briefly unreachable pauses us rather
    than aborting the overnight run. Only transient errors are caught — a real bug
    (auth failure, programming error) re-raises on the first occurrence so we
    don't spin forever on something a retry can't fix.

    CALLERS MUST BE IDEMPOTENT: `fn` may run many times. Our download (skips
    already-extracted products) and watch (jobs live server-side) both are.

    `_sleep` is injectable so tests can assert backoff without real waits.
    """
    attempt = 0
    while True:
        try:
            return fn()
        except BaseException as exc:  # noqa: BLE001 — re-raised below unless transient
            if not _is_transient(exc):
                raise
            attempt += 1
            delay = min(cap, base * (2 ** (attempt - 1)))
            delay += random.uniform(0, delay * 0.25)  # jitter: avoid thundering herd
            log(f"  ⚠ {what}: network error ({type(exc).__name__}: {exc}); "
                f"retry {attempt} in {delay:.0f}s…")
            _sleep(delay)


def _pair_key(flight: str, path: int, ref: datetime, sec: datetime) -> str:
    """AOI-independent identity of a scene pair: "<D><path>-<ref><sec>".

    Two AOIs in the same Sentinel-1 frame (e.g. Huruma, Kileleshwa, Kilimani on
    ascending path 57) produce byte-identical full-frame interferograms for the
    same pair, so the job-name suffix is shared. Reuse + cross-AOI dedup key off
    this — it's everything in `_job_name` after the per-AOI letter prefix.
    """
    d = flight[:1]  # "A" or "D"
    return f"{d}{path}-{ref:%y%m%d}{sec:%y%m%d}"


def _job_name(aoi_code: str, flight: str, path: int, ref: datetime, sec: datetime) -> str:
    letter = _AOI_LETTER.get(aoi_code, aoi_code[:1])
    return f"{letter}-{_pair_key(flight, path, ref, sec)}"


def _aoi_wkt(aoi: AOI) -> str:
    """Closed-ring WKT polygon for the AOI bounding box."""
    minlon, minlat, maxlon, maxlat = bbox(aoi)
    return (
        f"POLYGON(({minlon} {minlat}, {maxlon} {minlat}, {maxlon} {maxlat}, "
        f"{minlon} {maxlat}, {minlon} {minlat}))"
    )


@dataclass(frozen=True)
class Scene:
    name: str
    start_time: datetime
    path: int
    flight_direction: str  # "ASCENDING" or "DESCENDING"


@dataclass(frozen=True)
class ScenePair:
    reference: str
    secondary: str
    # Carried through so the rest of the pipeline can keep ASC and DESC stacks
    # separate when running MintPy.
    path: int
    flight_direction: str
    # Start times let us build deterministic, resume-safe HyP3 job names
    # without re-querying ASF on restart.
    reference_time: datetime
    secondary_time: datetime


def search_scenes(aoi: AOI, start: date = START, end: date = END) -> list[Scene]:
    """Step 1. Find every S1 SLC scene over the AOI in the window.

    Returns scenes with enough metadata to build per-track pair chains in
    `make_pairs`. We intentionally keep both ascending and descending tracks
    here — vector decomposition of LOS into vertical + east-west requires both.
    """
    import asf_search as asf

    results = asf.geo_search(
        intersectsWith=_aoi_wkt(aoi),
        platform=asf.PLATFORM.SENTINEL1,
        processingLevel=asf.PRODUCT_TYPE.SLC,
        beamMode=asf.BEAMMODE.IW,
        start=start.isoformat(),
        end=end.isoformat(),
    )
    scenes: list[Scene] = []
    for r in results:
        p = r.properties
        ts = p.get("startTime")
        # asf_search returns ISO 8601 with trailing Z
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else datetime.min
        scenes.append(
            Scene(
                name=p["sceneName"],
                start_time=dt,
                path=int(p.get("pathNumber", -1)),
                flight_direction=p.get("flightDirection", "?"),
            )
        )
    scenes.sort(key=lambda s: (s.flight_direction, s.path, s.start_time))
    return scenes


def make_pairs(
    scenes: list[Scene],
    max_temporal_baseline_days: int = 24,
) -> list[ScenePair]:
    """Step 2a. Build sequential pairs grouped by (flight_direction, path).

    Mixing ascending and descending in a single InSAR pair is nonsense — the LOS
    geometry differs. Mixing two different relative orbits over the same AOI is
    equally bad: the imaging geometry shifts and the interferogram falls apart.
    So we partition by (flight_direction, path) and chain within each group.

    `max_temporal_baseline_days` protects against decorrelation: pairs longer
    than ~24 days lose coherence quickly in vegetated areas.
    """
    by_track: dict[tuple[str, int], list[Scene]] = defaultdict(list)
    for s in scenes:
        by_track[(s.flight_direction, s.path)].append(s)

    pairs: list[ScenePair] = []
    for (flight, path), group in by_track.items():
        group.sort(key=lambda s: s.start_time)
        for i in range(len(group) - 1):
            ref, sec = group[i], group[i + 1]
            dt_days = (sec.start_time - ref.start_time).days
            if dt_days <= 0 or dt_days > max_temporal_baseline_days:
                continue
            pairs.append(
                ScenePair(
                    reference=ref.name,
                    secondary=sec.name,
                    path=path,
                    flight_direction=flight,
                    reference_time=ref.start_time,
                    secondary_time=sec.start_time,
                )
            )
    return pairs


# Default track selection per AOI. Picked from Stage-1 plan: longest ASC chain
# per AOI gives a clean LOS series back to 2024-06; DESC tracks only go back to
# 2025-04 so we leave them as opt-in via --all-tracks.
DEFAULT_TRACKS: dict[str, list[tuple[str, int]]] = {
    "huruma":     [("ASCENDING", 57)],
    "mombasa":    [("ASCENDING", 159)],
    # Kileleshwa & Kilimani share Huruma's ascending path-57 Nairobi frame, so
    # their default (ascending) stack is fully reused from Huruma at zero cost.
    # Descending is opt-in via `--tracks both` (its path is discovered, not
    # hardcoded, because DESC history over Nairobi is shorter — see select_best_tracks).
    "kileleshwa": [("ASCENDING", 57)],
    "kilimani":   [("ASCENDING", 57)],
    "south_c":    [("ASCENDING", 57)],
}


def filter_to_tracks(pairs: list[ScenePair], tracks: list[tuple[str, int]]) -> list[ScenePair]:
    """Keep only pairs matching one of the given (flight_direction, path) tracks."""
    keep = set(tracks)
    return [p for p in pairs if (p.flight_direction, p.path) in keep]


def select_best_tracks(
    pairs: list[ScenePair], directions: list[str]
) -> list[tuple[str, int]]:
    """Pick the longest-chain (flight, path) track for each requested direction.

    The descending relative-orbit path over an AOI isn't known a priori, and an
    AOI can be clipped by several paths with only a handful of pairs each. For
    each direction we choose the path with the most pairs (the usable stack) and
    drop the rest. O(#pairs); pure.
    """
    counts: dict[tuple[str, int], int] = defaultdict(int)
    for p in pairs:
        counts[(p.flight_direction, p.path)] += 1
    chosen: list[tuple[str, int]] = []
    for direction in directions:
        in_dir = [(t, n) for t, n in counts.items() if t[0] == direction]
        if not in_dir:
            continue
        chosen.append(max(in_dir, key=lambda kv: kv[1])[0])
    return chosen


def _connect_hyp3():
    """Build an authenticated HyP3 client. Reads .env on first call."""
    from dotenv import load_dotenv
    from hyp3_sdk import HyP3
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    user = os.environ.get("EARTHDATA_USER", "").strip()
    pw = os.environ.get("EARTHDATA_PASS", "").strip()
    if not user or not pw:
        raise RuntimeError("EARTHDATA_USER / EARTHDATA_PASS missing — populate backend/.env")
    return HyP3(username=user, password=pw)


def _submit_one_pair(hyp3, aoi_code: str, pair: ScenePair):
    """Submit a single InSAR_GAMMA job with bounded retry. Returns its Batch."""
    name = _job_name(aoi_code, pair.flight_direction, pair.path,
                     pair.reference_time, pair.secondary_time)
    for attempt in range(3):
        try:
            return hyp3.submit_insar_job(
                granule1=pair.reference,
                granule2=pair.secondary,
                name=name,
                looks="20x4",                      # 80 m × 80 m pixels — right size for building-scale InSAR
                include_displacement_maps=True,    # LOS + vertical displacement, headline product
                include_inc_map=True,              # needed for ASC/DESC vector decomposition
                include_dem=True,                  # MintPy needs the SRTM clip
                apply_water_mask=True,             # silences ocean phase noise (Mombasa)
                phase_filter_parameter=0.6,        # SDK default; right for mixed urban/vegetation
            )
        except Exception as e:
            if attempt == 2:  # Hard crash if all 3 attempts fail
                print(f"❌ Failed to submit {name} after 3 attempts.")
                raise
            print(f"⚠️ Server hiccup ({e}). Pausing 5 seconds before retrying...")
            time.sleep(5)


def submit_hyp3(
    aoi_code: str,
    pairs: list[ScenePair],
    hyp3=None,
):
    """Step 2b. Submit InSAR_GAMMA jobs idempotently, reusing everything we can.

    HyP3 *is* the manifest: each pair gets a deterministic name. Before spending
    any credit we reuse, in order:
      1. **On-disk products** from sibling AOIs in the same Sentinel-1 frame
         (`_link_existing_products`) — symlinked in, zero credits, zero download.
      2. **Existing HyP3 jobs for the same pair, from ANY AOI** — matched by
         AOI-independent `_pair_key`, not just this AOI's name prefix. A pair
         already processed under another AOI is resumed + downloaded here rather
         than re-submitted, so the same granules are never charged twice.
    Only genuinely new pairs are submitted. O(1) network calls; lookups O(1).

    Returns a hyp3_sdk.Batch of jobs whose products still need downloading
    (resumed-from-HyP3 + newly submitted). Pairs satisfied from disk in step 1
    are not in the batch — they're already on disk under this AOI.
    """
    from hyp3_sdk import Batch

    if hyp3 is None:
        hyp3 = _connect_hyp3()

    # Step 1: reuse on-disk products from sibling AOIs (and our own prior runs).
    reused_keys = _link_existing_products(aoi_code, pairs)
    remaining = [
        p for p in pairs
        if _pair_key(p.flight_direction, p.path, p.reference_time, p.secondary_time)
        not in reused_keys
    ]

    # Step 2: index every existing HyP3 job by AOI-independent pair_key. We don't
    # filter by this AOI's letter — a pair processed under any AOI is reusable.
    existing = hyp3.find_jobs(job_type="INSAR_GAMMA")
    existing_by_key: dict[str, object] = {}
    for j in existing:
        if not j.name or "-" not in j.name:
            continue
        key = j.name.split("-", 1)[1]  # strip "<letter>-"
        # Prefer a succeeded job over a running/failed one for the same key.
        prev = existing_by_key.get(key)
        if prev is None or (j.succeeded() and not prev.succeeded()):
            existing_by_key[key] = j

    resumed: list = []
    todo: list[ScenePair] = []
    for p in remaining:
        key = _pair_key(p.flight_direction, p.path, p.reference_time, p.secondary_time)
        j = existing_by_key.get(key)
        if j is not None and not j.failed():
            resumed.append(j)
        else:
            todo.append(p)

    print(f"  reused {len(reused_keys)} from disk, resumed {len(resumed)} HyP3 "
          f"job(s), submitting {len(todo)} new pair(s)")

    # Submit the genuinely new pairs. One roundtrip per pair — there's no public
    # bulk endpoint, and parallel HTTP would only hit the rate limiter.
    new_batch = Batch()
    for idx, pair in enumerate(todo):
        name = _job_name(aoi_code, pair.flight_direction, pair.path,
                         pair.reference_time, pair.secondary_time)
        print(f"   -> Submitting pair {idx + 1}/{len(todo)}: {name}")
        new_batch += _submit_one_pair(hyp3, aoi_code, pair)

    full = Batch()
    for j in resumed:
        full += j
    full += new_batch
    return full


def wait_and_download(batch, dest: Path, hyp3=None):
    """Step 3. Block on completion, then download finished products in parallel.

    `hyp3.watch()` uses adaptive polling (60-300s) and blocks until every job in
    the batch reaches a terminal state. Cheaper and faster than a hand-rolled
    loop. We parallelize the downloads with a small thread pool — products are
    50-200 MB each, and a single sequential download would take all day.
    """
    if hyp3 is None:
        hyp3 = _connect_hyp3()

    dest.mkdir(parents=True, exist_ok=True)
    print(f"  watching {len(batch)} jobs (this can take hours)…")

    # watch() blocks for hours polling ASF; a Wi-Fi blip or laptop sleep that
    # kills the socket would otherwise abort the whole overnight run. Jobs live
    # SERVER-SIDE, so on a transient error we just reconnect and re-watch the
    # still-pending jobs — nothing is lost, progress is never thrown away.
    def _watch_once():
        nonlocal hyp3
        try:
            return hyp3.watch(batch)
        except BaseException as exc:
            if _is_transient(exc):
                # Rebuild the client so a dead connection pool can't poison the
                # retry; the re-watch picks up wherever ASF's queue now stands.
                hyp3 = _connect_hyp3()
            raise

    batch = _retry_forever(_watch_once, what="watching jobs")
    succeeded = [j for j in batch if j.succeeded()]
    failed = [j for j in batch if j.failed()]
    print(f"  {len(succeeded)} succeeded, {len(failed)} failed")
    for j in failed:
        # Surface the failure reason so we can decide whether to resubmit.
        print(f"    FAILED {j.name}: {getattr(j, 'status_code', '?')}")

    # Parallel download. HyP3 product URLs are pre-signed S3, so the bottleneck
    # is bandwidth, not the server — 4-8 concurrent connections saturates a
    # typical home line without provoking rate limiting.
    to_fetch = [j for j in succeeded if not _already_extracted(j, dest)]
    print(f"  downloading {len(to_fetch)} products (skipping {len(succeeded) - len(to_fetch)} already on disk)…")

    def _fetch(job):
        # Retry THIS product forever on a flaky link. Idempotent: a half-written
        # download is re-fetched cleanly, and once the rasters are extracted
        # _already_extracted short-circuits so a retry costs nothing.
        def _do():
            if _already_extracted(job, dest):
                return job.name
            files = job.download_files(dest, create=True)
            for f in files:
                _unzip(Path(f), dest / job.name)
                Path(f).unlink(missing_ok=True)
            return job.name
        return _retry_forever(_do, what=f"download {job.name}")

    with ThreadPoolExecutor(max_workers=6) as pool:
        for fut in as_completed(pool.submit(_fetch, j) for j in to_fetch):
            name = fut.result()
            print(f"    ✓ {name}")

    return [dest / j.name for j in succeeded]


def _dir_has_products(d: Path) -> bool:
    """True if `d` is an extracted GAMMA product dir with the rasters MintPy
    needs. `glob` follows into symlinked dirs, so reused (linked) pairs pass."""
    if not d.is_dir():
        return False
    # GAMMA InSAR output contains <product>_unw_phase.tif and <product>_corr.tif
    return any(d.glob("*_unw_phase.tif")) and any(d.glob("*_corr.tif"))


def _already_extracted(job, dest: Path) -> bool:
    """Skip-download check: the per-job product dir exists and has its rasters."""
    return _dir_has_products(dest / job.name)


def _ondisk_pair_index() -> dict[str, Path]:
    """Map AOI-independent pair_key → an extracted product dir, across all AOIs.

    The key is the dir name with its leading "<letter>-" stripped, so a pair
    downloaded under any AOI in the same frame is findable. O(#on-disk-dirs).
    """
    on_disk: dict[str, Path] = {}
    for aoi_dir in sorted(WORK_DIR.glob("*")):
        if not aoi_dir.is_dir():
            continue
        for pair_dir in aoi_dir.iterdir():
            if "-" not in pair_dir.name or not _dir_has_products(pair_dir):
                continue
            key = pair_dir.name.split("-", 1)[1]  # strip "<letter>-"
            on_disk.setdefault(key, pair_dir)
    return on_disk


def _count_reusable_on_disk(aoi_code: str, pairs: list[ScenePair]) -> int:
    """How many of `pairs` are already on disk (this AOI or a sibling). Read-only
    — used for an honest pre-submit credit estimate without mutating anything."""
    on_disk = _ondisk_pair_index()
    return sum(
        1 for p in pairs
        if _pair_key(p.flight_direction, p.path, p.reference_time, p.secondary_time) in on_disk
    )


def _link_existing_products(aoi_code: str, pairs: list[ScenePair]) -> set[str]:
    """Reuse already-downloaded full-frame products from sibling AOIs.

    Two AOIs in the same Sentinel-1 frame (Huruma/Kileleshwa/Kilimani on
    ascending path 57) get byte-identical full-frame interferograms for the same
    pair — the per-AOI clip happens downstream in `hyp3_work_clipped`. So instead
    of re-ordering (and re-paying for) those pairs, we symlink the existing
    product dir into this AOI's folder. `reproject_hyp3`/`mintpy_run` enumerate by
    folder, so a linked dir is consumed transparently.

    Returns the set of reused pair_keys so the caller drops them from the
    submit/download set. Idempotent: existing links are left untouched. O(#pairs
    + #on-disk-dirs); lookups are O(1).
    """
    on_disk = _ondisk_pair_index()

    dest_aoi = WORK_DIR / aoi_code
    dest_aoi.mkdir(parents=True, exist_ok=True)

    reused: set[str] = set()
    for p in pairs:
        key = _pair_key(p.flight_direction, p.path, p.reference_time, p.secondary_time)
        src = on_disk.get(key)
        if src is None or src.parent.name == aoi_code:
            # No existing copy, or it's already this AOI's own download.
            if src is not None:
                reused.add(key)  # own product already present — nothing to submit
            continue
        link = dest_aoi / src.name  # keep the source dir name (e.g. "h-A57-…")
        if not link.exists():
            link.symlink_to(src.resolve(), target_is_directory=True)
        reused.add(key)

    if reused:
        print(f"  reused {len(reused)} existing pair(s) from sibling AOIs "
              f"(0 credits, 0 download)")
    return reused


def _unzip(zip_path: Path, into: Path) -> None:
    """Extract a HyP3 product zip, flattening the single top-level directory."""
    into.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        members = zf.namelist()
        # HyP3 zips wrap everything in <product_id>/ — strip that prefix.
        top = os.path.commonpath(members).rstrip("/") + "/" if members else ""
        for m in members:
            if m.endswith("/"):
                continue
            rel = m[len(top):] if top and m.startswith(top) else m
            out = into / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(m) as src, open(out, "wb") as dst:
                # 1 MiB chunks — small enough to keep memory flat across the
                # thread pool, large enough to keep syscall overhead negligible.
                while True:
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    dst.write(chunk)


def run_mintpy(stack_dir: Path) -> Path:
    """Step 4. Run MintPy headlessly. Writes velocity.h5 and timeseries.h5."""
    raise NotImplementedError(
        "Write a smallbaselineApp config (smallbaselineApp.cfg) pointing at:\n"
        "    mintpy.load.processor      = hyp3\n"
        "    mintpy.load.unwFile        = <stack>/*/*unw_phase.tif\n"
        "    mintpy.load.corFile        = <stack>/*/*corr.tif\n"
        "    mintpy.load.demFile        = <stack>/*/*dem.tif\n"
        "Then: subprocess.run(['smallbaselineApp.py', 'smallbaselineApp.cfg'], cwd=stack_dir)\n"
        "Output: velocity.h5, timeseries.h5"
    )


def join_to_footprints(mintpy_out: Path, footprints_geojson: Path) -> Path:
    """Step 5. Spatial-join MintPy points to OSM building footprints. Emit GeoParquet.

    The deliverables here match the seeder's schema exactly:
      - buildings.parquet            (one row per footprint)
      - subsidence.parquet           (building_id, observation_date, displacement_mm, ...)
    """
    raise NotImplementedError(
        "Approach:\n"
        "  1. Use h5py to read mintpy_out/timeseries.h5 → array of (n_dates, n_y, n_x)\n"
        "  2. Read mintpy_out/geo/geo_velocity.h5 lat/lon arrays for georeferencing\n"
        "  3. For each footprint polygon, take the mean displacement over intersecting pixels\n"
        "     weighted by coherence (drop pixels with coherence < 0.3)\n"
        "  4. Write GeoParquet via geopandas.to_parquet(..., schema_version='1.0.0')"
    )


def load_into_duckdb(parquet_dir: Path, db_path: Path):
    """Step 6. Replace the demo DuckDB with real data using the same schema."""
    import duckdb
    con = duckdb.connect(str(db_path))
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute((parquet_dir.parent / "init_db.sql").read_text())  # init_db.sql is reused
    con.execute(f"INSERT INTO buildings SELECT * FROM read_parquet('{parquet_dir}/buildings.parquet');")
    con.execute(f"INSERT INTO subsidence_time_series SELECT * FROM read_parquet('{parquet_dir}/subsidence.parquet');")
    con.close()


def plan(aoi: AOI) -> dict:
    """Stage-1 entry: discover scenes and report what HyP3 will be asked to do.

    Does NOT submit jobs. Use this to sanity-check track coverage and pair
    counts before committing compute. The dict is JSON-serializable so the
    caller can stash it as a manifest before Stage 2.
    """
    scenes = search_scenes(aoi)
    pairs = make_pairs(scenes)

    per_track: dict[str, dict] = {}
    for s in scenes:
        key = f"{s.flight_direction}/path={s.path}"
        per_track.setdefault(key, {"scenes": 0, "first": None, "last": None})
        per_track[key]["scenes"] += 1
        ts = s.start_time.date().isoformat()
        if per_track[key]["first"] is None or ts < per_track[key]["first"]:
            per_track[key]["first"] = ts
        if per_track[key]["last"] is None or ts > per_track[key]["last"]:
            per_track[key]["last"] = ts
    for key in per_track:
        per_track[key]["pairs"] = sum(
            1 for p in pairs
            if f"{p.flight_direction}/path={p.path}" == key
        )

    return {
        "aoi": aoi.code,
        "window": [START.isoformat(), END.isoformat()],
        "n_scenes": len(scenes),
        "n_pairs": len(pairs),
        "per_track": per_track,
    }


def _resolve_pairs(aoi: AOI, tracks_mode: str) -> list[ScenePair]:
    """Resolve the scene pairs for an AOI under a track-selection mode.

    - "default": the configured `DEFAULT_TRACKS` track(s) — single best ASC stack.
    - "both":    the longest ascending AND longest descending chain (paths
                 discovered from the data, not hardcoded).
    - "all":     every track returned by ASF.
    """
    scenes = search_scenes(aoi)
    pairs = make_pairs(scenes)
    if tracks_mode == "all":
        return pairs
    if tracks_mode == "both":
        tracks = select_best_tracks(pairs, ["ASCENDING", "DESCENDING"])
        if not tracks:
            raise RuntimeError(f"no ASC/DESC tracks found for {aoi.code}")
        return filter_to_tracks(pairs, tracks)
    # "default"
    tracks = DEFAULT_TRACKS.get(aoi.code, [])
    if not tracks:
        raise RuntimeError(
            f"no default track configured for {aoi.code}; pass --tracks both or --tracks all")
    return filter_to_tracks(pairs, tracks)


def _cmd_plan(args: argparse.Namespace) -> None:
    """Stage 1: print scene plan for the requested AOIs without submitting."""
    aois = [by_code(c) for c in args.aoi] if args.aoi else REGISTRY
    for aoi in aois:
        print(f"\n=== {aoi.code} ({aoi.name}) ===")
        p = plan(aoi)
        print(json.dumps(p, indent=2))


def _cmd_submit(args: argparse.Namespace) -> None:
    """Stage 2: submit pairs to HyP3 (idempotent), optionally watch + download."""
    if args.all_tracks:  # deprecated alias
        args.tracks = "all"
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    aois = [by_code(c) for c in args.aoi] if args.aoi else REGISTRY
    hyp3 = _connect_hyp3()

    # Estimate cost before we submit so the user can abort. Subtract pairs we
    # already have on disk (reused from sibling AOIs at zero credit cost) so the
    # number shown is the real, post-dedup credit spend — not the gross count.
    plans: list[tuple[AOI, list[ScenePair]]] = []
    total_new = 0
    for aoi in aois:
        pairs = _resolve_pairs(aoi, tracks_mode=args.tracks)
        reusable = _count_reusable_on_disk(aoi.code, pairs)
        new = len(pairs) - reusable
        plans.append((aoi, pairs))
        total_new += new
        print(f"  {aoi.code}: {len(pairs)} pairs ({reusable} reused, {new} new to submit)")
    info = hyp3.my_info()
    remaining = info.get("remaining_credits")
    print(f"\n  total NEW pairs to submit: {total_new}; HyP3 credits remaining: {remaining}")
    if total_new == 0:
        print("  nothing new to download — all pairs already on disk.")
    if remaining is not None and total_new > remaining:
        sys.exit(f"insufficient credits: need {total_new}, have {remaining}")
    if not args.yes:
        resp = input("  proceed? [y/N] ").strip().lower()
        if resp != "y":
            sys.exit("aborted")

    batches = []
    for aoi, pairs in plans:
        print(f"\n--- submitting {aoi.code} ---")
        b = submit_hyp3(aoi.code, pairs, hyp3=hyp3)
        batches.append((aoi, b))

    if args.watch:
        for aoi, b in batches:
            print(f"\n--- watching + downloading {aoi.code} ---")
            wait_and_download(b, WORK_DIR / aoi.code, hyp3=hyp3)


def _cmd_status(args: argparse.Namespace) -> None:
    """Quick status report: count of jobs per AOI in each terminal state."""
    hyp3 = _connect_hyp3()
    jobs = hyp3.find_jobs(job_type="INSAR_GAMMA")
    buckets: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for j in jobs:
        if not j.name:
            continue
        aoi_letter = j.name[:1]
        aoi = next((c for c, l in _AOI_LETTER.items() if l == aoi_letter), aoi_letter)
        status = "SUCCEEDED" if j.succeeded() else "FAILED" if j.failed() else "RUNNING/PENDING"
        buckets[aoi][status] += 1
    for aoi, counts in sorted(buckets.items()):
        print(f"  {aoi}: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))


def main():
    """CLI entry. Run from backend/:

        python -m scripts.hyp3_pipeline plan
        python -m scripts.hyp3_pipeline submit --watch
        python -m scripts.hyp3_pipeline status
    """
    p = argparse.ArgumentParser(description="InSAR pipeline driver")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_plan = sub.add_parser("plan", help="print scene plan without submitting")
    p_plan.add_argument("--aoi", action="append", help="AOI code (repeatable); default = all")
    p_plan.set_defaults(func=_cmd_plan)

    p_sub = sub.add_parser("submit", help="submit pairs to HyP3 (idempotent)")
    p_sub.add_argument("--aoi", action="append", help="AOI code (repeatable); default = all")
    p_sub.add_argument("--tracks", choices=["default", "both", "all"], default="default",
                       help="track selection. default=best ASC stack per AOI; "
                            "both=longest ASC + longest DESC chain (for vertical/EW); "
                            "all=every track ASF returns")
    p_sub.add_argument("--all-tracks", action="store_true",
                       help="deprecated alias for --tracks all")
    p_sub.add_argument("--watch", action="store_true",
                       help="block until jobs finish and download products")
    p_sub.add_argument("-y", "--yes", action="store_true", help="skip confirmation prompt")
    p_sub.set_defaults(func=_cmd_submit)

    p_stat = sub.add_parser("status", help="report HyP3 job state per AOI")
    p_stat.set_defaults(func=_cmd_status)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
