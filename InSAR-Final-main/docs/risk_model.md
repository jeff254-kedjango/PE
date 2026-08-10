# Composite Risk Score — MVP Heuristic

The "composite_risk" field on each building is a **deliberately simple, explainable** weighted sum. It is *not* a calibrated collapse probability. It exists so the demo has a single 0..1 number to color buildings by; every input is surface-able in the sidebar so a viewer can see why a number is high or low.

## Formula

```
composite_risk = 0.55 * subsidence_score
               + 0.25 * riparian_score
               + 0.20 * soil_score
```

| Input | Score function | Rationale |
|---|---|---|
| `subsidence_score` | `clamp(-velocity_mm_yr / 25, 0, 1)` | -25 mm/yr is severe; tune later from real distribution |
| `riparian_score`   | `exp(-riparian_dist_m / 400)` | Decays smoothly; informal builds on riparian land are over-represented in collapse reports |
| `soil_score`       | lookup: black_cotton=0.9, alluvial=0.7, red_clay=0.4, weathered_basalt=0.1 | Coarse local geotech proxy |

## What this score does NOT capture

- **Construction quality.** The dominant collapse driver in Nairobi (cement ratios, rebar specification, illegal vertical additions). Without inspection or permit data, no remote-sensing model can see it.
- **Sudden events.** Flash floods, point loads from new construction next door, foundation undermining from neighboring excavation.
- **Building age and material.** We hold `built_year` but don't weight it yet — too easy to overfit without ground truth.
- **InSAR uncertainty.** A coherence-weighted error bar should accompany every velocity. Add this before showing the score to anyone with authority to act on it.

## Calibration plan

1. Geocode a set of historical collapse incidents (NCA reports, news archive).
2. For each incident: pull our score 6 months before the event.
3. Tune weights against AUC of `score(t-6mo)` predicting `collapse(t)`.
4. Report performance honestly — even after calibration this is likely an aid to triage, not a sufficient signal to evict or condemn.
