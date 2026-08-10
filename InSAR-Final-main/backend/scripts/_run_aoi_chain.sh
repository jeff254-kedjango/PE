#!/usr/bin/env bash
# Heavy, EE-independent InSAR chain for one AOI: clip -> reproject -> MintPy.
# Resumable (each stage is idempotent). Logs to data/mintpy/<aoi>_run.log.
# Footprints + join + provenance run separately (scripts.join_insar).
#
# Usage: scripts/_run_aoi_chain.sh <aoi_code> [track]
#   track defaults to ASCENDING/57. For the descending pass that lets the join
#   decompose vertical + east-west drift, pass e.g. DESCENDING/79:
#     scripts/_run_aoi_chain.sh kileleshwa DESCENDING/79
set -euo pipefail
cd "$(dirname "$0")/.."   # -> backend/

AOI="${1:?usage: _run_aoi_chain.sh <aoi_code> [track]}"
TRACK="${2:-ASCENDING/57}"
TRACK_SAFE="${TRACK//\//_}"      # ASCENDING/57 -> ASCENDING_57
PY=.venv/bin/python
LOG="data/mintpy/${AOI}_${TRACK_SAFE}_run.log"
mkdir -p data/mintpy
echo "=== ${AOI} [${TRACK}] heavy chain start: $(date -u +%FT%TZ) ===" | tee -a "$LOG"

# Clip + reproject are per-AOI and orbit-agnostic (they process every pair dir
# under the AOI, ascending and descending alike), so they're idempotent no-ops
# on a second track once the first has run.
echo "[1/3] clip_to_common_grid --aoi ${AOI}" | tee -a "$LOG"
$PY -m scripts.clip_to_common_grid --aoi "$AOI" 2>&1 | tee -a "$LOG"

echo "[2/3] reproject_hyp3 --aoi ${AOI} --src data/hyp3_work_clipped" | tee -a "$LOG"
$PY -m scripts.reproject_hyp3 --aoi "$AOI" --src data/hyp3_work_clipped 2>&1 | tee -a "$LOG"

echo "[3/3] mintpy_run --aoi ${AOI} --track ${TRACK} (HYP3_WORK_DIR=data/hyp3_work_4326)" | tee -a "$LOG"
HYP3_WORK_DIR=data/hyp3_work_4326 $PY -m scripts.mintpy_run --aoi "$AOI" --track "$TRACK" 2>&1 | tee -a "$LOG"

echo "=== ${AOI} [${TRACK}] heavy chain DONE: $(date -u +%FT%TZ) ===" | tee -a "$LOG"
echo "MINTPY_OK velocity.h5: $(ls -la data/mintpy/${AOI}_${TRACK_SAFE}/velocity.h5 2>/dev/null || echo MISSING)" | tee -a "$LOG"
