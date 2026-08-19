#!/usr/bin/env bash
# Weespas Celery workers — broker redis://localhost:6379/1.
# The celery_app `include=` list uses short module names (services.*), so the
# worker MUST run from inside weespas/ (this is what scripts/run_workers.sh does).
# This wrapper just runs that existing script with the right venv on PATH.
. "$(dirname "$0")/lib.sh"

banner "Weespas Celery workers (auth/analytics/feeds/media + default)"
cd "$WEESPAS_DIR"
export PATH="$WEESPAS_VENV:$PATH"
exec bash scripts/run_workers.sh
