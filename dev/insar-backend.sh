#!/usr/bin/env bash
# InSAR read app — FastAPI on :8002 (moved off :8000 to avoid the Weespas clash).
# Read-only over data/demo.duckdb; no live network. Uses InSAR's OWN venv
# (fastapi>=0.110, separate from weespas's fastapi==0.104.1).
. "$(dirname "$0")/lib.sh"

PORT="${INSAR_PORT:-8002}"
require_port_free "$PORT" "InSAR read app"
banner "InSAR read app → http://127.0.0.1:$PORT  (health: /health)"
cd "$INSAR_DIR"
exec "$INSAR_VENV/uvicorn" app.main:app --reload --port "$PORT"
