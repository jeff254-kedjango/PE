# InSAR-Final-main — Performance Analysis & Celery/Redis Plan

_Date: 2026-06-22. Author brief: distinguished software / geospatial engineer
lens, with civil-engineering review of the real-estate implications._

This responds to the task: *improve InSAR-Final-main performance; consider
adding Celery + Redis; keep the O(1)-or-better discipline; keep in mind a future
Weespas ↔ InSAR integration.*

---

## 0. The honest headline (read this first)

Adding Celery and Redis to the **request-serving path** of this app would be a
mistake, and the architecture docs already locked against it for good reasons
("Stack is locked DuckDB + single FastAPI process"). Here's the truth about why,
stated plainly so we don't cargo-cult the Weespas stack into a system with a
completely different shape:

- **The serving path is already at the theoretical floor.** Every AOI's payload
  is precomputed once at startup into an in-RAM binary bundle
  (`_build_bundle`, `app/main.py:49`), hashed to an ETag, and served as a raw
  `memcpy` with 304 revalidation (`aoi_bundle`, `main.py:589`). That is O(1) per
  request and already faster than anything a Redis round-trip could give you —
  Redis would *add* a network hop and a serialization step to data that already
  lives in process memory.
- **There is no per-user, per-write, or fan-out workload** on the read side.
  Celery exists to move slow/unreliable work *out* of the request cycle. The
  read requests here are microseconds. There is nothing to move out.

So if we stopped there, the answer would be "no." **But that's the wrong place
to look.** The real heavy compute in this project is the **build-time
pipeline**, and *that* is where a task system genuinely belongs — and it becomes
close to mandatory the moment Weespas starts asking InSAR for risk on demand.

**Net recommendation:**
- ❌ Do **not** put Celery/Redis in front of the read endpoints or the bundle.
- ✅ Use **Celery (+ Redis as broker/result backend)** to turn the build-time
  pipeline — today a set of overnight shell scripts — into a managed,
  observable, retryable, **schedulable** job graph. This is what lets the system
  refresh itself every Sentinel-1 cycle (~12 days) and be triggered by an API
  call from Weespas.
- ✅ Use **Redis as a small read cache** *only* for the new dynamic surface the
  integration introduces (point/bbox "risk at this property" lookups), never for
  the static bundle.
- ✅ Separately, take the two real CPU hot spots (the per-building STL loop and
  the per-building scoring loop) from minutes to seconds. These are pure
  performance wins independent of any queue.

---

## 1. What this system actually is (and why it changes the answer)

| | Weespas | InSAR-Final-main |
|---|---|---|
| Workload | Live CRUD marketplace, per-user writes, OTP, analytics | Read-only viewer over a **precomputed** scientific dataset |
| Hot path | DB queries per request | `memcpy` of an in-RAM bytes blob |
| Heavy work | Image processing, SMS, analytics rollups (request-time) | InSAR processing (**build-time**, hours, offline) |
| Where a queue helps | Offload request-time side effects | Orchestrate the offline pipeline |

The two projects look similar (both FastAPI) but have **opposite performance
profiles**. Weespas needs Celery to get work *off* the request thread. InSAR has
already pushed *all* heavy work to build time; its request thread does nothing
slow. Copying the Weespas pattern verbatim would add moving parts, failure
modes, and latency for zero benefit on the read side.

### The serving path, audited
- `lifespan` (`main.py:474`) opens one read-only DuckDB connection, loads
  `spatial`, and pre-bakes every AOI bundle. **O(n_buildings) once at startup.**
- `aoi_bundle` (`main.py:589`) — dict lookup + ETag compare + `Response(bytes)`.
  **O(1).** This is the frontend's primary data path.
- `/buildings`, `/buildings/at-date`, `/buildings/{id}/timeseries`,
  `/risk-summary` — live DuckDB spatial queries. These are secondary (the bundle
  carries the bulk), but they *are* the only request-time compute and the only
  place a cache could ever matter (see §4, integration).

**Verdict:** the read side honors the O(1) bar. No change needed there, and
none recommended.

---

## 2. Where the real cost is: the build-time pipeline

From the pipeline map, the genuinely expensive, blocking, retryable stages are:

| Stage | Script | Nature | Runtime |
|---|---|---|---|
| HyP3 submit + **watch** + download | `scripts/hyp3_pipeline.py` | Network, polls, retry-forever | **2–24 h** |
| MintPy SBAS inversion | `scripts/mintpy_run.py` | CPU+RAM subprocess, self-healing anchor loop | **1–3 h** |
| GACOS submit/ingest | `scripts/fetch_gacos.py` | Network (async portal, human-in-loop email) | mins + wait |
| clip / reproject | `clip_to_common_grid.py`, `reproject_hyp3.py` | CPU (gdalwarp) | minutes |
| join + score | `scripts/join_insar.py`, `scripts/postprocess.py` | CPU | seconds–**minutes** |

