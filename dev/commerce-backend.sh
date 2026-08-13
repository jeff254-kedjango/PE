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

# Identity bridge (§8 Chunk C+): commerce → weespas POST /commerce/users/lookup, which puts real
# names + profile pictures on the seller console's Viewing Card. One PE_USERS_LOOKUP_SECRET in
# dev/dev.env feeds BOTH services under their own peer-named variables (weespas-backend.sh exports
# the mirror). Only exported when actually set, so an absent dev.env leaves the fail-closed default
# ("" ⇒ no call, every viewer 'Guest') rather than an empty override.
if [ -n "${PE_USERS_LOOKUP_SECRET:-}" ]; then
  export WEESPAS_USERS_LOOKUP_SECRET="$PE_USERS_LOOKUP_SECRET"
else
  echo "  ⓘ PE_USERS_LOOKUP_SECRET unset in dev/dev.env — live-viewer names/avatars will show" >&2
  echo "    as 'Guest' (identity bridge disabled). See dev/dev.env.example." >&2
fi

exec "$COMMERCE_VENV/uvicorn" PE.commerce.main:app --reload --port "$PORT"
