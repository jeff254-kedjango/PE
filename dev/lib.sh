#!/usr/bin/env bash
# Shared helpers for the PE/dev launchers. Sourced, not executed.
set -euo pipefail

PE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # /home/jeff/PE
REPO_ROOT="$(dirname "$PE_ROOT")"                            # /home/jeff
WEESPAS_DIR="$PE_ROOT/weespas"
WEESPAS_FE_DIR="$PE_ROOT/weespas-frontend"
INSAR_DIR="$PE_ROOT/InSAR-Final-main/backend"
INSAR_FE_DIR="$PE_ROOT/InSAR-Final-main/frontend"
COMMERCE_DIR="$PE_ROOT/commerce"
MOBILITY_DIR="$PE_ROOT/mobility"

WEESPAS_VENV="$WEESPAS_DIR/.venv/bin"
INSAR_VENV="$INSAR_DIR/.venv/bin"
COMMERCE_VENV="$COMMERCE_DIR/.venv/bin"
MOBILITY_VENV="$MOBILITY_DIR/.venv/bin"

# Optional shared dev env.
[ -f "$PE_ROOT/dev/dev.env" ] && set -a && . "$PE_ROOT/dev/dev.env" && set +a

# Refuse to start if a port is already taken — clearer than a uvicorn stacktrace.
require_port_free() {
  local port="$1" name="$2"
  if command -v ss >/dev/null 2>&1 && ss -ltn "( sport = :$port )" 2>/dev/null | grep -q ":$port"; then
    echo "✗ Port $port is already in use — cannot start $name." >&2
    echo "  Find it with:  ss -ltnp '( sport = :$port )'" >&2
    exit 1
  fi
}

banner() { echo "▶ $*"; }
