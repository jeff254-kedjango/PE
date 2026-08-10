#!/usr/bin/env bash
# Run all four Celery worker pools — one per queue class.
#
# Why split: a 30-second `compute_listing_benchmarks` job must NEVER delay a
# 200ms `send_otp`. Each queue gets its own concurrency budget tuned to the
# expected job profile (short/IO-bound → more workers; heavy/CPU-bound → fewer).
#
# Run in foreground in dev; in prod use a process manager (systemd / supervisord)
# and point each unit at the matching `celery ... worker -Q <queue>` line.

set -euo pipefail

# The package is imported as PE.weespas.*, so the app must be loaded as
# `PE.weespas.core.celery_app` (NOT the bare `core.celery_app` — that both fails to
# import `from PE.weespas...` AND would register tasks on a different module object
# than the worker runs). For that import to resolve, the repo root (the parent of
# PE/, i.e. /home/jeff) must be on PYTHONPATH. WEESPAS_DIR = scripts/.. ; REPO_ROOT
# is two levels above that.
#
# The same "one module, one identity" rule applies to `celery_app.include`, which now
# lists fully-qualified `PE.weespas.services.*` paths. It used to list bare
# `services.*`, which loaded each file a SECOND time under a different module name —
# duplicating module-level state and registering unnamed tasks under whichever
# identity imported them first. That is what produced
# "Received unregistered task of type 'PE.weespas.services.personalization_tasks...'".
# Because the include list is now absolute, task discovery no longer depends on cwd.
# The `cd` below is still load-bearing for a different reason: core/config.py sets
# `env_file=".env"` (a RELATIVE path), so the worker must start in weespas/ or
# Settings() raises "database_url Field required" at import time.
WEESPAS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$WEESPAS_DIR/../.." && pwd)"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
cd "$WEESPAS_DIR"

APP="PE.weespas.core.celery_app"
LOGLEVEL=${LOGLEVEL:-info}

# auth: OTP send, last_seen touch — short, latency-sensitive, retry-friendly.
celery -A "$APP" worker -Q auth -c 4 -n auth@%h --loglevel="$LOGLEVEL" &

# analytics: log writes + Beat aggregators — mixed; cap concurrency so a slow
# nightly benchmark doesn't run 8 wide and exhaust DB connections.
celery -A "$APP" worker -Q analytics -c 2 -n analytics@%h --loglevel="$LOGLEVEL" &

# feeds: cache warmers, invalidations, prewarms — short, Redis-heavy.
celery -A "$APP" worker -Q feeds -c 4 -n feeds@%h --loglevel="$LOGLEVEL" &

# media: image WebP conversion, ffmpeg thumbnails — CPU-bound, keep narrow.
celery -A "$APP" worker -Q media,default -c 2 -n media@%h --loglevel="$LOGLEVEL" &

# Catch-all for the `default` queue and anything the router missed.
# Combined with media above so the legacy image tasks (default queue in older
# code) still get picked up during the rollout phase.

wait
