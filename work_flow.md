# work_flow.md — Weespas ↔ InSAR: how the system runs, end to end

> **Scope.** This document explains the *whole* two-product system as it exists today
> (2026-06-22): what each process is, how data flows between them, where the security
> exposure lives, what to build next, and the exact commands to install and run every
> process locally on one PC across several ports/terminals.
>
> Two codebases, one system:
> - **InSAR** (`PE/InSAR-Final-main/`) — the subsidence/collapse-risk *producer*. Reads
>   satellite radar, scores every building, serves a read-only risk API + a map UI.
> - **Weespas** (`PE/weespas/`) — the property marketplace and the *front door*: owns
>   identity, roles, listings, the verification badge, and the engineer/authority
>   "structural flag" intake. It calls InSAR; InSAR never calls it.

---

## 1. The one-paragraph mental model

InSAR can see the **ground move** but is physically blind to **construction quality**
(bad concrete, missing rebar, illegal extra floors) — which is the dominant reason
Nairobi buildings collapse, and why ~85 % of buildings can be unsafe while showing little
motion. So the system fuses **two sensors**: InSAR's motion score, and a human
**structural flag** recorded by a certified engineer or an authority inside Weespas. An
engineer records a flag → Weespas exports it to a file InSAR reads → Weespas pings InSAR's
control API → InSAR re-scores that area (debounced) → the building's collapse score and
danger tier update. The fusion is deliberately **one-directional in risk**: an `UNSAFE`
flag *raises* a floor regardless of motion; absence of a flag never lowers risk; a
`CLEARED` flag damps risk but decays over 2 years and can never silence an accelerating
mover (that path is where bribery/corruption would attack, so it is the most constrained).

---

## 2. Architecture — the processes and how they talk

```
                              ┌───────────────────────────────────────────────┐
   Browser (engineer,         │                  ONE PC                        │
   agent, owner, tenant)      │                                               │
        │                     │   ┌──────────────────┐   ┌──────────────────┐ │
        │  https              │   │ Weespas FRONTEND │   │  InSAR FRONTEND   │ │
        ├────────────────────▶│   │ React+Vite :5174 │   │ React+Vite :5173  │ │
        │                     │   └────────┬─────────┘   └────────┬──────────┘ │
        │                     │            │ /api/v1              │ /api        │
        │                     │            ▼                      ▼             │
        │                     │   ┌──────────────────┐   ┌──────────────────┐  │
        │  REST               │   │ Weespas BACKEND  │   │  InSAR READ APP   │  │
        ├────────────────────▶│   │ FastAPI  :8000   │   │ FastAPI  :8000*   │  │
        │                     │   │ (uvicorn)        │   │ (uvicorn)         │  │
        │                     │   └───┬─────────┬────┘   └─────────┬─────────┘  │
        │                     │       │         │                  │            │
        │                     │       │ enqueue │ POST /admin/     │ read-only  │
        │                     │       ▼         │ request-rebuild  ▼            │
        │                     │  ┌─────────┐    │            ┌───────────────┐  │
        │                     │  │ Weespas │    │            │  demo.duckdb  │  │
        │                     │  │ CELERY  │    └──────────▶ │ (file, atomic │  │
        │                     │  │ workers │       ┌─────────┤  swap on build│  │
        │                     │  │ ×4 queue│       │         └───────────────┘  │
        │                     │  └────┬────┘       ▼                            │
        │                     │       │      ┌──────────────┐  ┌─────────────┐  │
        │                     │       │      │ InSAR CONTROL│  │ InSAR build │  │
        │                     │       │      │ API  :8001   │─▶│ CELERY work │  │
        │                     │       │      └──────────────┘  │ (pipeline)  │  │
        │                     │       │                        └──────┬──────┘  │
        │                     │       ▼                               ▼         │
        │                     │  ┌─────────────────┐         flags JSON file    │
        │                     │  │ Redis (one srv) │◀────────data/structural_   │
        │                     │  │  /0 cache       │         flags/<aoi>.json    │
        │                     │  │  /1 weespas cel │            ▲                │
        │                     │  │  /2 insar cel   │            │ export         │
        │                     │  └─────────────────┘   ┌────────┴────────┐       │
        │                     │  ┌─────────────────┐   │ Weespas writes  │       │
        │                     │  │ PostgreSQL      │   │ flag → exports  │       │
        │                     │  │ (weespas: users,│   │ → triggers      │       │
        │                     │  │ listings, flags,│   │ rebuild         │       │
        │                     │  │ audit chain)    │   └─────────────────┘       │
        │                     │  └─────────────────┘                            │
        │                     └───────────────────────────────────────────────┘
```

`*` **Port collision (important):** both `weespas/main.py` and
`InSAR-Final-main/backend/app/main.py` default to **8000**. On one PC you MUST move one of
them. The recommended local port map is in §6.

### 2.1 The processes (what each one is and why it's separate)

