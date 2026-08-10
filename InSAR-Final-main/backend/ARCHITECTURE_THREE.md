# Architecture-three

National-TV-readiness plan. ARCHITECTURE_ONE delivered the renderer,
ARCHITECTURE_TWO delivered the risk engine. This cycle hardens the velocity
science, surfaces honest diagnostics in the UI, and makes the building-click
moment defensible to a structural engineer watching the broadcast.

The driving constraint: a viewer must be able to click any building and walk
away with a calibrated picture — what the data says, what it cannot say, and
what they should do next. **Subsidence indicator + composite context score.
Not a structural-safety determination. Not a predictive collapse engine.**
Copy that overclaims is more dangerous than copy that admits limits.

## Performance bar (applies to every change below)

- Pixel-wise math is vectorised numpy/pyarrow — no per-pixel Python loops.
  Anything that touches a building array uses `np.asarray` views, structured
  arrays, or DuckDB SQL. Per-row Python is a regression.
- Read paths are zero-copy: PMTiles + GeoParquet → `pyarrow.Table` → numpy
  views → typed JS arrays in the bundle. No JSON intermediate for any array
  that ever exceeds 1 000 rows.
- MintPy outputs land as `float32`/`uint8` columns in the parquet shard, never
  as Python lists. Coherence sparkline payloads (10 floats × N buildings)
  ride as a single `binary` column, not as a JSON nested array.
- Every new scripted step is idempotent and cache-aware — reruns are a no-op
  on already-computed files, never a full recompute.
- The demo critical path stays fully offline. Network access, when used, is
  build-time or a corner widget with a hard timeout (per project memory).

## Phase A — Velocity-quality upgrade (must ship first)

Velocity numbers drive every downstream story. Until they're publication-grade
in tropical contexts, the rest is decoration. Order of operations is fixed:
A1 unblocks A2/A4 retuning; A3 must be set before any new MintPy run is
treated as a baseline.

### A1 — GACOS tropospheric correction

The single largest correctable error source in tropical InSAR. PyAPS+ERA5 is
the in-tree alternative; GACOS is preferred because it ingests ERA5 + InSAR
geometry and outputs already-projected zenith delays per SAR date, which
collapses what would be two scripts and a CDS account into one fetch step.

- `scripts/fetch_gacos.py` (new). Reads HyP3 acquisition dates from
  `backend/data/hyp3_work/<aoi>/`, submits one GACOS request per AOI window,
  polls the GACOS portal API, downloads `*.ztd.tif` grids to
  `backend/data/raw/env/gacos/<aoi>/`. Idempotent (skip if `.ztd.tif` already
  present for a date).
- `scripts/mintpy_config.tmpl`: switch
  `mintpy.troposphericDelay.method = gacos` and add
  `mintpy.troposphericDelay.gacosDir = {{gacos_dir}}` with the per-AOI path.
- Performance: GACOS grids are ~1 MB each, ~60 dates per AOI = ~60 MB. Reads
  are GDAL-backed in MintPy, no Python overhead. Network is build-time only.

Acceptance: median per-pixel velocity uncertainty drops from current
~±2.5 mm/yr to ≤±1.5 mm/yr on the Mombasa stack; before/after histograms
saved to `docs/`.

### A2 — DEM error correction (already on, document)

`mintpy.topographicResidual = yes` is already set. The next-level improvement
is `mintpy.topographicResidual.stepFuncDate` when a known terrain disturbance
falls inside the window. Neither AOI has one we can defend right now — leave
unset, note the hook in the config.

### A3 — Explicit reference point

Currently `mintpy.reference.yx = auto`, which picks the highest-coherence
pixel anywhere in the subset. That makes every velocity *relative to a pixel
we did not choose*, and which can move between MintPy runs as coherence
shifts. For TV, the reference must be a documented, off-AOI, stable-bedrock
pixel.

- Extend `scripts/aois.py::AOI` with `reference_lon: float` and
  `reference_lat: float` (frozen). Candidates:
  - **Huruma** → granite outcrop at Karura Forest edge,
    `(36.8345, -1.2391)` — outside subsidence-prone alluvium, low NDVI
    variance, no construction activity in the S1 window.
  - **Mombasa** → coral platform at Changamwe Hill,
    `(39.6395, -4.0265)` — high coherence over both ASC/DESC, far from
    coastline, no reclaim fill.
- `mintpy_config.tmpl`: render
  `mintpy.reference.lalo = {{ref_lat}},{{ref_lon}}` from the AOI.
- `mintpy_run.py`: pass the new tokens.
- UI: a small "anchor" pin renders the reference point on the map with a
  tooltip explaining what it means (Phase C, also lands here).

Acceptance: both AOIs run MintPy with the explicit reference; the reference
appears on the map; reference choice documented in `docs/methodology.md`.

### A4 — Adaptive coherence weighting (already configured, verify)

