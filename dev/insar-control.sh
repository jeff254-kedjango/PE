#!/usr/bin/env bash
# InSAR pipeline control API — FastAPI on :8001. Enqueues Celery rebuild tasks;
# Weespas calls it (debounced) when a structural flag is recorded. Auth is a single
# shared X-Admin-Token: it returns 503 on refresh calls unless INSAR_ADMIN_TOKEN is
# set (put the same value in weespas/.env). Uses InSAR's own venv.
. "$(dirname "$0")/lib.sh"

PORT="${INSAR_CONTROL_PORT:-8001}"
require_port_free "$PORT" "InSAR control API"
if [ -z "${INSAR_ADMIN_TOKEN:-}" ]; then
  echo "⚠ INSAR_ADMIN_TOKEN not set — control API will 503 on refresh calls." >&2
  echo "  Set it in PE/dev/dev.env (and matching weespas/.env) to enable rebuilds." >&2
fi
banner "InSAR control API → http://127.0.0.1:$PORT"
cd "$INSAR_DIR"
exec "$INSAR_VENV/uvicorn" scripts.pipeline.control_api:app --reload --port "$PORT"
