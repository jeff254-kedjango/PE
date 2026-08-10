# InSAR pre-MintPy alignment plan (Huruma canonical stack)

Status: **blueprint, pending review.** This is the `splendid-stirring-micali.md` work
referenced in the project notes (that file never existed; this is its real form).

## Why this exists

A diagnostic pass found three real bottlenecks between the 58 downloaded HyP3 pairs
and a clean MintPy SBAS run. All three are **verified on disk** (not speculative):

| # | Bottleneck | Evidence (verified 2026-06-02) |
|---|-----------|--------------------------------|
| 1 | **Grid fragmentation** — MintPy `load_data` requires identical dimensions and silently drops mismatched rasters | 58 `*_unw_phase.tif` span **10 distinct grid sizes**; the dominant `3675×2963` has only **30** pairs. The other 28 are full S1 frames at 9 other sizes. Union extent is 314×336 km, intersection 273×144 km — i.e. these are full frames, not AOI-cropped. |
| 2 | **Reference anchor outside the box** — Karura stable anchor sits outside the 2 km Huruma display tile | Karura `(36.8345, -1.2391)` vs Huruma bbox `lon ∈ [36.865, 36.883]` → **outside**. A reference point outside the loaded subset can't anchor the inversion. |
| 3 | **GACOS sidecars + missing dates** — tropo grids lack `.rsc`, and 3 acquisition dates have no grid | **56 `.ztd.tif`, 0 `.rsc`** on disk. Dates with no GACOS grid: **`20240817`, `20250707`, `20251104`** (3, matches runbook). |

Plus one **non-bottleneck reality**: a full-res time-series cube on the dominant grid is
~2.5 GB (58 epochs) — well within 16 GB. The "117-image → 5.1 GB" figure in the older
notes is a *hypothetical* larger stack; on-disk we have **59 acquisition dates / 58 pairs**.
3×3 multilook drops the cube to ~0.28 GB for fast iteration.

## Design principle

Display geometry (`AOI.side_m`, the 2 km UI tile) and **processing geometry** (the MintPy
load subset) must be allowed to differ. The inversion box must (a) be identical across all
58 pairs, and (b) contain the reference anchor. The UI keeps showing the 2 km tile.

---

## Stage A — Clip all 58 pairs to one common AOI grid

**Goal:** every pair on a byte-identical grid so MintPy keeps all 58 (max temporal density).

