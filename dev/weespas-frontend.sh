#!/usr/bin/env bash
# Weespas frontend — Vite on :5174 (set in vite.config.ts; 5173 belongs to InSAR FE).
# Talks to the Weespas backend via VITE_API_BASE_URL (default http://127.0.0.1:8000/api/v1).
. "$(dirname "$0")/lib.sh"

PORT="${WEESPAS_FE_PORT:-5174}"
require_port_free "$PORT" "weespas frontend"
banner "Weespas frontend → http://localhost:$PORT"
cd "$WEESPAS_FE_DIR"
exec npm run dev -- --port "$PORT"