| # | Process | Tech | Default cmd | Port | Why it's its own process |
|---|---------|------|-------------|------|--------------------------|
| 1 | **Weespas backend** | FastAPI/uvicorn | `uvicorn main:app` | 8000 | System of record; owns auth, listings, flag intake. |
| 2 | **Weespas Celery (×4 queues)** | Celery 5 | `scripts/run_workers.sh` | — | Offloads OTP/analytics/feeds/media so a slow job never delays an OTP. |
| 3 | **Weespas Beat** (optional) | Celery beat | `celery -A core.celery_app beat` | — | Scheduled cache-warm/analytics; off by default. |
| 4 | **Weespas frontend** | React+Vite (Leaflet, Recharts, TanStack Query) | `npm run dev -- --port 5174` | 5174 | Marketplace + role-gated admin/stats dashboards; talks to #1 at `/api/v1` with the `weespas_session` cookie. Path: `PE/weespas-frontend/`. |
| 5 | **InSAR read app** | FastAPI/uvicorn | `uvicorn app.main:app` | 8000→**8002** | Pure read-only risk API over `demo.duckdb`. **Must stay untouched.** |
| 6 | **InSAR control API** | FastAPI/uvicorn | `uvicorn scripts.pipeline.control_api:app` | 8001 | Operator/automation seam: enqueues rebuilds. Auth-gated. |
| 7 | **InSAR build Celery** | Celery 5 | `celery -A scripts.pipeline.celery_app worker` | — | Heavy pipeline (clip/reproject/MintPy/join), 1 at a time. |
| 8 | **InSAR frontend** | React+Vite (deck.gl/MapLibre) | `npm run dev` | 5173 | Risk map UI; talks to #5. |
| 9 | **Redis** | redis-server | `redis-server` | 6379 | Shared infra; namespaced by DB index (see below). |
| 10 | **PostgreSQL** | postgres 14+ | system service | 5432 | Weespas durable store + the append-only audit chain. |

**Why so many processes?** Separation of *failure domains* and *scaling profiles*: a
60-second analytics job must never block a 200 ms OTP; the heavy InSAR pipeline (minutes–
hours, multi-core) must never share a box's CPU with the latency-sensitive read API; and
the read API stays a dumb, stateless, horizontally-scalable reader so it can be replicated
behind a load balancer without touching the producer.

### 2.2 Data stores (and why exactly these)

- **PostgreSQL — Weespas system of record.** Users, roles, listings, `building_link`
  (listing→footprint mapping), `structural_flag` (the manual-entry sensor),
  `notification_audit` (**hash-chained, append-only** — the legal evidence spine).
- **DuckDB (`InSAR-Final-main/backend/data/demo.duckdb`) — InSAR analytical read replica.**
  A single ~MB file of views over scored GeoParquet. The build **atomic-swaps** it
  (write temp → rename), so the live read app keeps serving its old handle and the next
  connection sees fresh data — lock-free, no downtime.
- **Redis — ephemeral only, namespaced by DB index so the two products never share
  keyspace on one server:** `/0` Weespas cache, `/1` Weespas Celery broker+backend,
  `/2` InSAR build Celery broker+backend, `/3` reserved for the future P4b risk-cache.
- **Flag handoff file (`data/structural_flags/<aoi>.json`).** Deliberately a *file*, not a
  cross-DB query: it decouples the two products (InSAR can rebuild from a frozen snapshot
  with Weespas down) and keeps the InSAR build free of a Postgres dependency.

---

## 3. The end-to-end flow that ties it together (the flag → rebuild loop)

This is the spine that was just completed. Step by step:

1. **Record.** A `professional` (engineer) or `authority` user `POST /api/v1/structural-flags`
   on Weespas. `structural_flag_service.record_flag` validates the role + state (only an
   authority may set `AUTH_UNSAFE`) and writes a row to Postgres with `granted_by` = caller.
2. **Export.** The router calls `structural_flag_export.export_aoi(db, aoi)` →
   writes/overwrites `<INSAR_FLAGS_EXPORT_DIR>/<aoi>.json` **atomically** (temp+rename), in
   the exact shape `scripts/structural_flags.fetch_structural_flags()` reads.
   Latest-judgement-wins (by `observed_at`, then `created_at`).
3. **Trigger (best-effort).** Only if the file was actually written,
   `structural_flag_export.trigger_rebuild(aoi)` does a short-timeout `urllib` POST to
   InSAR's `POST /admin/request-rebuild` with the `X-Admin-Token`. **It never raises** — if
   InSAR is down the flag is already durably recorded; the failure is logged and an operator/
   scheduled rebuild picks it up. No URL/token configured ⇒ silent no-op.
4. **Debounce.** InSAR `request_rebuild` bumps a per-AOI Redis token (`incr`) and schedules
   `_debounced_rebuild` after `INSAR_REBUILD_DEBOUNCE_SECONDS` (default 120 s). A burst of
   flags collapses to **one** rebuild — the deferred run only proceeds if its token is still
   the latest (else a newer request superseded it). No Redis ⇒ run-now.
5. **Provenance-aware rebuild.** `rebuild_aoi` checks `get_provenance(aoi)`:
   - `insar` (real data) → **re-JOIN** (`mintpy_gate → join`). The synthetic seeder *refuses*
     real-data AOIs, so re-seeding would silently drop the flag — this is a correctness gate.
   - `synthetic`/`partial` → **re-SEED** that one AOI via `scripts/reseed_one.py`.
6. **Re-score + swap.** The join/seed recomputes `composite_risk()` (the [0,1] collapse
   score) and `danger_level()` (the absolute STABLE…CRITICAL tier) *with the flag fused*, then
   atomic-swaps `demo.duckdb`. The read app and map now show the escalation.

**Fusion guarantees (locked, and why they matter):**
- `UNSAFE` → floor 0.85; `AUTH_UNSAFE` → floor 0.95 (applied **after** the motion protect
  floor). Absence (`STRUCT_NONE`) is the default and leaves the score **byte-identical** to
  today — the whole feature ships *inert* until a real flag exists.
