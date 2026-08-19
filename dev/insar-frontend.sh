#!/usr/bin/env bash
# InSAR frontend — Vite on :5173. Its dev proxy forwards /api → the InSAR read app
# on :8002 (set in frontend/vite.config.ts; override with VITE_INSAR_API_TARGET).
. "$(dirname "$0")/lib.sh"

PORT="${INSAR_FE_PORT:-5173}"
require_port_free "$PORT" "InSAR frontend"
banner "InSAR frontend → http://localhost:$PORT  (proxies /api → :8002)"
cd "$INSAR_FE_DIR"
exec npm run dev -- --port "$PORT"
