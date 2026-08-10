# Architecture-one

Implementation plan for the current work cycle: finish carryover from the prior
session, then ship two research-driven extensions (InSAR-derived building
heights with ML-footprint fusion, and vector-decomposed horizontal drift).

---

## Carryover (from last session)

1. Remove the red probe rectangle at `frontend/src/components/RiskMap.tsx:225-242,296`
   and the two `console.log` diagnostics at `:250` and `:317`. The render bug
   (MapboxOverlay `interleaved: true` + maplibre-gl.css overriding `position`)
   is fixed; the probe is now noise.
2. PMTiles install — the previous session never got `frontend/scripts/fetch_pmtiles.sh`
   to produce a file. Verify the script and run it (one-shot, offline-safe).
   If the upstream is gated, document the fallback (the empty dark canvas works).
3. The resize-on-layout effect already added to `RiskMap.tsx` stays.

---

## Study 1 — InSAR-derived building height (with ML-footprint fusion)

### Physics

Sentinel-1 side-looking radar measures range, not vertical height directly. In
dense urban areas the radar beam hits the roof of a tall building before the
ground at its base — the so-called layover phenomenon. The phase difference
across the layover zone, combined with the interferometric baseline, lets us
invert for height:

    Δφ = (4π · B⊥) / (λ · R · sinθ) · h

Sentinel-1 C-band: λ ≈ 5.6 cm. Spatial resolution is ~5 m × 20 m, so isolated
single-building heights in informal settlements like Huruma are noisy. The
fusion trick: ML building footprints (Google Open Buildings for Huruma, OSM for
Mombasa) bound which pixels belong to a given structure, so the noisy
per-pixel height estimates can be averaged within a known boundary.

### Backend changes

- `backend/scripts/phenomena.py` + `backend/scripts/seed_synthetic.py`:
  add three columns to the buildings table.
    - `insar_height_m` — float64. Noisy phase-fringe-inversion estimate.
      Generated as `true_h + N(0, σ)` where `σ` scales with Sentinel-1
      resolution and footprint area (small footprints σ up to ±4 m,
      large footprints σ ±1.5 m).
    - `insar_height_sigma_m` — float64. The σ used, exposed to the UI.
    - `height_source` — string. One of `"footprint_floors"` | `"insar_phase"`
      | `"fused"`. The actually-rendered height comes from `"fused"`,
      computed as inverse-variance-weighted average of footprint and InSAR.
- Per-AOI noise floor: Huruma σ is larger (decorrelation in dense informal
  settlement); Mombasa σ is smaller (concrete + bare coral surfaces hold
  coherence).
- `backend/scripts/init_db.sql`: extend the `buildings` view to expose the
  three new fields.
- `backend/app/main.py::_build_bundle`: pack three new sections into the
  binary bundle:
    - `insar_height_m` — Float32 [n_buildings]
    - `insar_height_sigma_m` — Float32 [n_buildings]
    - `fused_height_m` — Float32 [n_buildings] (this is the value used for
      3D extrusion)

The ETag is content-hashed, so any of these changes auto-invalidate browser
caches.

### Frontend changes

- `frontend/src/lib/bundle.ts`: three new Float32Array views and an updated
  `Bundle` type.
- `frontend/src/components/RiskMap.tsx`: the elevation buffer now reads from
  `fusedHeightM` (no shape change to the per-vertex buffer; just a different
  source).
- `frontend/src/components/ThreatSidebar.tsx`: new "Height" subsection in the
  per-building inspector. Shows:
    - Footprint estimate (m, derived from n_floors).
    - InSAR estimate ± σ (m).
    - Fused value (m), labelled as "Used for 3D".
  Honest framing — InSAR may disagree with footprint by several meters in
  noisy AOIs and the UI shows that gap explicitly.

---

## Study 2 — Horizontal drift (vector-decomposed E-W velocity)

### Physics

A single Sentinel-1 pass measures only line-of-sight displacement. By
combining ascending (south→north, looking east) and descending (north→south,
looking west) passes over the same coordinates and decomposing the two LOS
vectors trigonometrically, we recover two clean spatial components:

    U_up   = vertical (subsidence / heave)
    U_east = horizontal east-west drift