- `CLEARED` damps at most 0.35, decays to 0 over 730 days, and is cancelled by motion — and
  is applied **before** the protect floor, so it can **never** hide a PLASTIC/accelerating
  building. This is the anti-corruption property: a bribed "all clear" cannot silence a real
  collapse signal.
- Notifications (future, P4c) fire off the **tier + confidence**, never the raw %, because
  the score is a ranking signal, not a calibrated probability.

---

## 4. Security concerns — where data leaks / hostile attacks / hacking could happen

Mapped to the actual surfaces in this codebase, with current state and the gap.

### 4.1 Authentication & secrets
- **JWT signing key + DB password are now mandatory env vars** (`SECRET_KEY`,
  `DATABASE_URL`) — the app fails fast if absent (they used to be hard-coded literals; that
  was the single worst leak and is fixed). **Action:** never commit a real `.env`; rotate
  `SECRET_KEY` only deliberately (it logs everyone out). Keep `.env` in `.gitignore`.
- **InSAR control-API token** (`INSAR_ADMIN_TOKEN`) is a single shared bearer in
  `X-Admin-Token`. It is **fail-closed**: unset ⇒ the mutating endpoints return 503 (refuse),
  wrong ⇒ 401. **Gap:** it's a static shared secret — fine on a private host, but it must
  never be exposed on a public interface. Bind the control API to `127.0.0.1` locally.

### 4.2 The corruption / "owner bribes official, tenants die" threat (the product's core)
- The **append-only `notification_audit` table** is enforced at the database with a
  `BEFORE UPDATE OR DELETE OR TRUNCATE` trigger + a `REVOKE UPDATE,DELETE` from the app role,
  and is **hash-chained** (`prev_hash`→`row_hash`). Tampering breaks the chain detectably and
  the DB itself rejects edits. **This is the legal evidence spine — do not "optimize" it away.**
- The **`CLEARED` damp is bounded, decaying, and motion-overridable** precisely so a corrupt
  clearance cannot bury a real signal (see §3). The risk path that corruption attacks is the
  most constrained path in the system, by design.

### 4.3 Injection / data integrity
- All DB access is via SQLAlchemy ORM / parameterized queries — no string-built SQL in the
  request path. **Keep it that way**; the audit-chain trigger is the only raw SQL and it is
  migration-managed.
- The **listing→footprint resolver is 3-state** (`monitored` / `not monitored` /
  `unavailable`) and **never collapses "unknown" into "safe"** — a missing match is reported
  honestly, never silently downgraded to low risk.
- **Flag intake is role-gated twice:** `require_certifier` (professional/authority/staff/
  admin) at the route, and a second check in the service so only an authority can assert
  `AUTH_UNSAFE` or `source='authority'`. `FLAG_NONE` is not recordable.

