#!/usr/bin/env bash
# cleanup_idle.sh — delete ONLY confirmed-idle data to limit the WSL2 disk
# footprint. Dry-run by default; pass --yes to actually delete.
#
# Everything removed here is one of:
#   - a dated backup dir/file (a newer canonical exists),
#   - a MintPy run dir that join_insar ALREADY excludes by name
#     (*_bak / *audit / *crashed — see join_insar.py ~line 299),
#   - the raw GACOS archives that were already ingested into data/raw/env/gacos/
#     (verified: every archived date is present in the cache),
#   - a dangerous unused stub, or a regenerable build cache.
#
# It NEVER touches raw radar (data/hyp3_work*), canonical MintPy runs, parquet
# tables, footprints, or the GACOS grid cache.
#
# Usage (from backend/):
#   bash scripts/cleanup_idle.sh            # dry run — show what would go
#   bash scripts/cleanup_idle.sh --yes      # actually delete

set -euo pipefail

YES=0
[[ "${1:-}" == "--yes" ]] && YES=1

# Resolve backend/ as this script's parent's parent, so it works from anywhere.
BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BACKEND_DIR"

# Idle targets, relative to backend/. Edit here if new backups accumulate.
TARGETS=(
  "data/mintpy/huruma_ASCENDING_57_bak_20260531"
  "data/mintpy/huruma_ASCENDING_57_bak_20260603"
  "data/mintpy/huruma_ASCENDING_57_clipped_audit"
  "data/mintpy/mombasa_ASCENDING_159_bak_20260605"
  "data/mintpy/south_c_ASCENDING_57_crashed_18km_20260612"
  "data/parquet/_bak_huruma_20260603"
  "data/footprints/mombasa_bak_20260605.parquet"
  "geospatial_data/gacco-files"
  "raw_radar_data/download_radar.py"
  ".pytest_cache"
)

if [[ $YES -eq 1 ]]; then
  echo "cleanup_idle: DELETING idle data (--yes given)"
else
  echo "cleanup_idle: DRY RUN — nothing will be deleted. Re-run with --yes to act."
fi
echo "backend: $BACKEND_DIR"
echo

total_kb=0
present=()
for t in "${TARGETS[@]}"; do
  if [[ -e "$t" ]]; then
    sz_h="$(du -sh "$t" 2>/dev/null | cut -f1)"
    sz_kb="$(du -sk "$t" 2>/dev/null | cut -f1)"
    total_kb=$(( total_kb + sz_kb ))
    present+=("$t")
    printf "  %-58s %8s\n" "$t" "$sz_h"
  else
    printf "  %-58s %8s\n" "$t" "(absent)"
  fi
done

echo
printf "Reclaimable: ~%s MiB across %d paths\n" "$(( total_kb / 1024 ))" "${#present[@]}"

if [[ $YES -eq 0 ]]; then
  echo
  echo "Dry run only. Run: bash scripts/cleanup_idle.sh --yes"
  exit 0
fi

echo
for t in "${present[@]}"; do
  rm -rf -- "$t"
  echo "  ✓ removed $t"
done

echo
echo "Done. NOTE: deleting files inside WSL frees space in the VM but does NOT"
echo "shrink the Windows ext4.vhdx automatically. To reclaim it on the host, from"
echo "a Windows PowerShell (admin):"
echo "    wsl --shutdown"
echo "    Optimize-VHD -Path <...>\\ext4.vhdx -Mode Full   # Hyper-V hosts"
echo "  or (any edition):  diskpart → select vdisk file=\"...ext4.vhdx\" → compact vdisk"