Sentinel-1's near-polar orbits make it highly sensitive to east-west motion
and blind to north-south, so we capture E-W only. Structurally, horizontal
drift is often more dangerous than uniform vertical sinking: it indicates
leaning, shearing, or sliding — typically failing retaining walls,
destabilized riparian banks, or adjacent excavations.

### Backend changes

- `backend/scripts/phenomena.py`: extend the time-series generator. For every
  building × month, emit a new `velocity_horizontal_ew_mm_yr` field. Sign
  convention: positive = eastward, negative = westward.
- Per-AOI E-W bias:
    - Huruma: positive bias for buildings near the synthetic tributary
      (sliding toward the riparian line, simulating a failing bank).
    - Mombasa: positive bias near the shoreline on reclaimed fill (seaward
      creep).
    - Plus modest Gaussian noise per month and per-building coherence
      desaturation.
- `backend/scripts/init_db.sql`: add the new column to the
  `subsidence_time_series` view.
- `backend/app/main.py::_build_bundle`: pack a new
  `velocity_horizontal_ew` matrix [n_buildings × n_months] as Float32.

### Frontend changes

- `frontend/src/lib/bundle.ts`: new `velocityHorizontalEwMmYr` view and a
  `horizontalVelocityAt(b, i, m)` O(1) accessor.
- `frontend/src/components/TopBar.tsx`: segmented control toggle:
  `[ Subsidence ]  [ Drift ]`. Lifts a `mode` state.
- `frontend/src/components/RiskMap.tsx`:
    - **Color**: mode-aware. Subsidence mode keeps the existing red→amber→green
      ramp on `velocity_mm_yr`. Drift mode swaps to blue (westward) → grey (0)
      → orange (eastward), capped at ±15 mm/yr. Same per-vertex Uint8Array
      buffer, same `updateTriggers` flip — zero new allocations.
    - **Tilted geometry**: in Drift mode the building polygons render leaning.
      Two `ringCoords` Float32Arrays are kept in memory: the upright original,
      and a "drift-skewed" copy where each base ring is translated by
      `-offset = -(height × ew_velocity × visual_gain)` in lon-degrees.
      The SolidPolygonLayer extrudes flat (top sits over base), so a
      pre-translated base produces a leaning structure. Mode toggle is a
      pointer swap on `getPolygon.value`. Per-AOI gain tuned so a
      ±10 mm/yr building has a clearly visible lean at zoom 15.5.
- `frontend/src/components/ThreatSidebar.tsx`: new "Horizontal Drift" metric
  block next to "Subsidence V" in the selected-building inspector. Format:
  `+6.2 mm/yr (E)` or `-2.1 mm/yr (W)`. Severity threshold at ±5 mm/yr.

---

## Performance posture

- All new data lands in the **binary bundle** — same path that already works.
  One fetch per AOI, typed-array views into the original ArrayBuffer, zero
  copies on the hot path.
- Mode toggle = one ref flip + one re-write of the per-vertex color Uint8Array
  (same O(n_buildings) cost as a month tick). No new heap allocations.
- Tilt is precomputed once per AOI on bundle parse. Two
  `ringCoords` arrays held side by side; mode toggle is a pointer swap on the
  layer's `getPolygon.value`.
- Bundle ETag changes when the new fields are added, so any stale cached
  bundles invalidate on first reload.

---

## Order of execution

1. Carryover cleanup: probe layer and diagnostic logs.
2. Backend: extend `phenomena.py`, schemas, `init_db.sql`, and
   `_build_bundle` for all new fields.
3. Re-seed, restart API, verify bundle bytes round-trip.
4. Frontend: `bundle.ts` typed-array views + accessors.
5. Frontend: TopBar mode toggle.
6. Frontend: `RiskMap.tsx` tilt geometry + dual ramps + mode-aware color
   writer.
7. Frontend: ThreatSidebar — Height card + Horizontal Drift metric.
8. Drive Playwright on both modes, capture screenshots, share findings.
9. Visual confirmation on the user's screen.
