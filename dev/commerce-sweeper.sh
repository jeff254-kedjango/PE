#!/usr/bin/env bash
# Commerce expiry sweeper — expires pending negotiations past their TTL (§7).
# A standalone long-running process (NOT Celery — commerce keeps the lean sync stack);
# run ONE of these alongside the commerce backend. Reads the same commerce/.env, so it
# uses the same PostGIS DB. Cadence + on/off come from EXPIRY_SWEEP_INTERVAL_SECONDS /
# EXPIRY_SWEEP_ENABLED (see commerce/core/config.py).
#
# Same constraint pair as the commerce backend: imports are rooted at PE.commerce.* (repo
# root on PYTHONPATH) while pydantic-settings reads commerce/.env relative to CWD. So:
# cd into commerce/ AND put the repo root on PYTHONPATH.
. "$(dirname "$0")/lib.sh"

banner "Commerce expiry sweeper (TTL sweep of pending negotiations)"
cd "$COMMERCE_DIR"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec "$COMMERCE_VENV/python" -m PE.commerce.services.expiry_sweeper
