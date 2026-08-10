# Commerce live e2e (Playwright)

API-level end-to-end tests that run against the **live commerce server** (real PostGIS, real
RS256 verification, Redis denylist) — not the SQLite unit path. This is the layer that catches
backend-only bugs the unit tests structurally cannot, e.g. Postgres `VARCHAR` length
enforcement (SQLite ignores it).

## Run everything: `run_all.js` (the standing loop)

One entry point runs every fast live check in sequence and exits non-zero if any fails — the
"always run Playwright alongside vitest + pytest" rule as a single command. It preflights the three
services (weespas :8000, commerce :8003, FE :5173) and **skips** (does not fail) any script whose
services are down; a script that actually runs and fails does fail the loop. Every child inherits a
fully-populated env (base URLs + bridge-login creds), so no per-script env line is needed.

```bash
NODE_PATH=/home/jeff/PE/InSAR-Final-main/frontend/node_modules node PE/commerce/e2e/run_all.js
#   --with-perf    also run trending.perf.js  (~120s, seeds ~200 boosted listings)
#   --with-sweep   flags expiry_sweep_live.py (DB-direct — see "Expiry sweep" below; run per its recipe)
```

The heavy/DB checks are excluded from the default loop by design (slow, or need non-HTTP DB creds).
`jwt.js` is a token-minting helper the others `require`, never run on its own. Individual scripts can
still be run directly with the recipes below when iterating on one feature.

## Prerequisites

- Commerce server running on `:8003` (`PE/dev/commerce-backend.sh`), with its PostGIS DB,
  `COMMERCE_JWT_PUBLIC_KEY_PATH` set to the dev key, and Redis up (settlement denylist).
- Playwright is installed in `PE/InSAR-Final-main/frontend/node_modules` (not in commerce).
  Tokens are minted in-process with the dev **private** key via `jwt.js` — no extra deps.

## Run

```bash
cd PE/commerce/e2e
NODE_PATH=/home/jeff/PE/InSAR-Final-main/frontend/node_modules \
  COMMERCE_BASE_URL=http://127.0.0.1:8003 \
  node commerce.e2e.js
```

`NODE_PATH` points Node at the only `node_modules` that has `playwright`. `COMMERCE_BASE_URL`
defaults to `http://127.0.0.1:8003` if unset.

Each run uses a unique `RUN` suffix on all identifiers, so it is safe to re-run against the
persistent PostGIS DB without colliding on one-open-order / one-review-per-order constraints.

## What it covers

settlement (open → settle, 3% commission, stub rail) · receipts (issued on settle, money
split, hash-bound to the settle_ok chain tip, parties-only) · reviews (proof-of-purchase gate:
buyer-of-settled-order only, seller-own-sale 403, unsettled 409, one-per-order 409, rating
bounds) · rating badge on the feed and the public storefront · public storefront (in-stock
only, no POS-internal leak, embedded rating, 404 / 401).

## FE-1 buyer-feed bridge e2e (`trade.fe.e2e.js`)

Exercises the **real weespas→commerce bridge** the Trade frontend uses, end to end over HTTP:
`weespas /auth/login` → `weespas /commerce/session-token` (mints RS256) → `commerce /feed` +
`/sellers/{id}/storefront` (verifies RS256, public key only). This is the layer that catches
cross-service auth/config bugs the per-service tests can't — e.g. the bridge minting HS256 (RS256
disabled in weespas `.env`), which the commerce verifier rejects so every bridge token silently
401s. It seeds a far Sovereign-boosted listing (in-process token) and asserts the bridge-authed
buyer sees it labelled sponsored.

```bash
cd PE/commerce/e2e
NODE_PATH=/home/jeff/PE/InSAR-Final-main/frontend/node_modules \
  WEESPAS_BASE_URL=http://127.0.0.1:8000 COMMERCE_BASE_URL=http://127.0.0.1:8003 \
  WEESPAS_EMAIL=admin@weespas.com WEESPAS_PASSWORD=admin123 \
  node trade.fe.e2e.js
```

Needs the weespas backend (`:8000`) up with `INSAR_JWT_PRIVATE_KEY_PATH` / `INSAR_JWT_PUBLIC_KEY_PATH`
set in `weespas/.env` (so the bridge mints RS256), in addition to the commerce prerequisites above.

## FE-2a seller-console e2e (`seller.fe.e2e.js`)