- New script `scripts/clip_to_common_grid.py`:
  - Target extent = the **processing bbox** (Stage B's widened box), in UTM 37S, snapped
    to a whole-pixel grid; target size `-ts W H` derived from a fixed ground resolution.
  - For each pair, for each band: `conda run -n mintpy gdalwarp -te <extent> -te_srs EPSG:32737
    -ts W H -r <resample>` into `data/hyp3_work_clipped/huruma/<pair>/`.
    - phase / DEM / incidence → `bilinear`; coherence → `bilinear`; water mask → `near`.
  - Use the **mintpy env gdalwarp** (same pattern as `reproject_hyp3._conda_gdalwarp`) so
    PROJ resolves `proj.db`.
- Clip **before** reproject. Pipeline becomes:
  `clip_to_common_grid → reproject_hyp3 (UTM→4326) → mintpy_run`.
- Verify: `rasterio` reports a **single** `(W,H)` across all 58 clipped `unw_phase`.

## Stage B — Widen the processing subset to include Karura

- Add `processing_side_m: float | None` to the `AOI` dataclass (`scripts/aois.py`); when set,
  `processing_bbox(aoi)` uses it instead of `side_m`. Display/bundle code keeps using `bbox(aoi)`.
- Huruma: `processing_side_m = 10000` — Karura is **4396 m** from centre, so a 5 km half-side
  (10 km box) contains it with ~600 m margin (5 km box / 2.5 km half-side is **too small**).
- Mombasa: `processing_side_m = 9000` — Changamwe is **3840 m** from centre (~660 m margin).
- Verified with the in-bbox check: both anchors fall **outside** the display tile and **inside**
  the processing box.
- **Wiring (done):** `mintpy_run.render_config` previously used `bbox(aoi)` (the 2 km tile)
  for BOTH the subset extent and the `reference_lalo` inside-test — so without this change the
  widened box had no effect and the anchor stayed `auto`. `render_config` now calls
  `processing_bbox(aoi)`; the `bbox` import was dropped (it became unused → would be dead).

## Stage C — GACOS date exclusion (no `.rsc` needed) — ALREADY IMPLEMENTED

**Correction (verified 2026-06-02):** the original "missing `.rsc` → GACOS reader fails"
premise is **false for the `.ztd.tif` format**. MintPy's `tropo_gacos` reads each grid via
`readfile.read_attribute(ztd_file)`, which for a GeoTIFF gets `WIDTH/LENGTH/X_FIRST/Y_FIRST/
X_STEP/Y_STEP` straight from the GDAL geotransform — confirmed by calling it on a real
`huruma/*.ztd.tif`. A `.rsc` sidecar is only needed for legacy headerless binary `.ztd`.
A `gen_gacos_rsc.py` would therefore be **dead code** (writes files MintPy never reads) and
is **dropped from the plan**.

The one real part — excluding dates with no grid — is **already implemented** in
`mintpy_run.render_config` via `_missing_gacos_dates`, and verified to emit exactly
`excludeDate = 20240817,20250707,20251104` (59 SAR dates − 56 GACOS grids). No new code.

- Verify: `correct_troposphere` log says `gacos` and lists ≥ 56 grids; no "0 GACOS files found".

## Stage D — Multilook toggle

- Add `--multilook N` to `mintpy_run.py` → sets `mintpy.multilook.azimuth/range = N`.
  Default off (full-res 2.5 GB is fine on 16 GB); `--multilook 3` (~0.28 GB) for iteration.

## Stage E — Forward guard against footprint-id mislabel (debt cleanup)

Unrelated to the SBAS run but cheap and prevents a recurrence of the Mombasa id mislabel
(OSM ids written into `open_buildings_id` after the registry flip):

- In the footprint loader (`phenomena.py` `_load_real_footprints`) assert:
  `footprint_source == "open_buildings"` ⇒ the parquet has a non-empty `open_buildings_id`
  column; else `raise` with a clear message. Fail loud instead of silently mislabeling.

---

## Sequencing & dependencies

```
Stage B (define processing box)  ──┐
                                   ├─► Stage A (clip to that box)  ─► reproject_hyp3 ─► mintpy_run
Stage C (gacos rsc + excludeDate) ─┘                                         ▲
Stage D (multilook flag) ───────────────────────────────────────────────────┘
Stage E (guard)  — independent, land anytime
```

Stage A depends on Stage B's extent. C and D are independent config additions. E is independent.

## Acceptance (extends the runbook checklist)

Build-time, verified 2026-06-02 (no MintPy run yet):

- [x] All 58 clipped pairs report a single `(W,H)` — `126×125`, down from 10 distinct sizes (Stage A).
- [x] Rendered config `mintpy.subset.lalo` = the widened 10 km box; `mintpy.reference.lalo`
      = the explicit Karura coordinate `-1.23910,36.83450` (was `auto`) (Stage B).
- [x] Rendered config `mintpy.network.excludeDate` = `20240817,20250707,20251104`; MintPy
      reads `.ztd.tif` natively (no `.rsc`) (Stage C).
- [x] Loader raises when an open_buildings AOI's parquet lacks `open_buildings_id` (Stage E);
      full pytest suite green (6 passed / 3 skipped).

Verified by a real isolated run on the mintpy env, 2026-06-03
(`data/mintpy/huruma_ASCENDING_57_clipped/`, `--run-suffix clipped --multilook 1`,
`Normal end of smallbaselineApp processing!`):

- [x] MintPy `load_data` keeps **58** (not 30) interferograms — `ifgramStack.h5`
      `unwrapPhase` shape `(58, 128, 128)`; log line `number of interferograms: 58`.
      The only drops are the deliberate `excludeDate` (3 GACOS-less dates → 6 pairs);
      the coherence/MST filter removed **0** → 52 kept for inversion by design.
- [x] `correct_troposphere` log says `tropospheric delay correction with gacos approach`,
      finds **56** grids; `GACOS.h5` has 56 epochs.
- [x] Stage D built: `--multilook N` (default 1) renders `mintpy.multilook.ystep/xstep`;
      the run log echoes `multilook x/ystep: 1/1` (full-res). The clipped grid is tiny
      (128×128), so full-res is the production path; N>1 remains available for fast iteration.
- [x] No change to the 2 km UI tile or to Huruma's `insar` provenance: live
      `huruma_ASCENDING_57/velocity.h5` mtime stayed 2026-06-01, provenance still `insar`.
      The run wrote only the isolated `_clipped` sibling.

## Out of scope

- Does not touch the live demo DB or `provenance.json` until a real run lands.
- Does not delete `scripts/hyp3_pipeline.py` — see the demote recommendation in the session
  notes (move to `scripts/_reference/`, stub bodies → pointers). It documents the
  asf_search→HyP3 submit flow that has no live replacement.
```
