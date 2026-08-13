#!/usr/bin/env bash
# Weespas backend — FastAPI on :8000 (ngrok forwards here for the M-Pesa callback).
# Two constraints pull in opposite directions:
#   • imports are rooted at PE.weespas.* → the repo root (/home/jeff) must be on
#     PYTHONPATH, and
#   • pydantic-settings reads weespas/.env relative to the CWD → we must run from
#     inside weespas/.
# So: cd into weespas/ AND put the repo root on PYTHONPATH.
. "$(dirname "$0")/lib.sh"

PORT="${WEESPAS_PORT:-8000}"
require_port_free "$PORT" "weespas backend"
banner "Weespas backend → http://127.0.0.1:$PORT  (docs: /docs)"
cd "$WEESPAS_DIR"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

# Serving side of the identity bridge (§8 Chunk C+): weespas OWNS user names/avatars, commerce
# calls POST /commerce/users/lookup to resolve them for the Viewing Card. Same single
# PE_USERS_LOOKUP_SECRET as commerce-backend.sh, under this service's peer-named variable — a
# MISMATCH fails closed (401), which on the card is indistinguishable from unset, so both
# launchers must read the one value. Unset ⇒ endpoint stays 503 (fail-closed by design).
if [ -n "${PE_USERS_LOOKUP_SECRET:-}" ]; then
  export COMMERCE_USERS_LOOKUP_SECRET="$PE_USERS_LOOKUP_SECRET"
fi

exec "$WEESPAS_VENV/uvicorn" PE.weespas.main:app --reload --port "$PORT"