Today these are wired together by **shell scripts** — `overnight_download.sh`
(with a Windows wake-lock!) and `_run_aoi_chain.sh`. **That shell orchestration
*is* an ad-hoc task queue** — just one with no retry semantics beyond what each
script hand-rolls, no central state, no observability, no scheduling, and a
hard dependency on a human babysitting an overnight run.

This is the legitimate home for Celery. Not because the scripts are slow to
*write* — they're well-built and idempotent — but because the **orchestration**
around them (retry, resume, schedule, trigger, observe) is exactly Celery's job,
and it's currently done by `bash` + a person.

> Caveat that must shape the design: MintPy/ISCE cannot run on the laptop
> (RAM + dependency tree) — the SBAS step runs on **ASF OpenSARLab**. So the
> orchestrator cannot simply `subprocess` MintPy locally. The Celery graph
> models the *whole* workflow but the SBAS node is a **submit-to-OpenSARLab +
> poll-for-completion** task, mirroring how `hyp3_pipeline` already watches HyP3.

---

## 3. The two real CPU hot spots (fix regardless of Celery)

The O(1)-or-better doctrine says "per-row Python is a regression." Two places
currently violate it, both build-time:

1. **Per-building STL decomposition** (`postprocess.py` `_stl_decompose`).
   statsmodels `STL` is called once per building; ~O(n · m²) with a Python loop
   over buildings. Minutes for ~1–10k buildings. **STL of one building is
   independent of every other** → embarrassingly parallel. `multiprocessing` /
   `joblib.Parallel` across cores turns minutes into seconds. (When the Celery
   pipeline exists, this becomes a Celery `group` / chord — fan out per building
   block, gather.)
2. **Per-building scoring loop** (`join_insar.py` `synthesize_env_index_rows`
   calls `composite_risk` per building). The arithmetic is O(1) per building but
   runs in a Python loop. It can be **vectorized** to operate on the whole numpy
   column set at once (the same pattern `aggregate_blocks`,
   `_trailing_velocity`, and `decompose_asc_desc` already use). This is the most
   "on-brand" fix for your stated discipline.

Already good (leave alone): `tilt_rate_from_velocity_field` uses a `cKDTree`
radius query (O(n log n) + O(n·k̄), **not** O(n²)); `decompose_asc_desc`,
`aggregate_blocks`, `_trailing_velocity` are fully vectorized; the bundle build
is O(n) once. Credit where due — the prior optimization pass held.

**Minor, optional:** the startup bundle build loops AOIs serially
(`main.py:489`); parallelizing it shortens *cold start* only. Low priority.

---

## 4. Where Redis genuinely fits — and only there

Redis is the right tool the moment the **Weespas integration** introduces a
*dynamic* read surface that the static bundle can't serve:

- Weespas listing at (lat, lon) → "what is the subsidence risk here?" This is a
  **point-in-polygon / nearest-building** query against InSAR, parameterized by
  arbitrary coordinates — i.e. not precomputable into a fixed per-AOI bundle.
- A Weespas search over a map viewport → bbox risk summary.

These map onto the existing `/buildings` and `/risk-summary` DuckDB queries,
which would now be hit with **unbounded, repeating** parameter sets from a
high-traffic marketplace. That is a textbook Redis cache:
`key = (aoi, rounded bbox / geohash)` → cached JSON, short TTL, invalidated when
the AOI's bundle ETag changes (we already content-hash every bundle — reuse that
hash as the cache-version namespace, so a data refresh auto-busts the cache).

**Until that integration exists, Redis has nothing to cache that isn't already
in RAM.** So Redis enters in two roles, both justified, neither on today's hot
path: (a) Celery broker/result backend for the pipeline, (b) read cache for the
integration's dynamic queries.

---

## 5. Civil / geospatial engineering review (this is a real-estate product now)

The moment subsidence risk is shown next to a property listing, the engineering
bar and the **liability** bar both rise. As the civil-engineering reviewer on
this, my hard constraints:

1. **Never let the integration imply a structural-safety verdict.** The model is
   explicitly a *screening/triage* signal (`risk_model.md`: it does **not**
   capture construction quality, the dominant Nairobi collapse driver; nor
   sudden events; nor age/material). A marketplace badge that reads "unsafe"
   where there is only an InSAR velocity anomaly is (a) civil-engineering false
   precision and (b) a defamation / property-devaluation legal exposure. The
   existing disclaimer — *"Subsidence indicator. Not a structural-safety
   determination. Ground inspection required."* — must travel with the data into
   Weespas, not be left behind in the InSAR UI.
