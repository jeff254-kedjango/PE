#!/usr/bin/env bash
# overnight_download.sh — fetch the missing DESCENDING InSAR data (and stage the
# matching GACOS troposphere job) for huruma, mombasa, south_c, resiliently and
# WITHOUT letting the machine sleep through it.
#
# Why this exists: on WSL2 a Windows sleep freezes the whole Linux VM, so a
# download can't run while asleep. This script holds a Windows wake-lock for its
# duration (released on exit), and the downloader itself retries forever on a
# flaky link (see hyp3_pipeline._retry_forever), so a dropped connection pauses
# rather than fails. Kick it off before bed.
#
# Usage (from backend/):
#   GACOS_EMAIL=you@example.com bash scripts/overnight_download.sh
#   # or:  bash scripts/overnight_download.sh --email you@example.com
#
# The HyP3 download completes unattended. GACOS is a portal job that emails a
# download URL — this script SUBMITS it so it queues overnight; you ingest the
# emailed archive next session with `python -m scripts.fetch_gacos ingest`.

set -uo pipefail   # NOT -e: a step may exit non-zero (e.g. partial), we log and continue

AOIS=(huruma mombasa south_c)   # the three AOIs missing a descending pass

# ---- args / env -------------------------------------------------------------
EMAIL="${GACOS_EMAIL:-}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --email) EMAIL="${2:-}"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BACKEND_DIR"

if [[ -z "$EMAIL" ]]; then
  echo "ERROR: GACOS email required (the portal emails the download URL there)." >&2
  echo "  GACOS_EMAIL=you@example.com bash scripts/overnight_download.sh" >&2
  echo "  (or pass --email you@example.com)" >&2
  exit 2
fi

LOG_DIR="$BACKEND_DIR/data/logs"
mkdir -p "$LOG_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$LOG_DIR/overnight_${STAMP}.log"

# Mirror everything to the log so the morning-after state is readable.
exec > >(tee -a "$LOG") 2>&1

echo "=== overnight_download $STAMP ==="
echo "AOIs: ${AOIS[*]}    log: $LOG"

# ---- keep Windows awake for the duration ------------------------------------
# A background PowerShell holds ES_SYSTEM_REQUIRED|ES_AWAYMODE_REQUIRED until we
# kill it. This is the real lever on WSL2 (systemd-inhibit only blocks Linux
# sleep). Best-effort: if interop is unavailable we proceed anyway — the
# retry-forever downloader still finishes across wake-cycles, just not in one
# unattended night.
WAKE_PID=""
release_wake() {
  if [[ -n "$WAKE_PID" ]] && kill -0 "$WAKE_PID" 2>/dev/null; then
    kill "$WAKE_PID" 2>/dev/null || true
    echo "  released Windows wake-lock"
  fi
}
trap 'release_wake' EXIT INT TERM

if command -v powershell.exe >/dev/null 2>&1; then
  powershell.exe -NoProfile -Command '
    $sig = @"
[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);
"@
    $t = Add-Type -MemberDefinition $sig -Name Pwr -Namespace W32 -PassThru
    $HOLD = [uint32]::Parse("80000041","AllowHexSpecifier")   # CONTINUOUS|SYSTEM_REQUIRED|AWAYMODE
    $null = $t::SetThreadExecutionState($HOLD)
    while ($true) { Start-Sleep -Seconds 60 }
  ' >/dev/null 2>&1 &
  WAKE_PID=$!
  echo "  Windows wake-lock held (pid $WAKE_PID) — machine will not sleep until done"
else
  echo "  ⚠ powershell.exe not reachable; proceeding WITHOUT keep-awake."
  echo "    Download still survives sleeps (retry-forever) but may take several wake-cycles."
fi

