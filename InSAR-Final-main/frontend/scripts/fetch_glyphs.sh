#!/usr/bin/env bash
#
# Fetch the Noto Sans Regular glyph ranges (.pbf) into
# frontend/public/fonts/. The MapLibre style in RiskMap.tsx serves these at
# `/fonts/{fontstack}/{range}.pbf`; without them, the street-name and
# locality label layers render *nothing* (a symbol layer with a text-field
# needs a glyph source — there is no fallback).
#
# Source: the Protomaps `basemaps-assets` repo, which publishes the same
# Noto stacks the public PMTiles archive was authored against — so the
# fonts match the tiles fetched by fetch_pmtiles.sh.
#
# We self-host (rather than hot-link a CDN) so the demo works offline and
# carries no runtime external dependency, mirroring the local-first
# `pmtiles:///tiles/...` basemap.
#
# Re-running is a no-op for ranges already present; pass --force to refetch.
#
# Requires: curl, an internet connection. No keys, no accounts.

set -euo pipefail

cd "$(dirname "$0")/.."

# The single stack referenced by `text-font` in RiskMap.tsx. Add more here
# only if a layer starts using them (e.g. "Noto Sans Medium").
FONTSTACK="Noto Sans Regular"
OUT_DIR="public/fonts/${FONTSTACK}"

# The exact ranges MapLibre requests for these (Latin-script) Kenya tiles.
# Each range is 256 codepoints. We vendor only what the labels actually use
# rather than the full 256-range BMP (~20 MB) — but the set MUST be complete,
# because an un-vendored range is served by Vite as the index.html SPA
# fallback (HTTP 200, text/html), which MapLibre then fails to parse, dropping
# the glyph silently. The four ranges the app demands today plus their block
# neighbours (Latin/IPA/phonetic, Latin-Extended-Additional, and the
# punctuation/symbol blocks that carry the `·` middot, en-dash, etc.):
#   0-2047    Basic Latin .. Latin Extended-B + IPA + spacing modifiers
#   7424-7935 Phonetic Extensions, Latin Extended Additional
#   8192-8703 General Punctuation, super/subscripts, currency, letterlike, arrows
RANGES=(
  0-255 256-511 512-767 768-1023 1024-1279 1280-1535 1536-1791 1792-2047
  7424-7679 7680-7935
  8192-8447 8448-8703
)

# raw.githubusercontent serves the repo's files directly; URL is stable.
BASE_URL="https://raw.githubusercontent.com/protomaps/basemaps-assets/main/fonts"

force=0
if [[ "${1:-}" == "--force" ]]; then force=1; fi

mkdir -p "$OUT_DIR"

# URL-encode the spaces in the stack name for the remote path.
enc_stack="${FONTSTACK// /%20}"

fetched=0
for range in "${RANGES[@]}"; do
  out="${OUT_DIR}/${range}.pbf"
  if [[ -f "$out" && $force -eq 0 ]]; then
    continue
  fi
  url="${BASE_URL}/${enc_stack}/${range}.pbf"
  echo "→ ${FONTSTACK}/${range}.pbf"
  curl -fSL --retry 3 -o "$out" "$url"
  fetched=$((fetched + 1))
done

if [[ $fetched -eq 0 ]]; then
  echo "✓ ${OUT_DIR} already complete (${#RANGES[@]} ranges). Pass --force to refetch."
else
  echo "✓ wrote ${fetched} glyph range(s) to ${OUT_DIR}"
  echo "  Restart the Vite dev server and the basemap labels will appear."
fi
