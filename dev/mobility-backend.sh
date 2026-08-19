#!/usr/bin/env bash
# Mobility backend — FastAPI on :8004 (the trading layer's realtime dispatch service).
# Same constraint pair as commerce/weespas: imports are rooted at PE.mobility.* (repo root on
# PYTHONPATH) while pydantic-settings reads mobility/.env relative to CWD. So: cd into mobility/
# AND put the repo root on PYTHONPATH.
. "$(dirname "$0")/lib.sh"

PORT="${MOBILITY_PORT:-8004}"
require_port_free "$PORT" "mobility backend"
banner "Mobility backend → http://127.0.0.1:$PORT  (docs: /docs)"
cd "$MOBILITY_DIR"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec "$MOBILITY_VENV/uvicorn" PE.mobility.main:app --reload --port "$PORT"