`mintpy.networkInversion.weightFunc = coh` and
`mintpy.networkInversion.maskThreshold = 0.2` are set. After A1's
tropospheric correction lands, the maskThreshold may want to drop to 0.15 —
a less-noisy phase field tolerates lower-coherence pixels in the LS without
biasing. To be re-tuned post-A1 with the velocity-residual histogram.

## Phase B — Diagnostic overlays

The point is not to add more numbers. The point is to make trust visible.
Every B-item answers a viewer question of the form "why should I believe
that?".

### B1 — Closure-phase residual layer

Run `mintpy.utils.closure_phase.compute_closure_phase_bias` after the
inversion; persist per-pixel `closure_rms_rad` to the parquet shard.

- `scripts/postprocess.py`: read `closurePhase.h5`, write `closure_rms`
  per pixel into the joined parquet.
- `scripts/join_insar.py`: project from pixel → building via the same
  centroid lookup used for velocity (per project memory: footprints are
  sub-pixel; one pixel maps to many buildings — no rasterisation).
- Schema: `closure_rms_rad float32 NOT NULL DEFAULT 0`.
- UI: a togglable raster overlay (color ramp 0 → π/4 rad) plus a per-
  building badge "atmospheric noise: low / med / high". *Honest copy:* high
  closure = residual atmospheric/vegetation noise, **not** new construction.

### B2 — Coherence time-series, not just final coherence

Final-mean coherence hides the story of *when* a building was a reliable
target. A pixel that started γ=0.8 and drifted to γ=0.3 tells you something
real about the surface; a pixel at flat γ=0.55 tells you something different.

- `scripts/postprocess.py`: read `coherenceSpatialAvg.h5` (already exists),
  extract per-pixel per-epoch coherence (8–10 floats per pixel).
- Parquet: pack as a single fixed-length `binary` column
  (`coh_series float32[K]` where K = number of epochs), not a list. Saves
  ~3× over JSON nested arrays, decodes zero-copy on the frontend with
  `Float32Array`.
- UI: sparkline on the building card. Mouseover shows the date of each
  coherence sample.

### B3 — DEM-residual flag

`mintpy.topographicResidual = yes` produces `demErr.h5`. Per pixel,
`demErr > 15 m` means a meaningful chunk of the apparent velocity is DEM
artefact, not deformation. Flag those buildings; *don't* hide them — show
them with a "DEM-uncertain" tag.

- Schema: `dem_err_m float32`, `dem_err_flag bool`.
- UI: subtle warning chip on the building card; "what does this mean?"
  expand.

### B4 — Reference-point marker on the map

Small "⚓" pin at the A3 reference lat/lon. Tooltip: *"All velocities on this
map are measured relative to this pixel — chosen because it sits on stable
bedrock outside the AOI. If the reference moves, every reading moves with
it. We picked a defensible one and documented it."*

Acceptance for Phase B: building card shows coherence sparkline, closure
badge, DEM-error chip (when applicable). Reference pin is on the map with
working tooltip. Closure-RMS overlay togglable.

## Phase C — Building-click UX

The National TV moment. A viewer's first interaction is clicking a building.
They get a card with four panes in priority order — block subsidence first,
because that's where the InSAR pixel actually lives; building context second,
because that's where per-building granularity is defensible; what-we-don't-
know third, because honesty is the moat; history fourth, because trajectory
beats snapshot.

### C1 — Block-aggregation layer

Pixel-level velocity is the truth; per-building velocity is a sub-pixel
fiction. Roll up to block polygons (when admin block polygons exist) or
H3 r10 hexagons (≈80 m, matching the pixel) otherwise.

- `scripts/postprocess.py`: new step `aggregate_blocks(aoi)`. For each
  hexagon, weighted-mean velocity by inverse-variance pool, pooled
  uncertainty `σ_pool = sqrt(1 / Σ(1/σ_i²))`, member count, mean
  coherence. Write `data/parquet/blocks/aoi=<code>/blocks.parquet`.
- Performance: aggregation in DuckDB SQL (`GROUP BY h3`), not Python.
  ~30k pixels per AOI → ~ms.
- Schema:
  ```
  block_id          string PRIMARY KEY  -- h3-r10 cell id
  aoi               string NOT NULL
  v_mean_mm_yr      float32 NOT NULL
  v_sigma_mm_yr     float32 NOT NULL
  coh_mean          float32 NOT NULL
  pixel_count       uint16  NOT NULL
  building_count    uint16  NOT NULL
  ```
- `app/main.py`: serve `/api/blocks/{aoi}` and the per-block detail.
- Building card: pane #1 reads the block this building lives in.

### C2 — Building card component (four panes)

The card's panes are fixed:

1. **Block subsidence** — "This block: 4.2 ± 1.1 mm/yr (Sentinel-1,
   2024-06 to 2026-05)." Color-coded against a calibrated scale (stable
   < 2, slow 2–5, moderate 5–10, fast > 10 mm/yr).
2. **Your building's context** — composite percentile within AOI cohort.
   "Worse than 73% of buildings in this block." Drill-down lists the
   drivers (soil, riparian, NDVI, etc).