### 4.4 Cross-process / file-handoff surface
- The **flag JSON file** is written atomically and read fail-safe (absent/malformed ⇒ all
  `STRUCT_NONE`, never auto-cleared). **Gap to watch:** treat
  `INSAR_FLAGS_EXPORT_DIR` as a trust boundary — only Weespas should be able to write it and
  only InSAR read it; set filesystem perms accordingly (don't world-write it).
- **`trigger_rebuild` never blocks or fails a flag write** — a DoS'd or down InSAR cannot
  prevent the durable, legally-relevant flag from being recorded. Good. **Gap:** the trigger
  POST has no rate limit of its own beyond the 120 s debounce; behind the debounce it's safe,
  but expose the control API only on localhost / private network.

### 4.5 Network exposure & CORS
- The InSAR read app's CORS allow-list is currently `localhost:5173` / `localhost:3000`.
  **Action before any non-local deploy:** tighten CORS to the real frontend origin, put
  every backend behind TLS, and never expose the **control API (:8001)** or **Redis (:6379)**
  or **Postgres (:5432)** to the public internet — bind them to localhost/private subnet.
- **Redis has no auth by default.** On a shared/exposed host, set `requirepass` and use
  separate ACLs; the DB-index namespacing prevents *accidental* key collisions but is **not**
  a security boundary.

### 4.6 Denial-of-service / resource exhaustion
- Heavy InSAR pipeline is **concurrency=1** and **idempotent** (re-runs are no-ops on
  computed files) + `acks_late` + `reject_on_worker_lost`, so a killed worker re-queues rather
  than drops or double-runs. The debounce caps rebuild frequency.
- **Gap:** add request rate-limiting / a WAF in front of the public Weespas API; add Celery
  task time-limits (`task_time_limit`) so a hung pipeline stage can't pin a worker forever.

### 4.7 Quick "do this before exposing beyond localhost" checklist
1. Move secrets to a secrets manager; rotate the real `SECRET_KEY` and `INSAR_ADMIN_TOKEN`.
2. Bind control API, Redis, Postgres to localhost/private; TLS on the two public APIs.
3. Tighten CORS to real origins; add API rate-limits + Celery time-limits.
4. Set `requirepass` on Redis; least-privilege the DB app role (it already lacks
   UPDATE/DELETE on the audit table — keep that).
5. Lock filesystem perms on `INSAR_FLAGS_EXPORT_DIR`.
6. `cookie_secure=true` in prod (HTTPS-only session cookie).

---

## 5. What future updates should look like (the roadmap, already scoped)

Build order is deliberate; each phase is independently shippable and safe.

- **P4b — Risk-at-point API + Redis read-cache.** A `GET risk?lat&lon` (and bbox) endpoint
  on the InSAR side, cached in Redis `/3` (the reserved index). Lets Weespas show a live risk
  badge on a listing page without hammering DuckDB. *Pure read; no scoring change.*
- **P4c — Notification delivery + audit population + ACK/auto-escalation.** Wire the
  `notification_audit` chain to actual sends (SMS via Africa's Talking / email). Route off the
  **danger tier + confidence**, not raw %. Implement the escalating ladder
  (owner → +authority → +tenants as risk climbs) with delivery receipts and auto-escalation
  if an owner doesn't ACK. This is the piece that operationally defeats the bribery failure
  mode — tenants get told directly.
- **P4d — Authority/tenant tiers behind real-data calibration + a legal recipient registry.**
  Do not notify authorities/tenants until the score is calibrated on real data and there is a
  vetted, lawful recipient list (notifying the wrong person is itself a harm).
- **Auto-feed ingestion.** Today flags are manual entry; the table + loader are already shaped
  so an automated feed (OPRS enforcement notices, NCA records) plugs into the *same*
  `record_flag` service with no schema change.
- **Engineering hygiene going forward:** keep the read app (`app/main.py`) untouched and
  stateless; keep every fusion change **inert-by-default** and prove it against the golden SHA
  (a no-flag build must stay byte-identical); add new infra to Redis as a new index, not a new
  server; maintain O(1)-per-building scoring (no new per-building loops).

> **⚠️ End-of-project: data-handling discussion (TODO before launch).** Once the feature build
> settles, we must have a dedicated discussion on how data is handled across the whole system.
> Open questions to resolve there (not exhaustive): data **ownership & retention** (how long we
> keep listings, telemetry, InSAR-derived flags, M-Pesa/settlement records, and the append-only
> audit chain); **PII & consent** under Kenya's Data Protection Act 2019 (user identity, phone
> numbers, location traces, the listing↔unsafe-building joins flagged in §4.2/§9.7);
> **subject-access / deletion** requests vs the immutability of receipts and the hash-chained
> audit log (these intentionally cannot be edited — reconcile that with erasure rights);
> **cross-service data flow** (what identity/telemetry crosses the Weespas↔commerce↔InSAR
> boundaries and how it's minimised); **seed vs production data** (the placeholder seed media /
> profiles introduced during dev must be purged or clearly partitioned before real users);
> and **backups / disaster recovery / breach response**. Raised 2026-06-30 — revisit at the
> pre-launch hardening pass.

---

## 6. Install & run locally on one PC — every process, every port

> **Platform note.** Developed on WSL2/Linux, Python **3.10**, Node 18+. PostgreSQL 14+ and
> Redis must be running. Each numbered block below is a **separate terminal** (a separate
> "portal"). The InSAR read app and Weespas backend both default to 8000 — we move InSAR's
> read app to **8002** locally to avoid the clash.

### 6.0 Local port map (resolve the 8000 collision)

| Process | Port | Terminal |
|---------|------|----------|
| Weespas backend (uvicorn) | **8000** | A |
| Weespas Celery workers | — | B |
| Weespas frontend (Vite) | **5174** | C |
| InSAR read app (uvicorn) | **8002** | D |
| InSAR control API (uvicorn) | **8001** | E |
| InSAR build Celery worker | — | F |
| InSAR frontend (Vite) | **5173** | G |
| Redis | 6379 | (service) |
| PostgreSQL | 5432 | (service) |

### 6.1 One-time: system services

```bash
# Redis (Debian/Ubuntu/WSL)
sudo apt-get update && sudo apt-get install -y redis-server postgresql
sudo service redis-server start
sudo service postgresql start

# Create the Weespas database + a role (adjust names to taste)
sudo -u postgres psql -c "CREATE USER weespas WITH PASSWORD 'change-me';"
sudo -u postgres psql -c "CREATE DATABASE weespas_db OWNER weespas;"
# PostGIS/geometry types are used by listings — enable the extension:
sudo -u postgres psql -d weespas_db -c "CREATE EXTENSION IF NOT EXISTS postgis;"
```

### 6.2 One-time: Python environments

> Note: in this dev box a single shared venv at `PE/weespas/.venv` happens to satisfy both
> projects. For clarity below, each project gets its own venv — but you can reuse one.

```bash
# ---- InSAR backend ----
cd /home/jeff/PE/InSAR-Final-main/backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
deactivate

# ---- Weespas backend ----
cd /home/jeff/PE/weespas
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
deactivate
```

### 6.3 One-time: environment files

**Weespas `.env`** (copy from `.env.example`, fill in):
```bash
cd /home/jeff/PE/weespas && cp .env.example .env
```
Set at minimum:
```ini
DATABASE_URL=postgresql://weespas:change-me@localhost:5432/weespas_db
SECRET_KEY=<generate a long random string>
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/1
CELERY_RESULT_BACKEND=redis://localhost:6379/1
# ---- P4a InSAR integration (turn the loop ON) ----
INSAR_DUCKDB_PATH=/home/jeff/PE/InSAR-Final-main/backend/data/demo.duckdb
INSAR_MATCH_RADIUS_M=30
INSAR_FLAGS_EXPORT_DIR=/home/jeff/PE/InSAR-Final-main/backend/data/structural_flags
INSAR_CONTROL_API_URL=http://localhost:8001
INSAR_ADMIN_TOKEN=<same value you export for the InSAR control API>
INSAR_CONTROL_TIMEOUT_S=3
```
**InSAR** reads its config from environment variables (no `.env` file). Export in the
terminals that run the control API + build worker (see 6.5/6.6):
```ini
INSAR_ADMIN_TOKEN=<same token as Weespas above>   # required for control API to accept rebuilds
REDIS_URL=redis://localhost:6379/2                # InSAR Celery uses index /2
INSAR_REBUILD_DEBOUNCE_SECONDS=120                # optional; default 120
```

### 6.4 One-time: seed data + DB migrations

```bash
# InSAR: generate synthetic scored data + build demo.duckdb (~10s)
cd /home/jeff/PE/InSAR-Final-main/backend && source .venv/bin/activate
python -m scripts.seed_synthetic
deactivate

# Weespas: apply the non-destructive Alembic migration (creates the 3 P4a tables
# incl. the append-only audit trigger) then seed listings/users
cd /home/jeff/PE/weespas && source .venv/bin/activate
alembic upgrade head
bash setup_db.sh        # runs seed.py / seed_expanded.py / stats
deactivate
```

### 6.5 Run — Terminal A · Weespas backend (:8000)

```bash
cd /home/jeff/PE/weespas && source .venv/bin/activate
uvicorn main:app --reload --port 8000
```

### 6.6 Run — Terminal B · Weespas Celery (4 queue workers)

```bash
cd /home/jeff/PE/weespas && source .venv/bin/activate
bash scripts/run_workers.sh           # auth / analytics / feeds / media,default
# (optional) Beat scheduler, only if you enabled scheduled tasks:
# celery -A core.celery_app beat --loglevel=info
```

### 6.7 Run — Terminal C · Weespas frontend (:5174)

> The Weespas frontend is a **separate React 18 + Vite + TypeScript SPA** at
> **`/home/jeff/PE/weespas-frontend/`** (React Router, TanStack Query, **Leaflet** maps,
> Recharts). It talks to the Weespas backend at `VITE_API_BASE_URL` (default
> `http://127.0.0.1:8000/api/v1`) and **relies on the `weespas_session` cookie** — its
> `fetchJson` sends `credentials: 'include'` on every call, so the backend and frontend
> must be same-site or CORS+cookies configured. Vite pins no port (defaults to 5173, which
> **clashes with the InSAR frontend** — run Weespas on 5174):
```bash
cd /home/jeff/PE/weespas-frontend
cp .env.example .env 2>/dev/null || true   # set VITE_API_BASE_URL=http://127.0.0.1:8000/api/v1
npm install
npm run dev -- --port 5174                  # http://localhost:5174
```
> Routes: `/` (home feed + map + shorts), `/login` `/register` (phone/email + OTP),
> `/profile`, `/favorites`, `/agents`, and the **role-gated dashboards**: `/stats`
> (agent/staff/admin), `/staff` (staff/admin), `/admin` (admin). See §9 for how these and
> the analytics layer relate to InSAR.

### 6.8 Run — Terminal D · InSAR read app (:8002 to avoid the 8000 clash)

```bash
cd /home/jeff/PE/InSAR-Final-main/backend && source .venv/bin/activate
uvicorn app.main:app --reload --port 8002
# sanity: curl -s localhost:8002/health
```

### 6.9 Run — Terminal E · InSAR control API (:8001)

```bash
cd /home/jeff/PE/InSAR-Final-main/backend && source .venv/bin/activate
export INSAR_ADMIN_TOKEN=<same token as Weespas .env>
export REDIS_URL=redis://localhost:6379/2
uvicorn scripts.pipeline.control_api:app --port 8001
```

### 6.10 Run — Terminal F · InSAR build Celery worker

```bash
cd /home/jeff/PE/InSAR-Final-main/backend && source .venv/bin/activate
export REDIS_URL=redis://localhost:6379/2
celery -A scripts.pipeline.celery_app worker --loglevel=info --concurrency=1
```

### 6.11 Run — Terminal G · InSAR frontend (:5173)

```bash
cd /home/jeff/PE/InSAR-Final-main/frontend
npm install
npm run fetch:pmtiles      # one-time: basemap tiles
npm run dev                # http://localhost:5173 (proxies /api → :8000 by default)
```
> The Vite dev proxy targets `http://localhost:8000`. Since we moved the read app to **8002**,
> either change `vite.config.ts` proxy target to `:8002`, or run the read app on 8000 and move
> Weespas instead. Pick one canonical mapping and keep it.

### 6.12 Smoke-test the whole loop (proves §3 works)

```bash
# 1. Read app is serving risk
curl -s localhost:8002/health

# 2. Get a JWT for an engineer/staff user (see Weespas auth docs), then record a flag:
curl -s -X POST localhost:8000/api/v1/structural-flags \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"aoi_code":"huruma","insar_building_id":100000,"state":2,"source":"engineer","observed_at":"2026-06-22"}'

# 3. This writes data/structural_flags/huruma.json AND fires a debounced rebuild via :8001.
#    Watch Terminal F (build worker) — ~120s later it re-seeds/re-joins huruma.
ls -l /home/jeff/PE/InSAR-Final-main/backend/data/structural_flags/

# 4. After the rebuild swaps demo.duckdb, that building reads as HIGH/CRITICAL with
#    composite_risk ≥ 0.85 on the read app / map.
```

> **Reset to a clean baseline** (the no-mixing rule — never leave synthetic + real flags
> mixed): delete the export dir and re-seed, which restores the golden byte-identical scores.
> ```bash
> rm -rf /home/jeff/PE/InSAR-Final-main/backend/data/structural_flags
> cd /home/jeff/PE/InSAR-Final-main/backend && source .venv/bin/activate && python -m scripts.seed_synthetic
> ```

---

## 7. Tests (how to verify a change didn't regress)

```bash
# InSAR (currently 131 passing) — from backend/
cd /home/jeff/PE/InSAR-Final-main/backend && source .venv/bin/activate
python -m pytest -q

# Weespas (currently 46 passing) — from weespas/
cd /home/jeff/PE/weespas && source .venv/bin/activate
python -m pytest -q -p no:warnings
```

**Golden rule for any scoring change:** a build with *no flags present* must produce
**byte-identical** `composite_risk`/`danger_level` vs the golden — the fusion ships inert.
Prove it (re-seed, compare SHA) before and after.

---

## 8. Process-to-server mapping when you outgrow one PC

The process boundaries above *are* the deployment boundaries. A sensible multi-host split:

| Server | Runs | Scaling driver |
|--------|------|----------------|
| **web-1** (replicable behind LB) | Weespas backend + InSAR read app | stateless, scale by replicas |
| **worker-weespas** | Weespas Celery (auth/analytics/feeds/media) + Beat (single) | queue depth; one Beat only |
| **worker-insar** | InSAR build Celery (concurrency=1) + control API | CPU/disk; the heavy box |
| **data** | PostgreSQL (primary + read replica) | durability; audit chain lives here |
| **cache** | Redis (with `requirepass`, separate ACLs) | memory; namespaced by index |
| **edge/CDN** | Both Vite frontends built (`npm run build`) → static hosting | bandwidth |

Keep the InSAR read app stateless and the build worker isolated: the read tier scales by
adding replicas behind a load balancer; the producer scales vertically (it assumes sole use
of its box) and never shares CPU with the latency-sensitive readers.

---

## 9. Whole-system integration — the discussion (data flow, users, data collection)

> This section is the design map for joining the **four** codebases into one product:
> `weespas` (backend), `weespas-frontend`, `InSAR-Final-main/backend`, `InSAR-Final-main/
> frontend`. It is written from a full read of all four (not guessed). Read §9.1 first; it
> states the single most important asymmetry.
>
> **✅ Reconciliation note (2026-06-24): the "almost none of it exists" status is stale.**
> The integration spine and the primary user-visible surface are now BUILT. See the
> **§9.8 status table** below for the per-item state. In short — SHIPPED: scoped-JWT identity
> bridge, listing→footprint resolver (3-state), the **risk pill** on `PropertyDetails`
> (Option B), the **deep-link** to the InSAR map (Option A), `insar_building_view`/`export`
> telemetry into the Weespas plane, and the §8 company-detection soft-gate. **Update
> 2026-06-25: the two items previously listed as STILL OPEN — the flag-entry UI
> (`StructuralFlagModal`) and the staff risk tile (`RiskTileCard`) — are in fact also
> SHIPPED and unit-tested; the §9.8 status table is authoritative.**
> One design divergence to note: **P4b shipped as a Weespas-side resolver endpoint**
> (`GET /insar/listing/{id}/risk` reading the InSAR DuckDB directly), **not** as a new
> InSAR `/api/risk` endpoint — so the Redis `/3` cache reserved for it is currently unused.

### 9.1 The core asymmetry: Weespas has identity + telemetry; InSAR has neither

| Capability | Weespas (FE + BE) | InSAR (FE + BE) |
|------------|-------------------|------------------|
| User identity / login | **Yes** — JWT in `localStorage` (`weespas_token`/`weespas_user`) + `AuthContext` | **None** — no auth, no token, no cookie, no user concept anywhere |
| Per-visitor session | **Yes** — `weespas_session` HttpOnly cookie, minted by `middleware/session.py`, one `user_sessions` row per visitor | **None** — every page load starts fresh; bundle cache is an in-memory `useRef` Map lost on reload |
| Behavioral telemetry | **Yes** — `property_view_events`, `search_logs`, `favorites`, `property_dismissals`, geo, engagement | **Zero** — no analytics, no event reporting, no GA/PostHog, no localStorage |
| Roles / permissions | **Yes** — user/agent/staff/admin (+ professional/property_owner/tenant/authority) | **None** — all AOIs + bundles served publicly |
| Frontend shape | Multi-page React Router SPA, Leaflet, Recharts, TanStack Query | **Single-page** read-only deck.gl/MapLibre map; no router, no global store |

**Consequence (the whole integration hinges on this):** Weespas is the **identity + telemetry
plane**; InSAR is a **stateless risk-rendering plane**. So the right pattern is: *Weespas owns
the user, the session, and the analytics; InSAR stays a pure renderer that Weespas embeds or
calls.* We should **not** rebuild auth/sessions inside InSAR — we should let Weespas's session
cookie + JWT be the single source of identity and have InSAR ride on it where needed.

### 9.2 How a user moves through the joined system (target data flow)

```
 Visitor → weespas-frontend (:5174)
    │   weespas_session cookie minted by Weespas BE middleware on first request
    │   (anonymous row in user_sessions; one-time linked to user_id on login)
    │
    ├── browses listings ─────────────▶ Weespas BE  /properties (personalized feed)
    │        every view/search/favorite ──▶ property_view_events / search_logs / favorites
    │        (the telemetry that powers /analytics + the "For You" ranker)
    │
    ├── opens a listing detail ───────▶ Weespas BE resolves listing→InSAR footprint
    │        (services/insar_resolver.py: building_link, 3-state monitored/not/unavailable)
    │        └─▶ shows a RISK PILL (✅ built: GET /insar/listing/{id}/risk + RiskPill.tsx)
    │
    ├── clicks "View on risk map" ────▶ InSAR frontend (:5173) opened with ?aoi=<code>
    │        &building=<id> (deep-link). InSAR renders the map; NO login needed.
    │
    └── engineer/authority user ──────▶ records a STRUCTURAL FLAG (role-gated UI, P4c-ish)
             POST /api/v1/structural-flags → export JSON → debounced InSAR rebuild (§3)
             → that building's danger tier rises on the InSAR map for everyone.
```

The backend spine for the right-hand side (resolver, flag intake, export→rebuild loop) **is
built** (P4a), and the read-path (P4b, as a Weespas-side endpoint) + the listing risk pill +
the deep-link are built too. The **engineer/authority flag-entry UI** (bottom branch) is also
built (`StructuralFlagModal`, mounted in `PropertyDetails`) — see the §9.8 status table, all
six steps now SHIPPED as of 2026-06-25.

### 9.3 Three ways to surface InSAR inside Weespas (recommendation)

| Option | What it is | Pros | Cons | Verdict |
|--------|-----------|------|------|---------|
| **A. iframe embed** | Host built InSAR SPA at `/insar/`, embed with `<iframe src="/insar?aoi=...">` | Fastest; zero coupling; InSAR stays stateless | Iframe styling/communication friction; two map stacks loaded | Good for **v1 "View full risk map"** deep-link |
| **B. Risk badge via API** | Weespas BE serves a risk pill from `GET /insar/listing/{id}/risk` (reads InSAR DuckDB via the resolver), rendered on the listing detail | Native look; no iframe | — | ✅ **SHIPPED** (the recommended everyday surface). Note: built as a Weespas-side endpoint, not an InSAR `/api/risk`; Redis `/3` unused. |
| **C. Full port of InSAR layers into weespas-frontend** | Re-implement deck.gl risk layers inside the Weespas SPA | One unified app | Large effort; duplicates a 1184-line map; two teams' code merges | Later, if ever |

**Recommendation:** **B for the listing experience** (a risk pill + "monitored/not monitored"
honesty, fed by P4b + the existing `building_link` resolver), and **A for the deep-dive**
("View on risk map" opens the InSAR SPA deep-linked to the AOI + building). C is a future
consolidation, not now. This keeps InSAR stateless and avoids forking its map.

### 9.4 Should InSAR get sessions? — targeted, not wholesale

InSAR today is deliberately public and anonymous, and for the **map view** that's fine
(public-good subsidence data; no PII). Do **not** bolt a full auth system onto the InSAR SPA.
But two integration needs do require identity, and both are best solved **without** giving
InSAR its own login:

1. **Telemetry on InSAR usage** (you asked about collecting data for personalization/
   optimization). InSAR currently reports nothing. The clean fix: when InSAR is **embedded by
   or deep-linked from Weespas**, pass a short-lived signed context (the `weespas_session` id
   or a scoped token) so the InSAR read app can emit view events **into Weespas's existing
   telemetry tables** (e.g. a new `insar_view_events` or reuse `property_view_events` keyed by
   `insar_building_id`). That way the *one* analytics plane (Weespas) sees both marketplace and
   risk-map engagement — no second analytics system, no second session store.
2. **Privileged InSAR data** (if some AOIs/buildings ever become non-public). Then the InSAR
   read app should accept the **Weespas JWT** (validate the same `SECRET_KEY`/issuer) on
   `/api/aoi/{code}/bundle` rather than invent its own users. Identity stays single-sourced.

**Net:** InSAR gains *no* login UI. It gains an optional "who sent me" context from Weespas,
used for (1) telemetry and (2) authorization — both delegated to Weespas.

### 9.5 Data collection & optimization — unify on the Weespas plane

You already have a mature telemetry/personalization machine in Weespas; the integration goal
is to **feed InSAR interactions into it, not duplicate it**:

- **Weespas already collects** (per `middleware/session.py` + `models/analytics.py`):
  per-visitor sessions (anon→auth one-time link), property views, searches (with geo + filter
  params), favorites, dismissals, geo (MaxMind, Celery-offloaded), and computes engagement /
  heatmaps / agent funnels with **SWR Redis caching + Celery Beat warmers**.
- **The personalization ranker** (`services/personalization.py`) already weighs favorites
  (0.30), searches (0.20), recent views (0.15), trending, freshness, featured, minus a
  seen-penalty. **Risk is a natural new signal**: a building's InSAR danger tier (via
  `building_link`) could become a ranking input ("show safer verified listings higher", or
  surface monitored-and-stable as a trust signal) — but only after P4b exposes it cheaply.
