#!/usr/bin/env bash
#
# Fetch a PMTiles basemap covering both AOIs (Huruma + Mombasa) into
# frontend/public/tiles/nairobi.pmtiles. The MapLibre style in RiskMap.tsx
# auto-detects this file at boot and lights up the road/building base
# layers; without it the demo still runs on a flat dark canvas.
#
# Strategy: use the official `pmtiles` CLI to extract a Kenya bounding box
# from the Protomaps public daily PMTiles archive. The extract for the
# bbox below is roughly 40-60 MB. The Protomaps daily archive is multi-GB
# but `pmtiles extract` only downloads the byte ranges it needs.
#
# Re-running is a no-op if the output already exists; pass --force to
# overwrite.
#
# Requires: curl, an internet connection. No keys, no accounts.

set -euo pipefail

cd "$(dirname "$0")/.."

OUT_DIR="public/tiles"
OUT_FILE="${OUT_DIR}/nairobi.pmtiles"
PMTILES_BIN="scripts/.bin/pmtiles"

# Kenya bbox covering both Huruma (Nairobi, ~36.87, -1.25) and Mombasa
# Old Town (~39.67, -4.06), with margin. Lon-min, lat-min, lon-max, lat-max.
BBOX_MINLON=36.60
BBOX_MINLAT=-4.30
BBOX_MAXLON=39.90
BBOX_MAXLAT=-1.05

# Protomaps publishes a daily global PMTiles build. URL is stable.
SRC_URL="https://build.protomaps.com/$(date -u +%Y%m%d).pmtiles"

force=0
if [[ "${1:-}" == "--force" ]]; then force=1; fi

if [[ -f "$OUT_FILE" && $force -eq 0 ]]; then
  sz=$(du -h "$OUT_FILE" | cut -f1)
  echo "✓ $OUT_FILE already exists ($sz). Pass --force to overwrite."
  exit 0
fi

mkdir -p "$OUT_DIR" "$(dirname "$PMTILES_BIN")"

if [[ ! -x "$PMTILES_BIN" ]]; then
  echo "→ Installing pmtiles CLI to $PMTILES_BIN"
  uname_s=$(uname -s | tr '[:upper:]' '[:lower:]')
  uname_m=$(uname -m)
  case "$uname_m" in
    x86_64|amd64) arch=x86_64 ;;
    aarch64|arm64) arch=arm64 ;;
    *) echo "Unsupported arch: $uname_m" >&2; exit 1 ;;
  esac
  case "$uname_s" in
    linux) os=Linux ;;
    darwin) os=Darwin ;;
    *) echo "Unsupported OS: $uname_s" >&2; exit 1 ;;
  esac
  ver="1.22.2"
  tarball="go-pmtiles_${ver}_${os}_${arch}.tar.gz"
  url="https://github.com/protomaps/go-pmtiles/releases/download/v${ver}/${tarball}"
  tmpdir=$(mktemp -d)
  trap 'rm -rf "$tmpdir"' EXIT
  echo "  $url"
  curl -fSL --retry 3 -o "$tmpdir/$tarball" "$url"
  tar -xzf "$tmpdir/$tarball" -C "$tmpdir" pmtiles
  mv "$tmpdir/pmtiles" "$PMTILES_BIN"
  chmod +x "$PMTILES_BIN"
fi

echo "→ Extracting Kenya bbox from $SRC_URL"
echo "  bbox = $BBOX_MINLON,$BBOX_MINLAT,$BBOX_MAXLON,$BBOX_MAXLAT"
echo "  out  = $OUT_FILE"

# `pmtiles extract` performs range-request reads against the remote archive,
# so only the tiles inside the bbox are actually downloaded.
"$PMTILES_BIN" extract "$SRC_URL" "$OUT_FILE" \
  --bbox="$BBOX_MINLON,$BBOX_MINLAT,$BBOX_MAXLON,$BBOX_MAXLAT" \
  --maxzoom=15

sz=$(du -h "$OUT_FILE" | cut -f1)
echo "✓ wrote $OUT_FILE ($sz)"
echo "  Restart the Vite dev server and the basemap will appear."
