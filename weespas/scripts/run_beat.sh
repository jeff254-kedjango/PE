#!/usr/bin/env bash
# Run the Celery Beat scheduler with a Redis leader-lease.
#
# Why the lease: Beat is single-leader-by-design. Running it twice double-fires
# every aggregation. In multi-replica deployments, this wrapper makes losing
# replicas wait until the current leader's lease expires (see
# services/celery_helpers.py:acquire_beat_lease).
#
# Schedule file lives outside the deploy unit so a redeploy doesn't blow away
# the last-fire timestamps and trigger every task on startup.

set -euo pipefail

# Package is imported as PE.weespas.* — load the app by its full path and put the
# repo root (parent of PE/) on PYTHONPATH so it resolves. (See run_workers.sh.)
WEESPAS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$WEESPAS_DIR/../.." && pwd)"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
cd "$WEESPAS_DIR"

APP="PE.weespas.core.celery_app"
SCHEDULE_FILE=${BEAT_SCHEDULE_FILE:-/var/lib/celery/beat-schedule}
PIDFILE=${BEAT_PIDFILE:-/tmp/celerybeat.pid}
LOGLEVEL=${LOGLEVEL:-info}

mkdir -p "$(dirname "$SCHEDULE_FILE")"

# Acquire-or-wait loop. In dev we usually run a single Beat so this exits the
# first try; in prod replicas will idle here until the leader fails over.
python - <<'PY'
import sys, time
from PE.weespas.services.celery_helpers import acquire_beat_lease, renew_beat_lease

# Background renewal thread so Beat keeps the lease while running.
import threading
def renew_forever():
    while True:
        time.sleep(30)
        if not renew_beat_lease():
            # Lost the lease — exit so the wrapper can re-acquire (or stay down
            # and let another replica run Beat).
            print("BEAT: lost leadership lease, exiting", flush=True)
            import os
            os._exit(1)

while True:
    if acquire_beat_lease():
        print("BEAT: leadership acquired", flush=True)
        threading.Thread(target=renew_forever, daemon=True).start()
        break
    print("BEAT: another replica holds the lease, retrying in 15s", flush=True)
    time.sleep(15)
PY

exec celery -A "$APP" beat \
    -s "$SCHEDULE_FILE" \
    --pidfile="$PIDFILE" \
    --loglevel="$LOGLEVEL"
