# Build pipeline orchestration (Celery + Redis)

Turns the build-time InSAR pipeline — previously a set of overnight shell
scripts (`overnight_download.sh`, `_run_aoi_chain.sh`) — into a managed,
observable, retryable, schedulable task graph.

**This does not touch the serving app.** `app/main.py` stays a single FastAPI
process serving an in-RAM bundle. Celery orchestrates *build-time* work only; a
refresh ends with an atomic `demo.duckdb` swap that the live reader picks up on
its next connection. Nothing here is on the request hot path.

## What it wraps (no logic reimplemented)

Each task shells out to the same `python -m scripts.<x>` entry point you'd run
by hand, so the scripts remain the single source of truth and keep their
logging, idempotency, and self-healing:

| Task | Wraps | Nature |
|---|---|---|
| `insar.hyp3_submit_watch` | `scripts.hyp3_pipeline submit --watch` | network, retried |
| `insar.gacos_submit` | `scripts.fetch_gacos submit` | network, retried |
| `insar.clip` | `scripts.clip_to_common_grid` | CPU |
| `insar.reproject` | `scripts.reproject_hyp3` | CPU |
| `insar.mintpy_gate` | (gate — see below) | human gate |
| `insar.join` | `scripts.join_insar --rebuild-db` | CPU + atomic DB swap |
| `insar.refresh_aoi` | full chain | composition |
| `insar.rebuild_from_sbas` | gate → join | resume after SBAS |

## The OpenSARLab gate

MintPy/ISCE can't run on the laptop, so SBAS runs on **ASF OpenSARLab**.
`insar.mintpy_gate` does not run MintPy — it verifies `velocity.h5` (or
`geo/geo_velocity.h5`) exists in `data/mintpy/<aoi>_<track>/` and otherwise
raises `AwaitingOpenSARLab`, halting the chain deterministically. Run SBAS on
OpenSARLab, copy `velocity.h5` + `timeseries.h5` back into that dir, then
enqueue `insar.rebuild_from_sbas` to resume.

> If OpenSARLab submit+poll is later automated, this task becomes that call and
> the chain runs unattended (see `analysis_two.md`, Phase 0 decision #3).

## Running it (laptop / on-demand)

```bash
# 1. Redis (broker). Any local Redis works; we use DB index 2.
redis-server &                       # or: docker run -p 6379:6379 redis

# 2. A worker (from backend/, in the venv with requirements.txt installed)
celery -A scripts.pipeline.celery_app worker --loglevel=info --concurrency=1

# 3. Enqueue a refresh (from a Python shell / another process)
python -c "from scripts.pipeline.tasks import refresh_aoi; print(refresh_aoi.delay('huruma','ASCENDING/57').id)"

# 4. Observe (optional)
celery -A scripts.pipeline.celery_app flower    # http://localhost:5555
```

## Config (env vars)

| Var | Default | Purpose |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379/2` | broker + result backend |
| `INSAR_CELERY_BROKER_URL` | = `REDIS_URL` | override broker only |
| `INSAR_CELERY_RESULT_BACKEND` | = `REDIS_URL` | override backend only |

DB index 2 keeps this isolated from any Weespas Redis (0/1).

## Scheduled refresh (Phase 3) — opt-in, default OFF

Sentinel-1 re-observes each AOI every ~12 days. A beat schedule can auto-refresh,
but it's **off unless explicitly enabled** — on a laptop or a synthetic-only
checkout a tick would just spawn failing HyP3 tasks. Enable only on an always-on
host wired to the real-data path:

```bash
INSAR_BEAT_ENABLED=1 \
INSAR_BEAT_AOIS=huruma,mombasa \   # optional; default = all registry AOIs
INSAR_BEAT_DAYS=12 \               # optional; default 12
  celery -A scripts.pipeline.celery_app beat --loglevel=info
```

The scheduled task is `insar.refresh_aoi` — the same chain, so it still halts at
the OpenSARLab MintPy gate and never fabricates data.

## Control API (Phase 3) — trigger + monitor

A small FastAPI control plane, **separate from the read-serving app** (which
stays a pure read process). Run it on its own port:

```bash
INSAR_ADMIN_TOKEN=... uvicorn scripts.pipeline.control_api:app --port 8001
```

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /health` | none | liveness + broker url |
| `POST /admin/refresh` | `X-Admin-Token` | enqueue refresh (`{aoi, track?, tracks?, from_sbas?}`) → `{task_id}` |
| `GET /admin/refresh/{id}` | none | task state / result / error |

Fail-closed: if `INSAR_ADMIN_TOKEN` is unset, `POST /admin/refresh` returns 503
(disabled), never open. Use `from_sbas: true` to resume after copying
OpenSARLab SBAS outputs back to disk (skips HyP3/clip/reproject → gate → join).
```
