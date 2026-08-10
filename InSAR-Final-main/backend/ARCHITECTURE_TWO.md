# Architecture-two

Implementation plan for the next work cycle. ARCHITECTURE_ONE delivered the
*renderer*: synthetic state, fused heights, mode toggle, drift visualization.
This cycle turns it into a *risk engine* that classifies and prioritizes,
based on the principles in the in-house consultant's framework
("Architectural Principles for Geospatial Kinematic Anomaly Modelling").

The framework's five principles map to this codebase as follows:

| Principle | Current state | This cycle |
| --- | --- | --- |
| P1 — Shear prioritization | Drift rendered + shown, **scores zero** | Wire into composite (Tier 1) |
| P2 — Coherence-velocity matrix | Visual desaturation only | Backend classification + score gating (Tier 1) |
| P3 — Elastic vs plastic decoupling | Not implemented | STL decomposition + trend exposure (Tier 2) |
| P4 — Non-linear contextualization | Exponential proximity decay done; load × geology missing | Add load_factor multiplier (Tier 2) |
| P5 — Multi-source geometry fusion | Implemented end-to-end | No change |

Beyond the framework, three gaps a practitioner would flag are folded in:
velocity uncertainty (σ), acceleration, and cohort percentiles. These are the
honest-engineering layer the doc skips.

---

## Tier 1 — risk-engine core (ship first)

### 1. Coherence-velocity classification (P2)

The single highest-leverage change. Today low-coherence buildings are
*desaturated visually* but still contribute their full velocity to the
composite. A noisy 12 mm/yr reading on γ=0.25 dominates a clean -3 mm/yr on
γ=0.85, which is exactly backwards.

#### Backend changes

- `backend/scripts/phenomena.py`: per-building, at end-of-series, classify:

  ```python
  def classify(v_subs, v_ew, gamma):
      v_abs = abs(v_subs)
      ew_abs = abs(v_ew)
      if gamma < 0.35 and (v_abs > 10 or ew_abs > 5):
          return ENV_NOISE          # suppress
      if v_abs < 1.5 and gamma > 0.60:
          return STABLE_ANCHOR      # reference
      if (v_abs > 10 or ew_abs > 2.5) and gamma > 0.60:
          return CONFIRMED_THREAT
      return INDETERMINATE
  ```

  Emit as `classification: uint8` per building (0=INDETERMINATE,
  1=CONFIRMED_THREAT, 2=ENV_NOISE, 3=STABLE_ANCHOR).