3. **What we don't know** — explicit. The InSAR-resolves-blocks line. Link
   to the methodology modal.
4. **History & trajectory** — coherence sparkline (B2), velocity time-
   series with confidence band, last update.

- Performance: card receives a single typed bundle slice (already in
  `_build_bundle`), no per-card network call. All sparkline + time-series
  data sits in pre-packed `Float32Array` views.
- Copy is reviewed against the framing memory before each pane ships.

### C3 — Methodology modal

A single "How this works" modal accessible from every card pane. Contains:

- Physical-scale diagram: 80 m InSAR pixel ↔ median 64 m² footprint
  (per project memory's pixel/footprint reality note).
- Data sources table with cadence, resolution, latency.
- Honest limits list: what we resolve, what we don't, when to inspect.
- References: Sentinel-1, HyP3, MintPy, GACOS, CHIRPS, GRACE, SoilGrids,
  Google Open Buildings, OSM.

### C4 — Cohort percentile column

Mostly already computed in `scripts/phenomena.py` Tier-3 step. Surface as
`cohort_pct_aoi` (within AOI) and `cohort_pct_block` (within block) on the
building schema. Building card pane #2 uses block-percentile; AOI-percentile
is a fallback when block has < 30 members.

### C5 — Velocity time-series with band

Existing time-series gains a shaded ±σ band (per-epoch uncertainty from the
LS inversion). The band, not the line, is the honest visual.

## Phase D — Composite-context extension

Strengthen the per-building context score with sources that exist for both
AOIs. Reject sources that don't (permits in Nairobi informal settlements;
crowdsourced reports without a moderation product).

### D1 — Building geometry signal

- Schema add: `height_m_source` ∈ {`osm`, `open_buildings`, `null`};
  `height_confidence` float.
- Taller building + lower confidence → uncertainty penalty in composite.

### D2 — Slope at centroid

Sample SRTM/Copernicus 30 m DEM slope at each centroid; persist as
`slope_deg float32`. Slope > 8° → amplification factor in composite.
Vectorised single rasterio sample call per AOI.

### D3 — Distance to nearest mapped fault

Pull GEM Global Active Faults shapefile (small, ~50 MB global, ship a clip
per AOI in repo). Compute `fault_dist_m` per centroid via shapely STRtree
(same pattern as `scripts/fetch_env_context.py::_distance_to_lines_m` —
reuse the function).

### D4 — Permits / D5 — Crowdsourced

Out of scope for v1 (per the response above). D5 reserves
`citizen_report_count uint16 NOT NULL DEFAULT 0` on the schema so v2 has a
landing place without a migration.

## Phase E — Demo hardening

The presentation surface. Every E-item is a thing that has burned a live
demo somewhere. None of them are optional before broadcast.

- **E1 — Offline path verified.** Dress-rehearse with the laptop's WiFi
  off. Demo critical path must complete end-to-end in <8 minutes with
  zero exceptions.
- **E2 — Pre-baked talking-track buildings.** Four memorised IDs (2 per
  AOI): one high-risk on every signal, one ambiguous (the uncertainty
  pane gets its moment), one stable, one with interesting coherence
  history.
- **E3 — Live HyP3 status corner widget.** One network read at app load,
  hard 2 s timeout, silently hidden on failure. Per project memory:
  internet via hotspot is allowed, but the demo's primary path stays
  local.
- **E4 — Producer one-pager + 60 s elevator script.** Print, hand to
  producer the morning of.
- **E5 — Disclaimer footer, always visible.** "Subsidence indicator. Not
  a structural-safety determination. Ground inspection required for
  life-safety decisions." Legal floor + moral floor.

## Out of scope (explicit)

- **PS-InSAR.** At HyP3's 80 m pixel size, PSI does not deliver building-
  skeleton resolution. Doing it wrong on TV is worse than not doing it.
  Real PSI needs SLC reprocessing from scratch — a 2–3 month workstream,
  separate cycle.
- **±0.8 mm/yr precision claims anywhere in the UI or pitch.** Even with
  GACOS we are at ±1.0–1.5 mm/yr in Mombasa, worse in Huruma. The number
  the UI displays must be the number we can defend.
- **"Active hazard" / "OVERRIDE: CONFIRMED RISK" language.** The framing
  memory governs every string the UI ships. Indicator + context score.
  Inspection for life-safety.
- **Postgres / Celery / microservices.** Stack is locked DuckDB + single
  FastAPI process. Restating it because the source research suggested a
  different shape.

## Order and time

Eleven to thirteen working days end-to-end, in this order:

1. **A1 → A3 → A2/A4 retune** (~3 days). GACOS account request goes in
   immediately, in parallel — turnaround is 1–5 business days.
2. **D1–D3** + **C1** (~2 days, parallel work).
3. **B1–B4** (~3 days).
4. **C2–C5** (~4 days). The TV moment lives here.
5. **E1–E5** (~2 days, day before broadcast).

Two risks: GACOS portal turnaround (apply first), and MintPy reprocess
wall-clock (each AOI is hours per config change — schedule overnight
runs).
