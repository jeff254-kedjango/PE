"""Tiny control-plane API to trigger + monitor pipeline refreshes.

DELIBERATELY SEPARATE from the read-serving app (`app/main.py`), which stays a
pure read-only process over the in-RAM bundle. This is the operator/automation
seam (the future Weespas "rebuild this AOI" call lands here too). It only
enqueues Celery tasks and reports their status — it never does heavy work in the
request, and it never touches the serving app's data path.

Auth: a single shared token via `INSAR_ADMIN_TOKEN` (sent as `X-Admin-Token`).
If the env var is unset the API refuses to start a mutating request — refresh is
a privileged, expensive action and must not be open. (Status reads are allowed
without a token; they expose no data, only task state.)

Run (separately from the read app, different port):
    uvicorn scripts.pipeline.control_api:app --port 8001
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from scripts.pipeline.celery_app import app as celery_app
from scripts.pipeline import tasks
from scripts.pipeline import reconcile_flags

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """On startup, heal any structural-flag export that was written while this
    control stack was offline (its rebuild trigger would have been a no-op). This
    enqueues a debounced rebuild only for AOIs whose served scores actually differ
    from their export — idempotent, and a no-op when everything is already in sync.
    Never blocks or fails startup (reconcile_on_startup swallows its own errors)."""
    requested = reconcile_flags.reconcile_on_startup()
    if requested:
        logger.warning("startup flag reconciliation enqueued rebuilds: %s", requested)
    yield


app = FastAPI(title="InSAR pipeline control", version="0.1.0", lifespan=lifespan)


def _require_admin(token: str | None) -> None:
    expected = os.environ.get("INSAR_ADMIN_TOKEN")
    if not expected:
        raise HTTPException(503, "refresh disabled: INSAR_ADMIN_TOKEN not configured")
    if token != expected:
        raise HTTPException(401, "invalid or missing X-Admin-Token")


class RefreshRequest(BaseModel):
    aoi: str
    track: str = "ASCENDING/57"
    tracks: str = "both"
    # When True, skip HyP3/clip/reproject and resume at the MintPy gate → join.
    # Use after copying OpenSARLab SBAS outputs back to disk.
    from_sbas: bool = False


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "broker": celery_app.conf.broker_url}


@app.post("/admin/refresh")
async def trigger_refresh(
    req: RefreshRequest,
    x_admin_token: str | None = Header(default=None),
) -> dict:
    """Enqueue a refresh for one AOI; returns a task id to poll. Does NOT block
    on the pipeline (which can run for hours)."""
    _require_admin(x_admin_token)
    if req.from_sbas:
        async_result = tasks.rebuild_from_sbas.delay(req.aoi, req.track)
    else:
        async_result = tasks.refresh_aoi.delay(req.aoi, req.track, tracks=req.tracks)
    return {"task_id": async_result.id, "aoi": req.aoi, "from_sbas": req.from_sbas}


class RebuildRequest(BaseModel):
    aoi: str
    track: str = "ASCENDING/57"


@app.post("/admin/request-rebuild")
async def request_rebuild(
    req: RebuildRequest,
    x_admin_token: str | None = Header(default=None),
) -> dict:
    """Debounced, provenance-aware rebuild of one AOI's scored data. This is the
    seam Weespas calls after a structural flag is recorded — a burst of flags
    coalesces into a single rebuild (see tasks.request_rebuild). Returns the
    enqueue id; the rebuild itself fires after the debounce window."""
    _require_admin(x_admin_token)
    async_result = tasks.request_rebuild.delay(req.aoi, req.track)
    return {"task_id": async_result.id, "aoi": req.aoi, "debounced": True}


@app.get("/admin/refresh/{task_id}")
async def refresh_status(task_id: str) -> dict:
    """Report a refresh task's state. Read-only; no token required."""
    res = celery_app.AsyncResult(task_id)
    body: dict = {"task_id": task_id, "state": res.state}
    # `info` is the return value on success, or the exception repr on failure.
    if res.failed():
        body["error"] = repr(res.info)
    elif res.successful():
        body["result"] = res.info
    return body
