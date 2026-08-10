"""Celery tasks wrapping the existing build-time pipeline scripts.

Each task shells out to the same `python -m scripts.<x>` entry point an operator
runs by hand — the scripts remain the single source of truth for all pipeline
and scoring logic. Tasks add only orchestration: retry classification, a
deterministic OpenSARLab gate for the SBAS step, and chain composition.

Idempotency: every wrapped script is a no-op on already-computed files, so a
retried or re-queued task is safe.

Path note: this module lives at backend/scripts/pipeline/tasks.py, so
BACKEND_DIR is parents[2].
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from celery import chain

from scripts.pipeline.celery_app import app

BACKEND_DIR = Path(__file__).resolve().parents[2]
MINTPY_DIR = BACKEND_DIR / "data" / "mintpy"
PY = sys.executable  # the interpreter running the worker → same venv as the scripts

# Trailing-edge debounce window (seconds) for flag-triggered rebuilds. A burst of
# structural-flag entries collapses into ONE rebuild this many seconds after the
# LAST entry, instead of one heavy rebuild per flag. Override via env.
REBUILD_DEBOUNCE_SECONDS = int(os.environ.get("INSAR_REBUILD_DEBOUNCE_SECONDS", "120"))


class StageError(RuntimeError):
    """A pipeline stage exited non-zero. Carries the stage name + exit code."""


class AwaitingOpenSARLab(RuntimeError):
    """SBAS outputs are not present yet. MintPy runs on ASF OpenSARLab (the laptop
    can't host ISCE); this is a deterministic human gate, NOT a transient error —
    the chain stops here until an operator runs SBAS and the HDF5s land."""


def _track_safe(track: str) -> str:
    return track.replace("/", "_")


def _run_module(module: str, args: list[str], *, stage: str, env: dict | None = None) -> str:
    """Run `python -m scripts.<module> <args>` from backend/, streaming output to
    the worker log. Returns combined stdout/stderr on success; raises StageError
    on non-zero exit so Celery records the failure with context.
    """
    import os

    cmd = [PY, "-m", module, *args]
    run_env = {**os.environ, **(env or {})}
    proc = subprocess.run(
        cmd, cwd=str(BACKEND_DIR), env=run_env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    output = proc.stdout or ""
    # Surface output to the worker's own stdout so `celery worker` logs show it.
    print(output, end="")
    if proc.returncode != 0:
        raise StageError(f"[{stage}] {module} exited {proc.returncode}")
    return output


# --------------------------------------------------------------------------
# Network stages — retryable (transient ASF/HyP3/GACOS failures back off and
# retry; the underlying scripts already classify transient vs fatal, so we only
# need a bounded retry here as a safety net around the subprocess boundary).
# --------------------------------------------------------------------------

@app.task(bind=True, name="insar.hyp3_submit_watch",
          autoretry_for=(StageError,), retry_backoff=30, retry_backoff_max=600,
          retry_jitter=True, max_retries=5)
def hyp3_submit_watch(self, aoi: str, tracks: str = "both") -> str:
    """Submit Sentinel-1 pairs to HyP3 and block until ready + downloaded.
    Idempotent: reuses on-disk + server-side jobs."""
    return _run_module(
        "scripts.hyp3_pipeline",
        ["submit", "--aoi", aoi, "--tracks", tracks, "--watch", "-y"],
        stage="hyp3",
    )


@app.task(bind=True, name="insar.gacos_submit",
          autoretry_for=(StageError,), retry_backoff=30, retry_backoff_max=600,
          retry_jitter=True, max_retries=5)
def gacos_submit(self, aoi: str, email: str) -> str:
    """POST a GACOS atmospheric-correction job. Non-blocking on the portal side
    (download URL arrives by email — a human ingests later)."""
    return _run_module(
        "scripts.fetch_gacos",
        ["submit", "--aoi", aoi, "--email", email],
        stage="gacos",
    )


# --------------------------------------------------------------------------
# CPU stages — deterministic, not retried (a failure is a real error to inspect,
# not a transient blip; re-running by hand after a fix is the right workflow).
# --------------------------------------------------------------------------

@app.task(name="insar.clip")
def clip(aoi: str) -> str:
    return _run_module("scripts.clip_to_common_grid", ["--aoi", aoi], stage="clip")


@app.task(name="insar.reproject")
def reproject(aoi: str) -> str:
    return _run_module(
        "scripts.reproject_hyp3",
        ["--aoi", aoi, "--src", "data/hyp3_work_clipped"],
        stage="reproject",
    )


@app.task(name="insar.mintpy_gate")
def mintpy_gate(aoi: str, track: str = "ASCENDING/57") -> str:
    """OpenSARLab gate for the SBAS inversion.

    MintPy/ISCE cannot run on the laptop, so SBAS is executed on ASF OpenSARLab
    and its `velocity.h5` / `timeseries.h5` are copied back into
    data/mintpy/<aoi>_<track>/. This task verifies those outputs exist and
    raises AwaitingOpenSARLab otherwise — a clear, NON-retried stop so the chain
    pauses for the human step instead of failing obscurely or fabricating data.

    (If/when OpenSARLab submit+poll is automated — see analysis_two.md Phase 0
    decision point #3 — this task becomes the submit+poll call.)
    """
    run_dir = MINTPY_DIR / f"{aoi}_{_track_safe(track)}"
    # velocity.h5 (already-geocoded HyP3 input) or geo/geo_velocity.h5 (radar input).
    candidates = [run_dir / "velocity.h5", run_dir / "geo" / "geo_velocity.h5"]
    if not any(p.exists() for p in candidates):
        raise AwaitingOpenSARLab(
            f"SBAS outputs missing for {aoi} [{track}]. Expected one of: "
            f"{[str(p) for p in candidates]}. Run smallbaselineApp.py on "
            f"OpenSARLab and copy velocity.h5 + timeseries.h5 into {run_dir}/, "
            f"then re-enqueue from the join stage."
        )
    return f"SBAS outputs present for {aoi} [{track}] in {run_dir}"


@app.task(name="insar.join")
def join(aoi: str, track: str = "ASCENDING/57", rebuild_db: bool = True) -> str:
    """Join MintPy outputs to footprints, score buildings, write GeoParquet, and
    (optionally) atomic-swap demo.duckdb. The swap is safe against the live
    FastAPI reader — it keeps serving its old handle; the next connection sees
    new data."""
    args = ["--aoi", aoi, "--track", track]
    if rebuild_db:
        args.append("--rebuild-db")
    return _run_module("scripts.join_insar", args, stage="join")


# --------------------------------------------------------------------------
# Composed chains
# --------------------------------------------------------------------------

@app.task(name="insar.refresh_aoi")
def refresh_aoi(aoi: str, track: str = "ASCENDING/57", *, tracks: str = "both") -> str:
    """Enqueue the full ordered chain for one AOI and return the chain's id.

    Order mirrors _run_aoi_chain.sh + the runbook:
        hyp3 → clip → reproject → mintpy_gate → join(+rebuild_db)

    GACOS is intentionally NOT in the auto-chain: it depends on a human
    downloading the portal email, so it's enqueued separately (gacos_submit).
    The chain halts cleanly at mintpy_gate until SBAS outputs exist.
    """
    sig = chain(
        hyp3_submit_watch.si(aoi, tracks),
        clip.si(aoi),
        reproject.si(aoi),
        mintpy_gate.si(aoi, track),
        join.si(aoi, track, True),
    )
    result = sig.apply_async()
    return result.id


@app.task(name="insar.rebuild_from_sbas")
def rebuild_from_sbas(aoi: str, track: str = "ASCENDING/57") -> str:
    """Resume the chain AFTER OpenSARLab SBAS is done: just gate + join. This is
    the task to enqueue once velocity.h5 has been copied back."""
    sig = chain(mintpy_gate.si(aoi, track), join.si(aoi, track, True))
    return sig.apply_async().id


# --------------------------------------------------------------------------
# Flag-triggered rebuild — provenance-aware + debounced
# --------------------------------------------------------------------------
# When a structural flag is recorded in Weespas, that building's score only
# changes once its scored parquet is regenerated. HOW to regenerate depends on
# the AOI's provenance, and getting this wrong silently drops the flag:
#   - 'insar' (real data): re-JOIN (rebuild_from_sbas). The synthetic seeder
#     REFUSES insar AOIs by design, so re-seeding would be a no-op → the flag
#     would never take effect. Must go through join_insar.
#   - 'synthetic' / 'partial': re-SEED that one AOI (reseed_aoi), which re-scores
#     with the now-exported flag and rebuilds the DB views.
# Debounce: a burst of flag entries shouldn't fire a rebuild per flag. We use a
# trailing-edge debounce backed by a Redis token (the broker connection) — each
# request bumps a per-AOI counter and schedules the actual rebuild after the
# window; the deferred run executes ONLY if its token is still the latest, so an
# intervening request supersedes it and just one rebuild runs after the last flag.

def _rebuild_token_key(aoi: str) -> str:
    return f"insar:rebuild:token:{aoi}"


def _redis_client():
    """The broker's Redis client (same instance/db the Celery app already uses).
    Returns None if the broker isn't Redis (then debounce degrades to run-now)."""
    try:
        return app.backend.client  # redis.Redis for a redis result backend
    except Exception:
        return None


@app.task(name="insar.request_rebuild")
def request_rebuild(aoi: str, track: str = "ASCENDING/57") -> str:
    """Debounced entry point: ask for `aoi` to be rebuilt soon. Coalesces a burst
    of calls into one rebuild REBUILD_DEBOUNCE_SECONDS after the last call.

    Returns a short status string (the scheduled token, or 'ran-now' fallback)."""
    r = _redis_client()
    if r is None:
        # No Redis to coordinate on — just rebuild now (correctness over coalescing).
        return rebuild_aoi(aoi, track)
    token = r.incr(_rebuild_token_key(aoi))  # monotonic per-AOI; survives restarts
    _debounced_rebuild.apply_async(
        args=[aoi, int(token), track], countdown=REBUILD_DEBOUNCE_SECONDS
    )
    return f"scheduled rebuild for {aoi} (token {token}, in {REBUILD_DEBOUNCE_SECONDS}s)"


@app.task(name="insar._debounced_rebuild")
def _debounced_rebuild(aoi: str, token: int, track: str = "ASCENDING/57") -> str:
    """Fires after the debounce window. Runs the rebuild ONLY if `token` is still
    the latest request for this AOI — otherwise a newer request arrived during the
    window and will run its own deferred task, so this stale one is a no-op."""
    r = _redis_client()
    if r is not None:
        current = r.get(_rebuild_token_key(aoi))
        if current is not None and int(current) != int(token):
            return f"superseded: {aoi} token {token} != current {int(current)} (skip)"
    return rebuild_aoi(aoi, track)


@app.task(name="insar.rebuild_aoi")
def rebuild_aoi(aoi: str, track: str = "ASCENDING/57") -> str:
    """Provenance-aware rebuild of ONE AOI's scored data (no debounce; the actual
    work). Real 'insar' AOIs re-join from SBAS; synthetic/partial AOIs re-seed."""
    from scripts.provenance import get_provenance

    prov = get_provenance(aoi)
    if prov == "insar":
        # Real data: re-join (gate + join). The seeder would refuse this AOI.
        sig = chain(mintpy_gate.si(aoi, track), join.si(aoi, track, True))
        return f"insar rebuild (re-join) enqueued for {aoi}: {sig.apply_async().id}"
    # synthetic / partial: re-seed just this AOI and rebuild the DB views.
    return _run_module(
        "scripts.reseed_one", ["--aoi", aoi], stage="reseed",
    )
