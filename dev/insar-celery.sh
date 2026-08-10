#!/usr/bin/env bash
# InSAR pipeline Celery worker — broker redis://localhost:6379/2.
# Only needed when you actually run a rebuild (the control API enqueues to it).
# The celery app lives at scripts.pipeline.celery_app:app and is launched from
# the backend dir. Uses InSAR's own venv.
. "$(dirname "$0")/lib.sh"

banner "InSAR pipeline Celery worker (queue: default, broker db /2)"
cd "$INSAR_DIR"
exec "$INSAR_VENV/celery" -A scripts.pipeline.celery_app:app worker \
  --loglevel="${LOGLEVEL:-info}" -n insar@%h