2. **Carry uncertainty across the boundary.** The InSAR side already abstains on
   ~25% of buildings (the defensibility gate: coherence γ **and** linear-trend
   R²). The integration API must return the **tier + confidence + abstention**,
   never a bare number. "Insufficient evidence" is a valid, defensible answer and
   must render as such in Weespas — not as "no risk" (which is the dangerous
   misread).
3. **Differential settlement, not absolute sinking, is the structural signal.**
   The angular-distortion/tilt term encodes the civil reality that uniform
   settlement rarely cracks a frame, while *differential* settlement (angular
   distortion β; the classic Skempton–MacDonald / Bjerrum damage thresholds)
   does. Keep tilt **escalate-only** across the boundary too — InSAR cannot
   resolve true building-scale tilt, so it may raise a flag but must never
   downgrade one or be shown as a measured tilt angle.
4. **Spatial join honesty at the property level.** A Weespas pin resolves to a
   footprint via nearest-building/PIP. In dense informal settlements a single
   Sentinel-1 pixel (~5–20 m effective) may straddle several structures
   (`insar_pixel_share` already encodes this). The integration must surface
   "this estimate is shared across N footprints / 1 pixel" rather than imply
   per-building precision it doesn't have.

These are not blockers — they're the difference between a defensible product and
an actionable-but-wrong one. They mostly constrain the **API contract** between
the two systems, which is why §6 phases the integration last and behind a
deliberate schema.

---

## 6. Proposed plan (phased, each phase independently shippable)

**Phase 0 — Decide the deployment shape (no code).** Confirm: does the
orchestrator run on the laptop (driving OpenSARLab remotely) or on a small
always-on server? This determines whether Celery workers are local or hosted.
Recommendation: a single small Linux host (or the dev box) running
`redis` + one Celery worker + Celery beat — minimal ops, matches the "single
process" ethos by keeping the *serving* app untouched and isolating the queue.

**Phase 1 — CPU hot-spot wins (pure performance, no infra). ✅ DONE 2026-06-22.**
- **Measured first** (the right way round). On 1500 buildings × 24 months:
  `_stl_decompose` = **2756 ms (99.4%)**; the per-building scoring loop
  (`synthesize_env_index_rows`) = **17 ms (0.6%)**.
- **Decision reversed by the data:** do NOT vectorize the scoring loop. It is a
  17 ms life-safety code path that is readable and NaN-honest; refactoring it
  for a 0.6% gain trades real behavioral-drift risk for nothing. A distinguished
  engineer doesn't refactor working life-safety code for 15 ms.
- **Shipped:** parallelized `_stl_decompose` across processes (statsmodels' STL
  holds the GIL, so threads were measured 2× *slower* — must be processes; the
  building axis is split into one contiguous chunk per worker). Result:
  **2756 ms → 681 ms (4.0× faster)**, output **bit-identical** to the serial
  path (verified all 6 outputs equal vs a golden, both parallel and forced
  serial). `STL_WORKERS=1` forces serial; any pool failure falls back to serial.
- **Regression guard added:** `tests/test_stl_parallel.py` pins parallel==serial
  bit-for-bit + row-order preservation.
- **Side finding (out of scope, flagged):** `seed_synthetic.py:161` seeds RNG
  from `random.Random(hash(aoi.code))`; Python randomizes string `hash()`
  per-process, so synthetic data — and the `test_classification_invariants`
  suite — are non-deterministic between runs (2 of those tests fail
  independently of this change). Fix = seed from a stable hash
  (`hashlib`/`zlib.crc32`) or pin `PYTHONHASHSEED`. Not touched here.

**Phase 2 — Introduce Celery + Redis for the build pipeline (no serving change). ✅ DONE 2026-06-22.**
- Added `scripts/pipeline/` (celery_app + tasks + README) wrapping the existing
  idempotent scripts as subprocess tasks — no pipeline/scoring logic
  reimplemented. Tasks: hyp3_submit_watch, gacos_submit, clip, reproject,
  mintpy_gate, join, + composed `refresh_aoi` / `rebuild_from_sbas` chains.
- **OpenSARLab as a deterministic gate**: `mintpy_gate` verifies SBAS HDF5
  outputs exist and raises `AwaitingOpenSARLab` otherwise (no fabricated data,
  chain pauses cleanly for the human step).
- Network stages retry with backoff; CPU stages fail loud. Redis on DB index 2
  (isolated from Weespas 0/1), all env-overridable.