- **New events to add** (small, additive): `insar_map_open` (AOI, source listing),
  `insar_building_click` (building id, tier shown), `risk_badge_view`. Route them through the
  *same* session cookie so they join the existing `user_sessions` spine. This directly answers
  "how we collect data for personalization & optimization" — it's one pipe, Weespas's.
- **Admin/analytics surface** (`/stats`, `/staff`, `/admin` in weespas-frontend + the
  `/analytics/*` endpoints): once InSAR events land in the same tables, the existing dashboards
  (Recharts + Leaflet heatmaps, role-gated by `require_agent`/`require_staff`/`require_admin`)
  can grow a **risk view** — e.g. "listings in monitored vs unmonitored areas", "flagged-unsafe
  buildings with active listings" — with **no new analytics infrastructure**, just new queries.
  Treat this carefully: the flagged-unsafe×listing join is sensitive (see §4.2 corruption
  threat) and should be staff/authority-gated and audit-logged.

### 9.6 The two Admin/analytics surfaces must not be confused

- **Weespas Admin/Stats/Staff** (exists, mature): user & role management, deletion-request
  review, role-application approval, agent leaderboards/funnels/benchmarks, engagement +
  heatmaps. Gated FE-side by `hasRole(...)` route guards and BE-side by `require_role(...)`.