Exercises the **two-token seller write path** the seller console uses, end to end over HTTP:
`weespas /auth/login` → `weespas /media/trade` (the **weespas** token, multipart — returns
`/uploads/trade/...` URLs) → `weespas /commerce/session-token` (mints RS256) → `commerce /shops`,
`/shops/{id}/listings` (the **commerce** token, using those URLs) → `PATCH /listings/{id}/stock`
→ `/feed?kind=videos`. The two-token split is the feature's main integration risk (media lives in
the weespas pipeline; trade lives in commerce), so this is the only place the real handoff runs
over the wire. It also asserts commerce **rejects** the weespas token (proving the split is real),
plus the media endpoint's negative controls (bad content-type → 400, missing auth → 401/403).
Fixtures (a 1×1 PNG + a tiny ftyp-boxed mp4) are generated in-process — no committed binaries.

FE-2b ("reach & respond") extends the same script: promote the listing (story window →
`is_promoted`), then Boost it **sovereign** and assert the daily allowance decrements, the listing
reaches a **far** buyer (Mombasa) via the labelled sponsored lane (`is_sponsored` + `boost_tier`),
and revoke removes the slot **without** refunding the spent chance. (Sovereign + a far buyer is
deliberate: the sponsored lane drops any listing already shown organically, so the label is only
observable from a point where the listing isn't organic.)

```bash
cd PE/commerce/e2e
NODE_PATH=/home/jeff/PE/InSAR-Final-main/frontend/node_modules \
  WEESPAS_BASE_URL=http://127.0.0.1:8000 COMMERCE_BASE_URL=http://127.0.0.1:8003 \
  WEESPAS_EMAIL=admin@weespas.com WEESPAS_PASSWORD=admin123 \
  node seller.fe.e2e.js
```

Same prerequisites as the FE-1 bridge e2e (weespas `:8000` with RS256 keys + commerce `:8003`).

## Trending rail + categories + moderation e2e (`trending.fe.e2e.js`)

Exercises §8.5 (trending rail) and §8.4 (comment moderation) over the real weespas→commerce
bridge. Seeds a far shop with a `category`, **sovereign-boosts the SHOP** so it reaches the
Nairobi buyer's `GET /trending` slate, and asserts the card carries its category + `boost_tier`,
the polling contract (`dwell_seconds` + `next_change_at`), **no PII** (the seller's user id never
appears; cards expose only allow-listed fields), and lat/lng bounds (422) / no-token (401). Then it
runs the full moderation authorization matrix: a buyer comments → the listing **owner** hides it
(gone from thread + `comment_count`) → a non-owner/non-staff buyer is **404** (no existence leak) →
a **staff** principal un-hides → restored. Cleans up the seeded boost.

```bash
cd PE/commerce/e2e
NODE_PATH=/home/jeff/PE/InSAR-Final-main/frontend/node_modules \
  WEESPAS_BASE_URL=http://127.0.0.1:8000 COMMERCE_BASE_URL=http://127.0.0.1:8003 \
  WEESPAS_EMAIL=admin@weespas.com WEESPAS_PASSWORD=admin123 \
  node trending.fe.e2e.js
```

Same prerequisites as the FE-1 bridge e2e (weespas `:8000` with RS256 keys + commerce `:8003`).

## Expiry sweep (separate script — needs DB access, not HTTP)

The TTL expiry sweep can't be tested over HTTP (you can't back-date an order past the 1h
production TTL through the API), so it has a companion Python check that drives the DB directly
against the same live PostGIS:

```bash
cd PE/commerce
for l in $(grep -v '^#' .env | grep '='); do export "$l"; done
PYTHONPATH=/home/jeff .venv/bin/python e2e/expiry_sweep_live.py
```

It opens a bargain order, back-dates it, runs `expiry_sweeper.run_once` (exercising the Postgres
advisory lock + per-order commit), and asserts it expired with an `expire` event. Exits non-zero
on failure. The sweeper itself runs in production as a standalone process: `PE/dev/commerce-sweeper.sh`.

## Emoji-palette portal verify (`emoji_portal.verify.js` — real browser, not API)

The only **browser-driven** check here (the others are API-level `request.newContext()`). It drives a
real chromium against the live **weespas** frontend (`:5173`), logs in, opens the Trade composer and a
feed comment thread, and asserts the `EmojiPalette` portals to `<body>` with `position: fixed`, stays
fully inside the viewport, and isn't clipped by `.product-card { overflow: hidden }` — the regression
the portal fix addresses. It auto-resolves the installed full chromium (no `npx playwright install`).
Use `http://localhost:5173` (not `127.0.0.1`) so it matches a CORS origin — though both are now allowed.

```bash
cd PE/commerce/e2e
NODE_PATH=/home/jeff/PE/InSAR-Final-main/frontend/node_modules \
  WEESPAS_FE_URL=http://localhost:5173 \
  WEESPAS_EMAIL=admin@weespas.com WEESPAS_PASSWORD=admin123 \
  node emoji_portal.verify.js
```

Needs the weespas frontend (`:5173`) and backend (`:8000`) + commerce (`:8003`) up (the feed must load
for the comment-thread scenario).
