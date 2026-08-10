# OpenSARLab runbook — real MintPy SBAS with GACOS

The one step that produces **real velocities** for the demo. The laptop can't host
MintPy/ISCE (locked decision, PLAN.md), so the SBAS inversion runs on **ASF OpenSARLab**
(free hosted JupyterHub for Earthdata users, MintPy pre-installed). Everything before this
(HyP3 interferograms, GACOS grids, footprints) and after it (`join_insar.py`) runs locally.

When this runbook is done, `data/mintpy/<aoi>_ASCENDING_<path>/` holds a **real** geocoded
`velocity.h5` + `timeseries.h5` (hundreds of px per side, `troposphericDelay.method=gacos`),
the [dry-run guard](../backend/scripts/join_insar.py) stops firing, and the join produces real
parquet that lights up the existing UI.

> **Why this matters for the broadcast:** the products currently in `data/mintpy/` are
> 25×25 placeholders from `scripts/_dryrun_stage4.py` (height_correlation, not GACOS). The
> map is showing **synthetic** building data because no real SBAS run has happened yet. This
> is the run.

---

## What goes up (per AOI)

| AOI | Track | HyP3 work dir | GACOS dir |
|---|---|---|---|
| huruma | ASCENDING / path 57 | `backend/data/hyp3_work/huruma/` (58 pairs) | `backend/data/raw/env/gacos/huruma/` (56 `*.ztd.tif`) |
| mombasa | ASCENDING / path 159 | `backend/data/hyp3_work/mombasa/` (60 pairs) | `backend/data/raw/env/gacos/mombasa/` (57 `*.ztd.tif`) |

> **Coverage note:** 6 acquisition dates have no GACOS grid (Huruma: 20240817, 20250707,
> 20251104; Mombasa: 20240905, 20250702, 20251205 — never submitted to the portal). MintPy
> skips tropospheric correction on interferograms touching those epochs and proceeds. If you
> want full coverage, resubmit those 6 dates to gacos.net first and re-run
> `python -m scripts.fetch_gacos ingest-dir --dir geospatial_data/gacco-files`. Not a blocker.

The rendered MintPy config also goes up. Generate it **locally** first (it bakes in the GACOS
dir + explicit reference point), then upload it alongside the data:

```bash
cd ~/infra-proptech/backend
source .venv/bin/activate
# Renders data/mintpy/<aoi>_ASCENDING_<path>/config.cfg with GACOS + reference tokens.
# (This only writes the config; it does NOT try to run MintPy locally.)
python - <<'PY'
from scripts.aois import by_code
from scripts.mintpy_run import render_config, MINTPY_DIR
for code, track in (("huruma","ASCENDING/57"), ("mombasa","ASCENDING/159")):
    aoi = by_code(code)
    dest = MINTPY_DIR / f"{code}_{track.replace('/','_')}"
    cfg = render_config(aoi, track, dest)
    print("wrote", cfg)
PY
```

Confirm the rendered config has the right knobs (these are the A1/A3 acceptance items):

```bash
grep -E "troposphericDelay.method|gacosDir|reference.lalo" \
  data/mintpy/huruma_ASCENDING_57/config.cfg
# expect:
#   mintpy.troposphericDelay.method   = gacos
#   mintpy.troposphericDelay.gacosDir = .../data/raw/env/gacos/huruma
#   mintpy.reference.lalo      = -1.23910,36.83450
```

---

## 1. Get on OpenSARLab

1. Sign in at <https://opensciencelab.asf.alaska.edu/> with your Earthdata Login (same account
   as HyP3).
2. Start a server profile that includes **MintPy** (the "Insar Analysis" / SAR profile). Pick
   the largest RAM option offered — SBAS on ~60 pairs is comfortable in 8–16 GB.
3. Open a terminal in JupyterLab.

## 2. Get the inputs onto OpenSARLab

The interferograms are **~16.5 GB** (8.6 GB Huruma + 7.9 GB Mombasa). Don't upload
them — they already live in ASF's cloud keyed by deterministic job names, so the
**same Earthdata account re-downloads them server-side in minutes**. Only GACOS
(~1.1 GB, came from gacos.net, not ASF) and the tiny config actually need uploading.

**2a. Re-download the interferograms on the lab (no upload).** Copy
`backend/scripts/osl_fetch_hyp3.py` up (drag the single file into JupyterLab), then:

```bash
export EARTHDATA_USER=<your_login>      # the SAME login that ran the pipeline
export EARTHDATA_PASS=<your_password>   # or rely on ~/.netrc / interactive prompt
mkdir -p ~/work/huruma
python osl_fetch_hyp3.py --aoi huruma  --dest ~/work/huruma/hyp3_work/huruma
# → ~/work/huruma/hyp3_work/huruma/<pair>/*_unw_phase.tif, *_corr.tif, *_dem.tif, ...
```