- **Serving app (`app/main.py`) source untouched — verified.** Redis/celery added
  to `requirements.txt` (worker-only).
- Tests: `tests/test_pipeline_tasks.py` (6, eager-mode, no broker/Redis/HyP3
  needed). Full InSAR suite now 64 passed / 0 failed.
- Also fixed (this session): seeder determinism (`seed_synthetic.py` now seeds
  from `zlib.crc32`, not per-process `hash()`), which stabilized the previously
  flaky classification-invariant tests; `.pytest_cache/` added to `.gitignore`.

Original Phase 2 plan, for reference:
- Add `celery`, `redis` to `backend/requirements.txt`; Redis as broker+backend.
- Model the existing scripts as idempotent Celery tasks, composed with
  `chain`/`group`/`chord`, preserving their current idempotency/cache-awareness:
  `search → submit_hyp3 → watch_hyp3 → download → clip → reproject →
  submit_mintpy(OpenSARLab) → poll_mintpy → fetch/ingest_gacos → join → score →
  atomic-swap demo.duckdb`.
- Replace the retry-forever hand-rolling with Celery `autoretry_for` +
  exponential backoff (keep the transient/non-transient classification already
  in `hyp3_pipeline._is_transient`).
- `overnight_download.sh` becomes a one-line `celery call` of the chain; the
  Windows wake-lock hack goes away (the worker host stays up).
- Observability via Flower (already a Weespas dependency — shared knowledge).

**Phase 3 — Scheduled + API-triggered refresh. ✅ DONE 2026-06-22.**
- `scripts/pipeline/schedule.py`: opt-in Celery beat (`INSAR_BEAT_ENABLED`,
  `INSAR_BEAT_AOIS`, `INSAR_BEAT_DAYS`; default ~12-day S1 cadence). **Empty
  unless enabled** — a default worker never auto-fires pipeline jobs (would just
  fail on a laptop with no real-data path).
- `scripts/pipeline/control_api.py`: a **separate** control-plane FastAPI app
  (NOT the read server) with `POST /admin/refresh` + `GET /admin/refresh/{id}`.
  Fail-closed auth (`INSAR_ADMIN_TOKEN`; 503 when unset, 401 on mismatch).
  Scheduled/triggered refresh still halts at the OpenSARLab gate — never
  fabricates data.
- **Synthetic↔real safety (added this session per requirement):** synthetic
  seeder now hard-skips AOIs flagged `insar` unless `--force`, so real data is
  never silently overwritten/mixed (`tests/test_seed_guard.py`).
- Tests: `tests/test_pipeline_phase3.py` (7). Full InSAR suite now 76 passed / 0
  failed. Serving app source (`app/main.py`) untouched throughout.

Original Phase 3 plan, for reference:
- Celery **beat**: every ~12 days per AOI, kick the chain to pull new Sentinel-1
  acquisitions and rebuild. The atomic `demo.duckdb` swap + content-hash ETag
  already make this safe against the live reader (the running FastAPI keeps
  serving its old handle; next bundle fetch is the new data).
- A thin authenticated `POST /admin/refresh?aoi=` enqueues a rebuild and returns
  a task id; `GET /admin/refresh/{id}` reports status. This is the seam Weespas
  (or an operator) calls.

**Phase 4 — Integration read surface + Redis cache (enables Weespas).**
- Define the **risk-at-point / risk-in-bbox** API contract carrying tier +
  confidence + abstention + disclaimer + pixel-share (per §5).
- Back it with the existing DuckDB spatial queries, cached in Redis keyed by
  `(aoi, geohash, bundle_etag)` with short TTL and ETag-namespaced invalidation.
- Weespas consumes it as a *screening badge with "inspection required"*, never a
  verdict.

**Out of scope / explicitly rejected:** Celery or Redis between the browser and
the bundle; Postgres/microservices for the serving app; any per-request InSAR
recompute.

---

## 7. Decision points I need from you

1. **Phase ordering / appetite.** Do you want the pure-performance Phase 1 first
   (fast, low-risk, on-brand), or go straight to standing up Celery/Redis
   (Phase 2)?
2. **Worker host.** Laptop-driven or a small always-on server? (Sets local vs.
   hosted workers, and whether beat-scheduling is even meaningful.)
3. **OpenSARLab automation.** Is programmatic submit/poll of the SBAS step on
   OpenSARLab feasible from your account, or does that step stay manual (i.e.
   the Celery chain pauses at a human gate)? This is the one genuinely hard
   integration question.
4. **Integration timing.** Is the Weespas↔InSAR risk API in scope now (pulls
   Phase 4 forward) or later (we stop at Phase 3 and the serving app is
   untouched)?