- **InSAR "admin"** (exists, minimal, different meaning): the **control API** (:8001) — a
  single-token operator seam to trigger rebuilds. It is **not** a user-facing admin; it's
  machine-to-machine (Weespas's `trigger_rebuild` calls it). Don't expose it to browsers.

When we add a flag-management or risk-oversight UI, it belongs in the **Weespas** admin plane
(it has the roles, the audit chain, and the session context), calling InSAR only as a data/
control source.

### 9.7 Integration risks specific to joining these four (watch-list)

1. **Two Vite frontends both default to :5173** — pin Weespas-FE to 5174 (done in §6.7).
2. **Two backends both default to :8000** — InSAR read app moved to 8002 (done in §6).
3. **CORS + cookies**: InSAR read app's CORS allow-list is `localhost:5173/:3000` and it
   neither sets nor needs cookies; if InSAR ever emits telemetry to Weespas, the call must go
   **to the Weespas origin** with `credentials:'include'`, not cross-origin to InSAR.
4. **Two map stacks** (Leaflet in Weespas, deck.gl/MapLibre in InSAR) — fine while embed/
   deep-link (A/B); a real cost only if we attempt full port (C).
5. **Identity single-sourcing**: resist adding a second login in InSAR. Every identity need
   routes through the Weespas JWT/session. One user store, one audit chain.
6. **Telemetry single-sourcing**: do not stand up a second analytics DB for InSAR; emit into
   the existing `user_sessions`-anchored tables.
7. **Sensitive joins**: "unsafe-flagged building ↔ public listing" is exactly the data the
   bribery-failure-mode is about. Gate it (staff/authority), audit it (the append-only chain),
   and never expose raw flag→listing maps on a public endpoint.

### 9.8 Concrete next steps — status (✅ shipped / ⬜ open, as of 2026-06-24)

| # | Step | State | Where it landed / what's left |
|---|------|-------|-------------------------------|
| 1 | **P4b — risk-at-point** read path, cached | ✅ (divergent shape) | Shipped as a **Weespas-side** endpoint `GET /insar/listing/{id}/risk` (`routers/insar.py`) reading the InSAR DuckDB via `services/insar_resolver.py::tier_for_building`, **not** a new InSAR `/api/risk` endpoint. Cache is React-Query `staleTime` 30 min on the FE; the reserved Redis `/3` is **unused** — fold or drop it. |
| 2 | **Weespas BE** listing→`building_link`→tier resolver | ✅ | `services/insar_resolver.py` (`resolve_and_link`, `tier_for_building`); `BuildingLink` cache table; 3-state monitored/not_monitored/unavailable, never collapses unknown→safe. |
| 3 | **FE risk pill (Option B) + deep-link (Option A)** | ✅ | `components/property/RiskPill.tsx` + `hooks/useListingRisk.ts` + `api/insar.ts`, mounted in `PropertyDetails`. Deep-link to the InSAR map (`?aoi=&building=`) shipped. Pill is on **PropertyDetails**, not yet on the compact `PropertyCard`. |
| 4 | **FE flag-entry UI** for professional/authority | ✅ SHIPPED | `weespas-frontend/src/components/property/StructuralFlagModal.tsx` (+ `hooks/useStructuralFlag.ts`, `api/structuralFlags.ts`), mounted in `PropertyDetails` behind `canFlag = isCertifier(user) && coverage==='monitored' && aoi_code && insar_building_id != null`. Authority-only "Condemned" (`AUTH_UNSAFE`) option mirrors the backend `require_certifier` gate. Covered by `StructuralFlagModal.test.tsx`. |
| 5 | **Telemetry** `insar_*` events + admin tile | ✅ SHIPPED | Bridge emits `insar_building_view`/`insar_export` into the Weespas plane (scoped JWT, feeds §8 company-detection). The **risk-oversight tile** is `components/analytics/RiskTileCard.tsx`, mounted in `StaffPage` (staff/admin-gated), fed by `GET /analytics/risk/summary` (`require_staff`, counts-only — never the raw listing↔flag map, per §4.2/§9.7). Covered by `RiskTileCard.test.tsx`. |
| 6 | **InSAR SPA** `?aoi=&building=` deep-link + optional context token | ✅ | Deep-link pre-select + fly-to wired; metering context token accepted; still **no login UI** (correct). |

> **Net (updated 2026-06-25):** ALL six steps are now SHIPPED. The previously-open
> Weespas-admin-plane frontends — flag entry (#4, `StructuralFlagModal`) and the risk
> oversight tile (#5, `RiskTileCard`) — were in fact already built, wired, and unit-tested;
> this row was stale. Verified against code on 2026-06-25 (Weespas 162 pytest · InSAR 165
> pytest · weespas-frontend 93 vitest, all green).

> **Coverage note (per the user's warning about the system getting too big to fully analyze):**
> this section was written after a full structured read of all four codebases (two backends +
> two frontends) and the analytics/session machinery. The InSAR frontend was confirmed to have
> **no** identity/session/telemetry; the Weespas frontend's dual JWT+cookie model and the
> backend's `user_sessions` telemetry spine were confirmed in code. The one area NOT yet read
> in depth: the older historical audit docs in `weespas-frontend` (`PROJECT_AUDIT.md` 2026-04,
> `Audit_Report.md` 2026-05) — they predate current features and are design-intent, not current
> truth. Flag if you want those folded in.