It selects jobs by name prefix (`h-A57-` / `m-A159-`), is idempotent (skips products
already extracted), and downloads on a 6-way thread pool. If it reports "no succeeded
jobs", you're signed in with the wrong Earthdata account.

**2b. Upload GACOS + config.** Built by the laptop at `~/osl_upload/`:

```bash
# (already built locally:)
#   ~/osl_upload/huruma_gacos.tgz   (~644 MB)   huruma_cfg.tgz
#   ~/osl_upload/mombasa_gacos.tgz  (~460 MB)   mombasa_cfg.tgz
```

Drag the two `.tgz` for the AOI into JupyterLab, then:

```bash
cd ~/work/huruma
tar xzf ~/huruma_gacos.tgz       # → huruma/<date>.ztd.tif
tar xzf ~/huruma_cfg.tgz         # → mintpy/huruma_ASCENDING_57/config.cfg
```

> If you'd rather upload everything by hand instead of re-downloading, the laptop can
> still build `*_inputs.tgz` — but at 16.5 GB through a browser that's the slow path.

> The config's paths are absolute laptop paths. On OpenSARLab, edit the three path lines to
> match `~/work/<aoi>`:
> ```bash
> cd ~/work/huruma
> sed -i \
>   -e "s#.*mintpy.load.unwFile.*#mintpy.load.unwFile = $PWD/hyp3_work/huruma/*/*_unw_phase.tif#" \
>   -e "s#.*mintpy.load.corFile.*#mintpy.load.corFile = $PWD/hyp3_work/huruma/*/*_corr.tif#" \
>   -e "s#.*mintpy.load.demFile.*#mintpy.load.demFile = $PWD/hyp3_work/huruma/*/*_dem.tif#" \
>   -e "s#.*mintpy.load.incAngleFile.*#mintpy.load.incAngleFile = $PWD/hyp3_work/huruma/*/*_inc_map.tif#" \
>   -e "s#.*mintpy.load.waterMaskFile.*#mintpy.load.waterMaskFile = $PWD/hyp3_work/huruma/*/*_water_mask.tif#" \
>   -e "s#.*mintpy.troposphericDelay.gacosDir.*#mintpy.troposphericDelay.gacosDir = $PWD/huruma#" \
>   mintpy/huruma_ASCENDING_57/config.cfg
> grep -E "load\.|gacosDir|reference.lalo|method" mintpy/huruma_ASCENDING_57/config.cfg
> ```

## 3. Run SBAS

```bash
cd ~/work/huruma/mintpy/huruma_ASCENDING_57
smallbaselineApp.py config.cfg 2>&1 | tee run.log
```

`smallbaselineApp.py` runs the full chain: load_data → modify_network →
reference_point → invert_network → correct_troposphere (**GACOS**) → deramp →
topographic_residual → velocity → geocode. ~10–40 min depending on profile size. It's
restartable — re-running skips completed steps.

