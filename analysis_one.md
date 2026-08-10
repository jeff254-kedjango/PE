# PE Workspace — Analysis One

_Date: 2026-06-22_

This document is my analysis of the three folders inside `PE/` before any new
work begins. It is grounded in both the documentation **and** the actual code/
config on disk (where the two disagree, I flag it).

---

## 0. The big picture: these are TWO unrelated products, not one

The folder name `PE` groups three directories, but they belong to **two
separate projects**:

| Folder | Project | What it is |
|---|---|---|
| `InSAR-Final-main` | **Building-Subsidence Threat Monitor** | A satellite-radar (InSAR) geospatial system that flags Nairobi/Mombasa buildings at risk of ground-movement structural distress. Self-contained (own backend + frontend). |
| `weespas` | **Weespas — backend** | FastAPI property-marketplace API for the Kenyan market. |
| `weespas-frontend` | **Weespas — frontend** | React/TypeScript SPA that consumes the `weespas` API. |

So the real relationship is: **`weespas` + `weespas-frontend` are one product
(a property marketplace)**, and **`InSAR-Final-main` is a completely different
product (a structural-risk monitor)** that happens to share a parent folder.
They share no code, no data, and no API contract. The only thing they have in
common is a Nairobi/Kenya geographic focus and a FastAPI backend.

> If the intent is to *integrate* them (e.g. surface subsidence-risk on
> property listings), that is a real and interesting idea — but today there is
> zero wiring between them. I'd want to confirm the goal before assuming it.

**Repo state:** none of the three folders is its own git repo. The only `.git`
found is at `/home/jeff` (the home directory), and the current branch
(`feat/angular-distortion-tilt`) and last commit relate to the InSAR project.
So version control is currently coarse/ad-hoc — worth fixing (see
recommendations).

---

## 1. InSAR-Final-main — Building-Subsidence Threat Monitor

### What it does
Fuses Sentinel-1 InSAR deformation velocities with soil class, riparian/
shoreline distance, building height/load, and an angular-distortion
(differential-settlement) term into a single explainable **collapse score** +
absolute **danger tier**, then ranks worst-buildings-first for a human reviewer.
Five AOIs are on real InSAR: Huruma, South C, Kileleshwa, Kilimani, Mombasa.

### Architecture (verified in code)
- **Pipeline (build-time only):** `asf_search → HyP3 → MintPy → GACOS →
  reproject/clip → GeoParquet → DuckDB`. ~25 scripts under `backend/scripts/`.
- **API:** single read-only FastAPI process (`backend/app/main.py`) over a
  DuckDB file (`data/demo.duckdb`, git-ignored). Serves pre-baked binary
  bundles per AOI for fast frontend load — a genuinely nice touch (zero-copy
  ArrayBuffer slicing on the client).
- **Frontend:** React + TS + Vite + MapLibre GL + deck.gl, offline PMTiles
  basemap. Components: `RiskMap`, `ThreatSidebar`, `TimeSlider`, `TopBar`.
- **Tests:** 6 pytest files covering scoring invariants, the defensibility gate,
  decomposition, height pipeline, GACOS retry/bounce.

### Strengths
- **Intellectual honesty is the standout quality.** `docs/risk_model.md` and the
  README explicitly state this is a *screening/triage* tool, **not** a predictive
  collapse model; the score is "a deliberately simple, explainable weighted sum…
  *not* a calibrated collapse probability." The defensibility gate (coherence γ +
  linear-trend R²) and the "WATCH" tier for high-coherence-but-non-linear pixels
  show real domain maturity.
- **Tilt term is escalate-only** — correctly acknowledges InSAR can't resolve
  building-scale tilt, so it can raise but never lower a flag.
- Clean separation: heavy geoprocessing is build-time; the running app touches
  no network. DuckDB-as-file is a sensible laptop-scale choice (README even
  notes PostGIS is the right answer at city scale).
- Strong docs: `Ref_One.md`, `PLAN.md`, alignment plan, OpenSARLab runbook.

