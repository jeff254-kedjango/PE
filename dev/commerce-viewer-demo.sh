#!/usr/bin/env bash
# Commerce live-viewer DEMO seeder — keeps a rotating population of LIVE viewers on every real shop
# so the seller console's Viewing Card (§8 Chunk C+) has faces, names and areas to show in a dev
# stack (LOCAL/DEMO ONLY).
#
# A standalone long-running process (NOT Celery — commerce keeps the lean sync stack); run ONE
# alongside the commerce backend. It MUST loop rather than seed once: a heartbeat only counts as
# "live" for LIVE_WINDOW_SECONDS (60), so a one-shot script would leave the card empty within a
# minute and the feature would look broken.
#
# It FABRICATES shop-view heartbeats, so it is double-gated off in production: VIEWER_DEMO_ENABLED
# defaults false AND the process hard-refuses when COMMERCE_ENV=production (see
# commerce/services/viewer_demo.py + core/config.py). On shutdown it backdates its own rows so the
# card drains to empty instead of freezing a population that will never move again.
#
# Viewer identities are REAL weespas user uuids (drawn from commerce's own sellers.user_uuid), so
# the Viewing Card's S2S bridge can resolve them to a name and avatar. Note this means the FACES
# only appear once the bridge secret is wired — see dev/dev.env (PE_USERS_LOOKUP_SECRET); without
# it the rows still seed but every one renders as 'Guest'.
#
# Same constraint pair as the commerce backend: imports are rooted at PE.commerce.* (repo root on
# PYTHONPATH) while pydantic-settings reads commerce/.env relative to CWD. So: cd into commerce/ AND
# put the repo root on PYTHONPATH.
. "$(dirname "$0")/lib.sh"

banner "Commerce live-viewer demo seeder (rotating live viewers — DEV ONLY)"
cd "$COMMERCE_DIR"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

# Running THIS launcher is the explicit opt-in to seed, so enable the feature flag here rather than
# leaving it to the (gitignored, easily-absent) dev.env — otherwise the process just idles as a
# no-op (commerce/.env pins it false for safety). The OS env var overrides that .env value in
# pydantic-settings. This does NOT weaken the prod guard: run_forever() hard-refuses under
# COMMERCE_ENV=production BEFORE it ever reads this flag, so an accidental prod launch still
# fabricates nothing.
export VIEWER_DEMO_ENABLED=true
exec "$COMMERCE_VENV/python" -m PE.commerce.services.viewer_demo
