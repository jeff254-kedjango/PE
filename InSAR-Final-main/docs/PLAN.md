# infra-proptech — Implementation Plan

Locked decisions and the phase-by-phase roadmap. Update this file when scope changes; don't let it drift.

---

## Locked decisions

### MintPy execution: **ASF OpenSARLab**
User has no prior MintPy experience; laptop can't host the install (RAM + ISCE/PyAPS dependency tree). OpenSARLab is ASF's free hosted JupyterHub for Earthdata users — MintPy pre-installed, same Earthdata auth as HyP3, browser-driven. We'll write a runbook so this is doable cold. Only `velocity.h5` + `timeseries.h5` come down to the laptop.

### Two AOIs, two phenomena, two footprint sources

| AOI | Phenomenon | Footprints | Why |
|---|---|---|---|
| **Huruma, Nairobi** | Informal-settlement subsidence (riparian zones, black-cotton soils) | **Google Open Buildings** | OSM patchy in informal areas; Open Buildings is ML-derived and dense. InSAR will be noisy — we own that with coherence overlays. |
| **Mombasa Old Town / Kilindini** | Coastal subsidence on reclaimed land | **OSM** | OSM has strong coverage on mapped coastal Kenya; concrete + bare surfaces give cleaner InSAR coherence. Visually striking contrast to Huruma. |

The pitch becomes "same engine, two regimes, honest about what each can and can't tell you."

### Demo network reality
Hotspot is available during presentation, so we can opt in a "live HyP3 status" corner widget — but the primary demo path stays fully local. Reliability beats latency.

---

## Phase 1 — Multi-AOI foundation
- AOI-partitioned GeoParquet: `data/parquet/buildings/aoi=huruma/…`, `data/parquet/buildings/aoi=mombasa/…`
- New `aoi_registry` table/parquet: code, name, center lon/lat, bbox, footprint_source, phenomenon, narrative copy
- Refactor `init_db.sql` to use partitioned external tables (DuckDB reads partitioned Parquet natively)
- Seeder generates **both** synthetic datasets:
  - Huruma: existing physics (riparian + soil + hotspots)
  - Mombasa: coastal physics (distance-to-shoreline + reclaimed-land flag + tidal-loading-ish low-frequency oscillation in the time series)
- API gains `?aoi=` everywhere; new `/aois` endpoint returns the registry

**Exit:** seeder produces two AOI datasets; API switches between them; views over both work.

## Phase 2 — Performance pass
- **Backend hot path**: Arrow IPC streaming for `/buildings/at-date`. No JSON parsing on frontend; DuckDB → Arrow → bytes → `apache-arrow` in browser.
- Prepared statements per AOI; one DuckDB connection shared, cursor per request.
- Pre-bake all 24 monthly snapshots per AOI into a single binary blob loaded once; slider toggles attribute offset, doesn't refetch.
- **Frontend**: deck.gl binary attribute format (typed arrays, no per-frame object access). Target: 60fps with 20k buildings, full 24-month animation.
- Time-series endpoint returns Parquet bytes.

**Exit:** `/buildings/at-date` returns 10k buildings in <30ms locally; UI animates the slider at sustained 60fps.

## Phase 3 — Frontend skeleton + UX
- Vite + React 18 + TS + Tailwind, dark command-center theme
- AOI switcher (top-left chip group); per-AOI narrative card; phenomenon legend changes per AOI
- Coherence-aware rendering — hatched/desaturated when coherence < 0.3 (don't lie about confidence)
- Composite-risk breakdown panel (subsidence/riparian/soil contribution as stacked bar) inside the threat sidebar
- PMTiles fetch script (one Kenya extract covers both AOIs, ~50 MB)
- Time slider with play/pause, blinking severe-subsidence buildings, sparkline of selected building

**Exit:** `npm run dev` shows the dashboard with two switchable AOIs on synthetic data.

## Phase 4 — Real InSAR pipeline, Huruma
Sequenced to make use of HyP3/MintPy wait time:
1. Earthdata Login account + `~/.netrc`; validate `hyp3.my_info()`
2. `asf_search` for S1 SLCs over Huruma AOI, 2024-06 → 2026-05, ascending track only (don't mix orbits)
3. Submit Huruma HyP3 `InSAR_GAMMA` batch (~23 sequential pairs); jobs run hours
4. While jobs run: write `docs/opensarlab_runbook.md` + MintPy config templates
5. Download HyP3 outputs to OpenSARLab, run `smallbaselineApp.py` (SBAS)
6. Download MintPy products (`velocity.h5`, `timeseries.h5`, geocoded) to laptop
7. Spatial-join to Google Open Buildings (coherence-weighted mean per footprint; drop pixels with coherence < 0.3)
8. Replace Huruma synthetic Parquet with real Parquet (same schema → no app code changes)

**Exit:** Huruma in the UI is real Sentinel-1-derived data.

## Phase 5 — Real InSAR pipeline, Mombasa
Same as Phase 4 but with OSM footprints via Overpass. Run in parallel with Phase 4 where HyP3 quota allows.

**Exit:** Both AOIs show real data.

## Phase 6 — Calibration & honesty
- Hand-geocoded incident dataset (NCA reports + news, 2020–2026) → `data/incidents.csv`
- Score-at-T-minus-6mo retrospective evaluation; report AUC + calibration plot
- If signal isn't there: **document it.** Don't tune weights against noise.
- Per-building uncertainty bars on velocity (linear-fit std error from the time series)

**Exit:** Honest performance numbers in `docs/risk_model.md`.

## Phase 7 — Polish for the room
- Optional live-HyP3-status widget (only when hotspot is on)
- "Replay mode": auto-animation that pans between AOIs while the slider runs
- Pre-recorded `.mp4` fallback if anything melts during the session
- Keyboard shortcuts (space = play/pause, ←/→ = step month, 1/2 = switch AOI)

---

## Execution order
Phase 1 → 2 → 3 sequentially (each unlocks the next).
Phase 4 kicked off as soon as Earthdata account is ready, running in parallel with 2/3.
Phase 5 follows 4 (or parallel where HyP3 quota allows).
Phase 6 only after 4 + 5 land real data.
Phase 7 last.

## What's *not* in scope (for the MVP)
- More than two AOIs
- PostGIS / city-scale serving
- A live tile-server (PMTiles single-file is enough)
- ML risk model — the composite score stays linear and explainable until we have real ground truth (Phase 6)
- Mobile / responsive layout — desktop boardroom screen only
