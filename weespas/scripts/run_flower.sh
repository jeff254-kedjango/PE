#!/usr/bin/env bash
# Run Flower on a private port. Front this with your reverse proxy's admin
# auth — Flower itself has only HTTP basic auth, which is not enough for
# production. Never expose 5555 on the public LB.

set -euo pipefail

# Package is imported as PE.weespas.* — load the app by its full path with the repo
# root (parent of PE/) on PYTHONPATH. (See run_workers.sh.)
WEESPAS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$WEESPAS_DIR/../.." && pwd)"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
cd "$WEESPAS_DIR"

PORT=${FLOWER_PORT:-5555}
BASIC_AUTH=${FLOWER_BASIC_AUTH:-admin:jeff0713083378}

exec celery -A PE.weespas.core.celery_app flower \
    --port="$PORT" \
    --basic-auth="$BASIC_AUTH" \
    --persistent=True \
    --db=/var/lib/celery/flower.db