### Risks / gaps
- **No calibration / ground truth yet.** The score weights (`0.55 subsidence +
  0.25 riparian + 0.20 soil`) are hand-set. `risk_model.md` lays out a sound
  calibration plan (geocode historical collapses, score t-6mo, tune to AUC) but
  it is **not done**. This is the single biggest credibility gap if anyone wants
  to *act* on the output.
- **No uncertainty bar shown with the score** — the doc itself calls this out as
  a must-do "before showing the score to anyone with authority to act."
- Per-building InSAR in informal settlements is intrinsically noisy (iron-sheet
  decorrelation, sub-pixel buildings). The project handles this honestly, but it
  caps how strong any claim can be.
- Large data artifacts are (correctly) git-ignored and regenerable, but that
  means a fresh clone is non-trivial to bring up on real data — the synthetic
  seeder mitigates this for demos.

---

## 2. weespas — Property-Marketplace Backend

### What it does
FastAPI + SQLAlchemy property marketplace API: properties, agents, addresses,
images/videos, full RBAC (user/agent/staff/admin), OTP auth (Africa's Talking
SMS), favorites, saved searches, contact, analytics, and a Celery async stack.

### Scale of the codebase (verified)
- **~13.8k lines of Python**, **15 routers**, layered into
  `models / schemas / services / routers / middleware / core`. This is a
  substantial, real backend — far beyond the "6 models, 9 endpoints" the older
  `README_V2.md` describes.
- Celery workers + beat + flower, Redis, geoip, image processing, ranking,
  personalization, analytics caching — all present as service modules.

### ⚠️ Documentation is significantly out of date / contradictory
This is the most important finding for weespas. The headline docs do **not**
match the code:

1. **DB engine.** `README_V2.md` and `PROJECT_AUDIT.md` say "SQLite (dev) /
   PostgreSQL (production)" and list Postgres migration as a *future* checkbox.
   But `core/config.py` **already** hard-codes a PostgreSQL URL
   (`postgresql://postgres:…@localhost:5432/commercial`). The migration the docs
   call "not done" appears to have happened. (A 160 KB `weespas.db` SQLite file
   still sits in the tree as a leftover.)
2. **Feature surface.** The code has analytics, saved searches, dismissals,
   sessions, role-applications, staff/admin moderation, deletion-request
   workflow — none of which appear in `README_V2.md`. There are 9+ markdown docs
   (`Celery_Audit.md` alone is 79 KB) layered on top, so the *true* current state
   is scattered across many files rather than one authoritative README.

### 🔴 Security issues to flag immediately
- **Live secrets committed.** `weespas/.env` contains Africa's Talking
  credentials (`AT_API_KEY`, etc.) in cleartext on disk, and `config.py` has a
  **hard-coded DB password** (`254jeffWEESPAS`). These should be rotated and
  moved to untracked env/secret management before any push to a shared remote.
- **Hard-coded admin backdoor.** `main.py` startup always promotes
  `kwemangenyagrowa@gmail.com` to admin. Fine for a demo; a liability in prod.
- `allow_methods=["*"], allow_headers=["*"]` with credentialed CORS — acceptable
  for localhost dev, must be tightened for production.

### Other gaps
- **Zero automated tests** in the backend (no `test_*.py` found). For a 13.8k-LOC
  RBAC + payments-adjacent system, this is the biggest engineering risk.
- Many one-off migration/backfill scripts at the repo root (`add_*.py`,
  `backfill_*.py`, `relink_eunice.py`, `seed_*.py`) — these are effectively an
  ad-hoc migration history. A real migration tool (Alembic) would replace them.

### Strengths
- Clean layered architecture; proper service layer; RBAC is thoughtfully
  designed (ownership enforcement, self-protection, staff→admin deletion
  workflow). The async/analytics ambition is real and mostly built.

---

## 3. weespas-frontend — Property-Marketplace Frontend

### What it does
React 18 + TypeScript + Vite SPA. Consumes the weespas API
(`VITE_API_BASE_URL=http://localhost:8000/api/v1`). React Query for data,
React Router for ~11 routes, Leaflet for maps, Recharts for the agent/admin
dashboards.