- Gate `composite_risk`:
  - ENV_NOISE → multiply composite by 0.2 (don't zero — keep it findable).
  - STABLE_ANCHOR → cap composite at 0.15.
  - CONFIRMED_THREAT → pass through unchanged (or boosted by shear, see #2).
- `backend/scripts/init_db.sql`: expose `classification` on the `buildings`
  view.
- `backend/app/main.py::_build_bundle`: pack `classification` as Uint8
  [n_buildings].

#### Frontend changes

- `frontend/src/lib/bundle.ts`: new `classification: Uint8Array` view +
  exported `Classification` enum mirror.
- `frontend/src/components/ThreatSidebar.tsx`: classification badge above
  the metrics grid in `SelectedBuilding`. Color-coded:
  - CONFIRMED_THREAT — red, "Confirmed structural threat"
  - STABLE_ANCHOR — green, "Reference asset · stable"
  - ENV_NOISE — slate-amber, "Environmental noise · interpret with caution"
  - INDETERMINATE — slate-500, "Indeterminate"
- `NarrativeCard`: rewrite the "severe" stat to count only CONFIRMED_THREAT;
  add a third stat showing `noise` count so the AOI summary is honest about
  what's confidently classified vs. what isn't.

### 2. Wire drift into composite (P1)

Today `composite_risk = 0.55·subs + 0.25·prox + 0.20·soil`. Shear contributes
zero — the principle the framework considers most important is the one the
score ignores.

#### Backend changes

- `backend/scripts/phenomena.py::_composite_risk`: add a fourth term.

  ```python
  shear_score = sigmoid((abs(v_ew_end) - 2.5) / 2.0)   # midpoint at 2.5 mm/yr
  # New weights, re-normalized to sum to 1.0:
  composite = (0.35 * subs_score
              + 0.25 * shear_score
              + 0.20 * prox_score
              + 0.20 * soil_score)
  ```

  Sigmoid (not exponential) is deliberate: the framework says "scale
  exponentially" but exponentials in a [0,1] score blow up and are hard to
  communicate. Sigmoid with midpoint at the doc's 2.5 mm/yr threshold gives
  the same intent without unbounded values. Document this deviation in the
  function docstring.
- Gate on classification from #1: if ENV_NOISE, the shear term is
  multiplied by 0.2 along with the rest of the composite (already handled
  by the outer gate, no extra logic).

#### Frontend changes

- `frontend/src/components/ThreatSidebar.tsx::StackedRiskBar`: grow to
  **four segments** — `[subs | shear | prox | soil]`. Shear color: violet
  (`#a78bfa`) — distinguishable from amber (proximity).
- Legend below the bar: add `<Dot c="#a78bfa" /> shear` next to the existing
  three.

### 3. Acceleration axis (gap not in framework)

A building going -2 → -8 → -15 mm/yr over three quarters is urgent. A steady
-12 mm/yr for two years is chronic. Both look the same in today's sidebar.
This is a one-day add that fundamentally changes triage.

#### Backend changes

- `backend/scripts/phenomena.py`: per-building, compute acceleration over
  the trailing 6 months:

  ```python
  v_recent = velocity_mm_yr[i, -1]
  v_prior  = velocity_mm_yr[i, -7]   # 6 months earlier
  accel_mm_yr2 = (v_recent - v_prior) * 2   # annualized
  ```

  Emit `velocity_accel_mm_yr2: float32` per building.
- `_build_bundle`: pack as Float32 [n_buildings].

#### Frontend changes

- `frontend/src/lib/bundle.ts`: `velocityAccelMmYr2: Float32Array`.
- `frontend/src/components/ThreatSidebar.tsx::SelectedBuilding`: new metric
  cell `Trend` showing one of:
  - `▲ accelerating` (accel < -3 mm/yr²) — red
  - `▼ decelerating` (accel > +3 mm/yr²) — slate
  - `→ steady` otherwise
  Followed by the raw `±X.X mm/yr²` value, tabular.

---

## Tier 2 — modeling depth (next, after Tier 1 lands)

### 4. STL trend decoupling (P3)

Nairobi's black-cotton clay genuinely swells seasonally. The current
end-of-series `velocityAt(end)` misreads rainy-season uplift on Huruma
footprints as "improving." STL fixes this.

**Honest caveat to surface in the UI:** 24 months is the statistical floor
for STL with annual seasonality — two cycles. Confidence intervals on the
trend slope will be wide. Display them; don't hide them. If we can extend
the synthetic series to 36 months in this cycle, do.

#### Backend changes

- New dep: `statsmodels` (already transitive via scipy stack, just add
  explicit requirement in `backend/requirements.txt`).
- `backend/scripts/phenomena.py`: per-building, after series synthesis,
  run STL with `period=12`:

  ```python
  from statsmodels.tsa.seasonal import STL
  stl = STL(displacement_series, period=12, robust=True).fit()
  trend_slope = linregress(np.arange(n_months), stl.trend).slope * 12   # mm/yr
  seasonal_amplitude = stl.seasonal.max() - stl.seasonal.min()           # mm
  r2 = 1 - (stl.resid.var() / displacement_series.var())                 # trend fit quality
  failure_mode = PLASTIC if (trend_slope < -5 and r2 > 0.85) else ELASTIC
  ```

  Emit four new fields: `trend_slope_mm_yr: f4`, `seasonal_amplitude_mm: f4`,
  `trend_r2: f4`, `failure_mode: u1`.
- `_build_bundle`: pack all four.

#### Frontend changes

- `frontend/src/lib/bundle.ts`: four new views.
- `frontend/src/components/ThreatSidebar.tsx::Sparkline`: overlay the trend
  component as a second line (slate-300, dashed) on top of the raw
  displacement series. Shade the seasonal envelope (slate-700, 30% alpha)
  to visually distinguish "breathing with the rain" from "actually sinking."
- Add a `failure_mode` line below the sparkline: either
  `ELASTIC · seasonal soil response` or
  `PLASTIC · progressive foundation failure (R²=0.91)`.

  > **Implementation note:** the sparkline currently receives a
  > `Float32Array` of displacement values. To overlay the trend we'll need
  > to pass the STL trend array too — extend `buildingSeries()` in
  > `bundle.ts` to accept `"trend"` as a `which` argument, backed by a new
  > `trendDisplacementMm` [n_buildings × n_months] section in the bundle.
  > This roughly doubles bundle size for displacement-class arrays; verify
  > the impact (Huruma is ~1500 buildings × 24 months × 4 bytes = ~140 KB,
  > so the doubling is well inside budget).

### 5. Height-weighted soil interaction (P4)

Today a 4-story and a 16-story building on the same alluvial clay get the
same soil contribution. The framework specifically calls out height ×
geology as multiplicative. One-line backend change with outsized impact on
the Mombasa AOI story.

#### Backend changes

- `backend/scripts/phenomena.py::_composite_risk`:

  ```python
  load_factor = 1.0 + (fused_height_m / 10.0)   # 1.0 at 10m, 2.6 at 16m
  soil_score_loaded = min(1.0, soil_score * load_factor)
  ```

  Use `soil_score_loaded` in the composite. Clamp at 1.0 so it can't push
  the bar past full width.

#### Frontend changes

- `frontend/src/components/ThreatSidebar.tsx`: in the legend below the
  stacked bar, change `soil` to `soil × load` and add a one-line note:
  `"Soil contribution scales with building load (height-weighted)."`

---

## Tier 3 — honesty layer (gaps the framework misses)

### 6. Velocity uncertainty propagation

Every InSAR pipeline has per-pixel temporal noise and atmospheric residual
error. Today we publish `velocity_mm_yr` as a point estimate. The
framework's "v > 10 mm/yr" threshold is being applied to a number whose
noise floor in low-coherence pixels may be ±4 mm/yr.

#### Backend changes

- `backend/scripts/phenomena.py`: synthesize `velocity_sigma_mm_yr` and
  `velocity_ew_sigma_mm_yr` from coherence: `σ ≈ k * (1 - γ)` with
  `k ≈ 5 mm/yr` calibrated so γ=0.9 → σ≈0.5, γ=0.3 → σ≈3.5.
- Per-building emit as Float32 [n_buildings] (end-of-series σ; we don't
  need per-month σ for the UI).
- Replace the brittle threshold in `classify()` (Tier 1, #1) with a
  probabilistic version once σ exists:

  ```python
  # "is v_subs < -10 with probability > 0.8?"
  z = (v_subs - (-10)) / sigma
  is_severe = (1 - norm.cdf(z)) > 0.8
  ```

#### Frontend changes

- `frontend/src/components/ThreatSidebar.tsx`: in the `Metric` component for
  Subsidence V and Horizontal Drift, append ` ± σ` in the unit slot
  (smaller, slate-500). The HeightCard's `± σ` styling is the precedent.

### 7. Cohort percentile context

A composite of 0.62 means nothing in isolation. "92nd percentile for shear
among 47 buildings matched on height-band (15±3m) + soil-class (alluvial)
in this AOI" means something. This is the difference between a heatmap and
a decision tool.

#### Backend changes

- `backend/scripts/phenomena.py`: after composite computation, per-building:
  bin by `height_band` (5m buckets) × `soil_class`. Compute percentile
  rank within each cohort for `composite_risk` and for `|v_ew|`. Emit two
  new fields: `cohort_composite_pct: u1` (0-100) and `cohort_shear_pct: u1`.
- `_build_bundle`: pack both as Uint8.

#### Frontend changes

- `frontend/src/components/ThreatSidebar.tsx`: below the composite bar, a
  one-line context string:
  `"Composite: 87th pct  ·  Shear: 92nd pct  among 47 peer buildings"`.
  Peer count from a per-AOI lookup table in the bundle header (or just
  recomputed client-side from the height-band + soil-class arrays).

### 8. Validation hooks (regression test)

The framework doesn't mention ground truth at all. In production this is
the first question a structural engineer client asks. We don't need real
GNSS pins — but we should have placeholder validation buildings with
declared "true" states that the pipeline must reproduce.

#### Backend changes

- New file: `backend/tests/test_classification_invariants.py`. Define 3-5
  named buildings per AOI with expected classifications:
  - `huruma_riparian_failing_high_rise` → CONFIRMED_THREAT, failure_mode=PLASTIC
  - `huruma_stable_inland_lowrise` → STABLE_ANCHOR, failure_mode=ELASTIC
  - `mombasa_seaward_creep_tower` → CONFIRMED_THREAT, shear_pct ≥ 90
  - …
- Test: run the full seed → bundle pipeline in a tmpdir, parse the bundle,
  assert each named building hits the expected classification + failure
  mode. Fails CI if the synthetic generator drifts.

---

## Performance posture

- All new fields land in the binary bundle. Bundle size grows by:
  - Tier 1: +6 bytes/building (classification u1 + accel f4 + shear pct u1).
    Negligible at 1500 buildings (~9 KB).
  - Tier 2: +13 bytes/building × per-month-trend (≈ +140 KB for 24-month
    trend series). Verify under budget; document if it pushes the bundle
    past 2 MB on either AOI.
  - Tier 3: +8 bytes/building (sigma f4 × 2). Negligible.
- No new fetches. No new layers in deck.gl. The stacked risk bar growing
  from three to four segments is a layout change in React, not a render
  change in WebGL.
- STL decomposition is an O(n_months · log n_months) one-time cost at
  seed; not in any hot path.
- Bundle ETag changes whenever any of these fields are added, so stale
  caches invalidate on first reload.

---

## Order of execution

1. **Tier 1 backend**: classification (#1) → composite re-weighting with
   shear term (#2) → acceleration (#3). One re-seed at the end.
2. **Tier 1 frontend**: bundle.ts views → ThreatSidebar classification
   badge → four-segment risk bar → acceleration metric cell.
3. **Drive Playwright + visual confirm**: rerun `scripts/drive-modes.mjs`,
   capture an extra screenshot of an inspected building with badge +
   four-segment bar visible. Spot-check that ENV_NOISE buildings now have
   visibly suppressed composite scores.
4. **Tier 2 backend**: extend phenomena.py with STL + load factor. Re-seed.
   Verify bundle size still under budget.
5. **Tier 2 frontend**: sparkline trend overlay + failure mode line + soil
   × load legend update.
6. **Tier 3 backend**: σ propagation, cohort percentiles, validation test.
7. **Tier 3 frontend**: ± σ in metrics, cohort percentile context string.
8. **Final drive + screenshot pass**: 7 PNGs same as ARCHITECTURE_ONE
   convention, but the inspected building now shows the full risk-engine
   panel — badge, four-segment bar with cohort percentile, σ-annotated
   metrics, trend overlay sparkline, failure mode classification.

---

## What this delivers

A demo that has moved from "beautiful renderer of synthetic state" to
"opinionated risk engine that an engineering firm could be asked to
defend." Every published number carries its uncertainty; every classified
building can be tied back to a deterministic rule; every score can be
contextualized against its peers; and a regression test guards the
classification rules against silent drift.