Watch for these in the log (they're the A1/A3 acceptance signals):

- `reference_point` step reports the lat/lon you set (not "auto").
- `correct_troposphere` says it's using **gacos** and lists the `.ztd.tif` grids it found.
  If it says "0 GACOS files found", the `gacosDir` path is wrong — fix and re-run that step.
- `velocity` step completes and `geocode` writes `geo_velocity.h5`.

## 4. Sanity-check the products (on OpenSARLab)

```bash
cd ~/work/huruma/mintpy/huruma_ASCENDING_57
info.py velocity.h5 | grep -Ei "WIDTH|LENGTH|REF_LAT|REF_LON|troposphericDelay.method"
#   expect WIDTH/LENGTH in the hundreds (NOT 25), method = gacos,
#   REF_LAT/REF_LON ≈ the reference point.

# Median velocity uncertainty — the A1 acceptance number.
python - <<'PY'
import h5py, numpy as np
with h5py.File("velocity.h5") as f:
    s = f["velocityStd"][:]
s = s[np.isfinite(s)] * 1000  # m/yr → mm/yr
print(f"median σ_v = {np.median(s):.2f} mm/yr   (target Mombasa ≤ 1.5)")
PY
```

A quick-look PNG to eyeball: `view.py velocity.h5 -v -10 10 --save velocity_quicklook.png`
then download and confirm it isn't flat noise.

## 5. Download the real products back to the laptop

MintPy with already-geocoded HyP3 GAMMA input writes the geocoded products **flat in the run
dir** (no `geo/` subdir) — `join_insar.py::_resolve_mintpy_paths` already handles both layouts.
Bring back exactly these into `backend/data/mintpy/<aoi>_ASCENDING_<path>/`, **overwriting** the
placeholders:

```
velocity.h5
timeseries.h5
avgSpatialCoh.h5
inputs/geometryGeo.h5        # incidence angle — join needs it
demErr.h5                    # B3 DEM-error chip (optional but wanted)
closurePhase.h5              # B1 closure-phase badge (only if the step produced it)
config.cfg                   # so the dry-run guard sees method=gacos
smallbaselineApp.log
```

From JupyterLab: select the run dir → Download (or `tar czf out.tgz velocity.h5 timeseries.h5
avgSpatialCoh.h5 inputs/geometryGeo.h5 demErr.h5 config.cfg` and download the one tarball).
On the laptop:

```bash
cd ~/infra-proptech/backend/data/mintpy/huruma_ASCENDING_57
tar xzf ~/Downloads/huruma_out.tgz      # overwrites the 25×25 placeholders
```

Repeat all of section 2–5 for **mombasa / ASCENDING_159**.

## 6. Join → real parquet → live UI (back on the laptop)

```bash
cd ~/infra-proptech/backend && source .venv/bin/activate
python -m scripts.join_insar --aoi huruma  --track ASCENDING/57  --rebuild-db
python -m scripts.join_insar --aoi mombasa --track ASCENDING/159 --rebuild-db
```

The dry-run guard now passes (real grid is hundreds of px + gacos). The join writes
`footprint_source` = `open_buildings` (both huruma and mombasa) — no longer `synthetic` — and
atomic-swaps `demo.duckdb`. The running FastAPI keeps serving on its old handle; the next bundle
fetch is real. Confirm:

```bash
python - <<'PY'
import duckdb
c = duckdb.connect("data/demo.duckdb", read_only=True)
for aoi in ("huruma","mombasa"):
    n, src = c.execute(
        "SELECT count(*), any_value(footprint_source) FROM buildings WHERE aoi_code=?", [aoi]
    ).fetchone()
    print(f"{aoi}: {n} buildings, source={src}")   # source must NOT be 'synthetic'
PY
```

Then hard-refresh the frontend — the disclaimer flips to the real-data provenance line and the
velocities on the map are Sentinel-1-derived.

---

## Mombasa quickstart (inputs already downloaded)

Unlike Huruma's first run, **Mombasa's real inputs are already on the laptop** — no
HyP3 re-download or GACOS re-fetch needed:

- `backend/data/hyp3_work/mombasa/` — 59 HyP3 GAMMA interferogram pairs (UTM)
- `backend/data/raw/env/gacos/mombasa/` — 57 `*.ztd.tif` GACOS grids

What's still missing is the **4326 reproject**, the **real SBAS run**, and the
**Open Buildings footprints**. So for Mombasa you start at the reproject step:

```bash
cd ~/infra-proptech/backend && source .venv/bin/activate

# 0. Footprints — switch is in the registry (footprint_source=open_buildings);
#    fetch the ML footprints (needs GEE / Earth Engine auth).
python -m scripts.open_buildings_footprints --aoi mombasa

# 1. Reproject the UTM HyP3 pairs into the 4326 mirror tree.
python -m scripts.reproject_hyp3 --aoi mombasa
#    → data/hyp3_work_4326/mombasa/<pair>/...

# 2. Render the MintPy config off the 4326 tree (track ASCENDING/159).
HYP3_WORK_DIR=data/hyp3_work_4326 \
  python -m scripts.mintpy_run --aoi mombasa --track ASCENDING/159

# 3. Run SBAS on OpenSARLab (sections 1–5 above, GACOS already present),
#    download real velocity.h5 / timeseries.h5 / avgSpatialCoh.h5 / config.cfg
#    into data/mintpy/mombasa_ASCENDING_159/, overwriting the placeholders.

# 4. Join → real parquet → live UI.
python -m scripts.join_insar --aoi mombasa --track ASCENDING/159 --rebuild-db
```

Acceptance gates for the Mombasa stack (same as the checklist below): real grid
**» 25×25**, `troposphericDelay.method = gacos`, reference point =
**Changamwe Hill (-4.0265, 39.6395)**, median **σ_v ≤ ±1.5 mm/yr**. Until a real
stack lands, `join_insar.py`'s dry-run guard refuses the placeholder — that's
intended.

---

## Acceptance checklist (ARCHITECTURE_THREE A1/A3)

- [ ] `velocity.h5` WIDTH/LENGTH in the hundreds (not 25×25).
- [ ] `info.py velocity.h5` shows `troposphericDelay.method = gacos`.
- [ ] Reference point = the documented lalo (Huruma -1.2391,36.8345; Mombasa -4.0265,39.6395).
- [ ] median σ_v ≤ ±1.5 mm/yr on Mombasa (record both AOIs' numbers in `docs/methodology.md`).
- [ ] Join runs without the dry-run guard firing; `buildings.footprint_source` ≠ `synthetic`.
- [ ] UI velocities and disclaimer both reflect real data.