### Scale (verified)
- **~17.5k lines of TS/TSX.** Rich component library: layout (Navbar, Hero,
  Footer, SearchPanel, MobileBottomNav, MegaMenu, Splash), property
  (Details/Gallery), shorts (vertical video feed — a "Reels" feature), analytics
  dashboards, maps, and a deep `ui/` kit.
- Routes live and lazy-loaded with `Suspense` + per-route error boundaries:
  `/`, `/favorites`, `/login`, `/register`, `/profile`, `/stats`, `/admin`,
  `/staff`, `/customer-care`, `/agents`, `/agents/:id`.

### Documentation vs reality
- `PROJECT_AUDIT.md` (dated 2026-04-11) is a detailed completion plan with most
  items struck through as DONE — but it predates whole feature areas now in the
  code (**shorts/vertical video, analytics dashboards, staff page, customer
  care, role applications, agents directory**). So, like weespas, the frontend
  has **outrun its own audit doc**. There's also a separate `Audit_Report.md`.
- The audit's "WHAT'S NOT DONE" list (PropertyDetails, MobileBottomNav CSS, Map,
  auth pages) is now stale — those exist in the tree.

### Gaps
- **No frontend tests** (0 `*.test.*` files) and no test runner configured in
  `package.json` (only dev/build/lint/preview).
- `dist/` (a build output) is committed into the tree — should be git-ignored.
- A `node_modules/` with 186 entries is present in the working tree.

### Strengths
- Genuinely modern stack and good practices: code-splitting, error boundaries,
  React Query caching, a real design-token system (variables/animations/reset),
  localStorage favorites, debounced geo search.

---

## 4. Cross-cutting observations

1. **Two products, one folder.** Decide explicitly whether `PE` is just a
   convenient parent or whether InSAR and Weespas are meant to integrate. Today
   they're independent.
2. **Docs drift is the recurring theme.** All three folders have thorough
   documentation, but in weespas and weespas-frontend the *headline* docs lag the
   code by a wide margin (DB engine, whole feature areas). InSAR is the exception
   — its docs are current and unusually honest.
3. **Testing is lopsided.** InSAR has a real (if small) pytest suite tied to its
   risk logic; both Weespas halves have **none**. For a marketplace with auth,
   money-adjacent listings, and RBAC, that's the highest-leverage gap.
4. **Secret hygiene + version control.** Committed secrets, a hard-coded DB
   password, a committed `dist/`, and no per-project git repos should be sorted
   before any collaborative or production step.

---

## 5. Suggested next steps (for your decision — not yet acted on)

**If the goal is to harden Weespas:**
- Rotate the leaked Africa's Talking + DB credentials; move to untracked env /
  secret store; scrub them from any history.
- Stand up `git` per project with proper `.gitignore` (exclude `dist/`,
  `node_modules/`, `.env`, `*.db`).
- Replace the root-level `add_*/backfill_*` scripts with Alembic migrations.
- Add a pytest suite for auth/RBAC + a Vitest suite for the frontend.
- Refresh `README_V2.md` / `PROJECT_AUDIT.md` to match the real, current system
  (or write one authoritative `STATE.md`).

**If the goal is to advance InSAR:**
- Execute the calibration plan in `risk_model.md` (historical collapses → AUC).
- Attach a coherence-weighted uncertainty bar to every displayed velocity/score.

**If the goal is integration (InSAR ↔ Weespas):**
- Define the contract first: e.g. Weespas listings query an InSAR
  `risk-summary`/`buildings` endpoint by lat/lng to show a subsidence-risk badge.
  This is feasible but currently 0% built.

---

### What I'd like clarified before writing any code
1. Which project (or the integration) is the focus of this session?
2. For Weespas: is PostgreSQL now the live DB (config says yes) and is the
   SQLite file dead?
3. Should I treat the existing docs as authoritative, or reconcile them to the
   code as part of the work?
