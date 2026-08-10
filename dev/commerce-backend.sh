#!/usr/bin/env bash
# Commerce backend — FastAPI on :8003 (the trading layer's social marketplace service).
# Same constraint pair as weespas: imports are rooted at PE.commerce.* (repo root on
# PYTHONPATH) while pydantic-settings reads commerce/.env relative to CWD. So: cd into
# commerce/ AND put the repo root on PYTHONPATH.
. "$(dirname "$0")/lib.sh"

PORT="${COMMERCE_PORT:-8003}"
require_port_free "$PORT" "commerce backend"
banner "Commerce backend → http://127.0.0.1:$PORT  (docs: /docs)"
cd "$COMMERCE_DIR"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec "$COMMERCE_VENV/uvicorn" PE.commerce.main:app --reload --port "$PORT"
