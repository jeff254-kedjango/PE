#!/usr/bin/env bash
# Commerce trending DEMO seeder — keeps a live, looping population of boosted PRODUCTS near a demo
# centre so the §8 trending rail visibly cycles in a dev stack (LOCAL/DEMO ONLY).
#
# A standalone long-running process (NOT Celery — commerce keeps the lean sync stack); run ONE
# alongside the commerce backend. It FABRICATES synthetic sellers/listings/boosts, so it is
# double-gated off in production: TRENDING_DEMO_ENABLED defaults false AND the process hard-refuses
# when COMMERCE_ENV=production (see commerce/services/trending_demo.py + core/config.py). On
# shutdown it revokes everything it created, leaving the queue clean.
#
# Same constraint pair as the commerce backend: imports are rooted at PE.commerce.* (repo root on
# PYTHONPATH) while pydantic-settings reads commerce/.env relative to CWD. So: cd into commerce/ AND
# put the repo root on PYTHONPATH.
. "$(dirname "$0")/lib.sh"

banner "Commerce trending demo seeder (looping boosted products — DEV ONLY)"
cd "$COMMERCE_DIR"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

# Running THIS launcher is the explicit opt-in to seed, so enable the feature flag here rather than
# leaving it to the (gitignored, easily-absent) dev.env — otherwise the process just idles as a
# no-op (commerce/.env pins it false for safety). The OS env var overrides that .env value in
# pydantic-settings. This does NOT weaken the prod guard: run_forever() hard-refuses under
# COMMERCE_ENV=production BEFORE it ever reads this flag, so an accidental prod launch still
# fabricates nothing.
export TRENDING_DEMO_ENABLED=true
exec "$COMMERCE_VENV/python" -m PE.commerce.services.trending_demo