# Also inhibit Linux-side idle/sleep where available (belt and suspenders).
# CRITICAL: systemd-inhibit is used as a COMMAND PREFIX below, so if it fails it
# takes the wrapped download down with it. On WSL2 there's no working logind, so
# `systemd-inhibit … cmd` exits "Failed to inhibit: Access denied" WITHOUT ever
# running cmd — which silently skipped the entire HyP3 download on 2026-06-12.
# So we PROBE it on a trivial command first and only keep it if the inner command
# actually executed. The Windows wake-lock above is the real lever regardless.
INHIBIT=()
if command -v systemd-inhibit >/dev/null 2>&1; then
  if [[ "$(systemd-inhibit --what=sleep:idle --why=probe --mode=block \
            echo __inhibit_ok__ 2>/dev/null)" == "__inhibit_ok__" ]]; then
    INHIBIT=(systemd-inhibit --what=sleep:idle --why="InSAR overnight download" --mode=block)
    echo "  Linux sleep-inhibit active"
  else
    echo "  ⚠ systemd-inhibit unavailable (no logind, e.g. WSL2) — skipping it;"
    echo "    relying on the Windows wake-lock + retry-forever downloader."
  fi
fi

PY="${PYTHON:-python}"

# ---- 1. HyP3 descending products (ASC already on disk → reused free) --------
echo
echo "--- [1/2] HyP3 submit+watch+download (DESCENDING) ---"
AOI_FLAGS=()
for a in "${AOIS[@]}"; do AOI_FLAGS+=(--aoi "$a"); done
# Capture the real exit status instead of masking it with `|| echo`. The python
# already streams its own traceback to this log (stderr is teed at the top), so a
# failure here is fully diagnosable rather than a content-free "see log above".
HYP3_RC=0
"${INHIBIT[@]}" "$PY" -m scripts.hyp3_pipeline submit "${AOI_FLAGS[@]}" \
    --tracks both --watch -y || HYP3_RC=$?
if [[ "$HYP3_RC" -eq 0 ]]; then
  echo "  ✓ HyP3 stage finished"
else
  echo "  ❌ HyP3 stage FAILED (exit $HYP3_RC) — see the traceback above. Re-run is idempotent."
fi

# ---- 2. GACOS troposphere job for the new (and any missing) dates -----------
echo
echo "--- [2/2] GACOS submit (troposphere grids for the new DESC dates) ---"
GACOS_RC=0
"$PY" -m scripts.fetch_gacos submit --aoi "${AOIS[@]}" --email "$EMAIL" || GACOS_RC=$?
if [[ "$GACOS_RC" -eq 0 ]]; then
  echo "  ✓ GACOS submit returned cleanly (watch your email for the download URL)"
else
  echo "  ⚠ GACOS submit returned non-zero (exit $GACOS_RC) — see output above."
fi

# Honest status line: never claim success for a stage that returned non-zero.
hyp3_status="✅ products downloaded into data/hyp3_work/<aoi>/"
[[ "$HYP3_RC" -ne 0 ]] && hyp3_status="❌ FAILED (exit $HYP3_RC) — nothing reliably downloaded; re-run the script (idempotent)"
gacos_status="✅ submitted — the portal will email a download URL"
[[ "$GACOS_RC" -ne 0 ]] && gacos_status="⚠ submit returned non-zero (exit $GACOS_RC) — may not have queued; check output above"

cat <<BANNER

============================================================
OVERNIGHT RUN COMPLETE — $(date -u +%Y%m%dT%H%M%SZ)
  • HyP3 descending: $hyp3_status
  • GACOS troposphere: $gacos_status

NEXT SESSION (manual, can't be automated — portal is human-in-loop):
  1. Download the GACOS archive from the emailed URL.
  2. python -m scripts.fetch_gacos ingest --aoi <code> --archive <that.tar.gz>
  3. Re-run MintPy for the new DESCENDING track, then join_insar, then verify:
       python -m scripts.check_coverage     # target: all AOIs ✅
Full log: $LOG
============================================================
BANNER

# Exit non-zero if the critical (HyP3) stage failed, so callers/cron can detect it
# instead of seeing a 0 exit behind a cheerful banner.
[[ "$HYP3_RC" -eq 0 ]] || exit 1
