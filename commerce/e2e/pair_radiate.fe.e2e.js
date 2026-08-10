/**
 * §8.1b PAIR-RADIATE — live 2-browser-context "we're connected" e2e.
 *
 * This is the one check that proves the whole realtime slice end-to-end on the REAL stack — the
 * HTTP uplink, the buyer-local glow, AND the Redis-Pub/Sub SSE downlink to the seller:
 *
 *   BUYER browser (telemetry sub = buyer)          SELLER browser (telemetry sub = seller)
 *     opens a shop pin                                holds GET /insar/contact/stream (SSE)
 *        │ POST /insar/contact                              ▲
 *        ▼                                                  │ publish {shop_building_id, aoi}
 *     glows OWN footprint (fuchsia) ── weespas ── Redis Pub/Sub ── glows the SHOP footprint (fuchsia)
 *        from the POST response                          on the seller's own map
 *
 * Two contexts in ONE browser, each with its OWN telemetry token (distinct `sub`), model the two
 * parties. The seller's `sub` == the commerce Seller.user_uuid (create_shop binds the shop to the
 * creating token's sub), so the anonymized pulse routes to `contact-events:<seller>` — exactly the
 * seller's SSE channel. The buyer OWNS a distinct footprint (seeded spine User.agent_id → Property
 * → BuildingLink) so the buyer-half glow has something real to light up.
 *
 * deck.gl paints to a WebGL canvas (not the DOM), so "it glowed" is asserted via the DEV-only hook
 * window.__insarShopsE2E (tree-shaken from prod): `.openShop(bid)` drives the REAL production
 * openShopPin path, and `.contactGlowing()` reports the building_ids the fill buffer is overriding
 * fuchsia this frame — data-plane truth of the glow.
 *
 * What it asserts:
 *   1. the aggregator paints the shop pin for both parties (backend join sanity before the browser);
 *   2. BUYER: opening the pin glows the buyer's OWN footprint (from the POST response) — NOT the
 *      shop building via ownership (that comes from the local shop-row add), proving the buyer half;
 *   3. SELLER: the anonymized pulse arrives over live SSE and glows the SHOP footprint on the
 *      seller's map — the downlink works over real Redis;
 *   4. PRIVACY (decision #2): the buyer's own building_id NEVER appears on the seller's map (no
 *      home-location leak) — the seller only ever sees the shop footprint.
 *
 * Seeding spans TWO databases; teardown does too and BOTH run on every exit path:
 *   - weespas rows (Agents + buyer User + Properties + BuildingLinks): e2e/seed_pair_radiate.py.
 *   - commerce rows (Shop + synthetic Seller): the shared e2e/cleanup_run.py via registerCleanup().
 * Nothing here touches the 177 genuine listings or any real shop.
 *
 * Run (weespas :8000, commerce :8003, InSAR FE :5173 — NOT the weespas FE; Redis must be up):
 *   cd PE/commerce/e2e
 *   NODE_PATH=/home/jeff/PE/InSAR-Final-main/frontend/node_modules node pair_radiate.fe.e2e.js
 */
const { chromium } = require('playwright');
const { spawnSync } = require('child_process');
const path = require('path');
const { telemetry, seller, registerCleanup } = require('./jwt.js');

const WEESPAS = process.env.WEESPAS_BASE_URL || 'http://127.0.0.1:8000';
const COMMERCE = process.env.COMMERCE_BASE_URL || 'http://127.0.0.1:8003';
const INSAR_FE = process.env.INSAR_FE_URL || 'http://127.0.0.1:5173';
const COMMERCE_API = `${COMMERCE}/api/v1`;

// Real AOI footprints present in every bundle (confirmed live from the InSAR DuckDB). A pin can only
// paint if its building_id exists in the AOI the map loads — a made-up id would fetch but never draw.
const AOI = 'huruma';
const SHOP_BID = 100000;   // the shop's footprint (both parties see the pin here)
const BUYER_BID = 100001;  // the buyer's OWN footprint (buyer-half glow lights this)

const RUN = `pr-${Date.now()}`;
const BUYER_UUID = `buyer-${RUN}`;
const SELLER_UUID = `seller-${RUN}`;      // == commerce Seller.user_uuid == SSE channel key
const SHOP_PROP_UUID = `shopprop-${RUN}`;

const WEESPAS_ROOT = path.resolve(__dirname, '../../weespas');
const WEESPAS_PY = path.resolve(WEESPAS_ROOT, '.venv/bin/python');

let passed = 0;
const failures = [];
const check = (name, cond, detail = '') => {
  if (cond) { passed += 1; console.log(`  ✓ ${name}`); }
  else { failures.push(`${name}${detail ? ` — ${detail}` : ''}`); console.log(`  ✗ ${name}${detail ? ` — ${detail}` : ''}`); }
};

function weespasSeed(args) {
  const r = spawnSync(WEESPAS_PY, [path.join(WEESPAS_ROOT, 'e2e/seed_pair_radiate.py'), ...args], {
    cwd: WEESPAS_ROOT, env: { ...process.env, PYTHONPATH: '/home/jeff' }, encoding: 'utf8',
  });
  if (r.status !== 0) throw new Error(`weespas seed ${args.join(' ')} failed (exit ${r.status}): ${(r.stderr || '').trim()}`);
  return (r.stdout || '').trim();
}
function weespasClean(run) {
  const r = spawnSync(WEESPAS_PY, [path.join(WEESPAS_ROOT, 'e2e/seed_pair_radiate.py'), 'clean', run], {
    cwd: WEESPAS_ROOT, env: { ...process.env, PYTHONPATH: '/home/jeff' }, encoding: 'utf8',
  });
  const out = (r.stdout || '').trim();
  if (out) console.log(`  ⤺ ${out}`);
  if (r.status !== 0) console.error(`  ⚠ weespas clean(${run}) exited ${r.status}: ${(r.stderr || '').trim()}`);
}

// Create the commerce shop via the real API so PostGIS geog is built exactly as in production; the
// creating token's sub becomes Seller.user_uuid (the pulse channel key).
async function createCommerceShop(sellerToken) {
  const res = await fetch(`${COMMERCE_API}/shops`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${sellerToken}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name: `PR Shop ${RUN}`, display_name: `PR Store ${RUN}`,
      lat: -1.2597409, lng: 36.8687368, property_uuid: SHOP_PROP_UUID, category: 'bakery',
    }),
  });
  if (!res.ok) throw new Error(`commerce shop create failed (${res.status}): ${await res.text()}`);
  return res.json();
}

// Poll a page's glow set until it contains `bid` (or timeout). Returns the final glow array.
// `polling: 100` forces a TIMER poll, NOT the default requestAnimationFrame: a contact glow is a
// ~10s-TTL transient, and rAF is throttled/paused in a non-foreground tab. With two browser
// contexts open, BOTH pages are backgrounded, so an rAF-polled wait can miss a glow that genuinely
// arrived and then expired inside the poll gap. A timer poll keeps ticking while backgrounded (the
// launch flags below keep it near-real-time), so the transient is observed before its TTL elapses.
async function waitForGlow(page, bid, timeout = 20000) {
  const handle = await page.waitForFunction((wantBid) => {
    const h = window.__insarShopsE2E;
    if (!h) return null;
    const g = h.contactGlowing();
    return g.includes(wantBid) ? g : null;
  }, bid, { timeout, polling: 100 }).catch(() => null);
  return handle ? handle.jsonValue() : null;
}

// Wait for the DEV-only render hook to mount (React has painted past the gate). Needed because we
// navigate with 'domcontentloaded' (the SSE stream keeps the network from ever going idle).
async function waitForHook(page, timeout = 30000) {
  return page.waitForFunction(() => !!window.__insarShopsE2E, null, { timeout })
    .then(() => true).catch(() => false);
}

// Wait for the shop layer to resolve its pin (data plane) so the map is ready to drive/observe.
async function waitForPin(page, bid, timeout = 30000) {
  return page.waitForFunction((wantBid) => {
    const h = window.__insarShopsE2E;
    if (!h) return false;
    return h.resolved().some((r) => r.building_id === wantBid);
  }, bid, { timeout }).then(() => true).catch(() => false);
}

async function main() {
  // Register BOTH teardowns before creating anything, so a crash mid-seed still cleans up.
  registerCleanup(RUN);                         // commerce rows, on process exit
  process.on('exit', () => weespasClean(RUN));  // weespas rows, on process exit

  // 1. Seed weespas: buyer ownership chain + BuildingLinks on both footprints.
  weespasSeed(['seed', RUN, AOI, String(SHOP_BID), String(BUYER_BID), BUYER_UUID, SHOP_PROP_UUID]);
  // 2. Seed commerce: the seller (sub == SELLER_UUID) creates the shop, binding it to that channel.
  await createCommerceShop(seller(SELLER_UUID));

  // 3. Backend sanity: the aggregator returns the shop pin (proves the join before the browser).
  const buyerWt = telemetry(BUYER_UUID);
  const sellerWt = telemetry(SELLER_UUID);
  const aggRes = await fetch(`${WEESPAS}/api/v1/insar/shops/near?aoi=${AOI}`, {
    headers: { Authorization: `Bearer ${buyerWt}` },
  });
  check('aggregator responds 200', aggRes.ok, `status=${aggRes.status}`);
  const agg = aggRes.ok ? await aggRes.json() : { shops: [] };
  check('aggregator returns the shop pin', (agg.shops || []).some((s) => s.insar_building_id === SHOP_BID));

  // Anti-throttling flags: with two contexts open, Chromium backgrounds the non-foreground page and
  // slows its timers/rAF — which would starve the glow animation AND the timer-poll observing it.
  // These keep a backgrounded renderer near-real-time so the 10s-TTL contact glow both animates and
  // is observable in both contexts (a headless-CI parity fix, not a product concern).
  const browser = await chromium.launch({
    args: [
      '--disable-background-timer-throttling',
      '--disable-backgrounding-occluded-windows',
      '--disable-renderer-backgrounding',
    ],
  });
  try {
    // ── SELLER context: open the map and hold the SSE stream OPEN before the buyer acts. Redis
    //    Pub/Sub drops messages with no live subscriber, so the seller must be subscribed first. ──
    const sellerCtx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const sellerPage = await sellerCtx.newPage();
    // NOT 'networkidle': the pair-radiate SSE client opens a long-lived stream on mount, so the
    // network never goes idle. 'domcontentloaded' + the explicit hook/pin waits below are the real
    // readiness signal (waitForPin polls the data plane; the SSE settle is handled separately).
    await sellerPage.goto(`${INSAR_FE}/?wt=${encodeURIComponent(sellerWt)}&aoi=${AOI}`, { waitUntil: 'domcontentloaded' });
    check('seller map rendered past the gate', await waitForHook(sellerPage));
    check('seller sees the shop pin', await waitForPin(sellerPage, SHOP_BID));
    // Give the seller's SSE subscription time to establish on the server before the buyer publishes
    // (Redis Pub/Sub drops a message with no live subscriber — there is no retry on the publish
    // side). The fetch-stream client opens the stream on mount and the server's ": connected" frame
    // confirms it; a short settle here closes the race deterministically.
    await sellerPage.waitForTimeout(1500);

    // ── BUYER context: open the shop pin. Drives the REAL openShopPin (POST + local glow). ──
    const buyerCtx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const buyerPage = await buyerCtx.newPage();
    await buyerPage.goto(`${INSAR_FE}/?wt=${encodeURIComponent(buyerWt)}&aoi=${AOI}`, { waitUntil: 'domcontentloaded' });
    check('buyer map rendered past the gate', await waitForHook(buyerPage));
    check('buyer sees the shop pin', await waitForPin(buyerPage, SHOP_BID));

    // Opening the pin fires BOTH halves at the same instant: the buyer glows locally (POST response)
    // and the anonymized pulse publishes to the seller. Both glows are ~10s-TTL transients, so START
    // both observers BEFORE awaiting either — a serial buyer-first wait could otherwise burn the
    // seller glow's whole TTL before we ever look, and the seller half would read as expired (null).
    const buyerGlowP = waitForGlow(buyerPage, BUYER_BID);
    const sellerGlowP = waitForGlow(sellerPage, SHOP_BID);
    const opened = await buyerPage.evaluate((bid) => window.__insarShopsE2E.openShop(bid), SHOP_BID);
    check('buyer opened the shop pin (real trigger fired)', opened === true);
    const [buyerGlow, sellerGlow] = await Promise.all([buyerGlowP, sellerGlowP]);

    // 4. BUYER half: the buyer's OWN footprint glows (from the POST response). The shop building also
    //    glows locally (added from the resolved pin row), so we assert the OWN building specifically.
    check('BUYER: own footprint glows fuchsia (buyer half)',
      Array.isArray(buyerGlow) && buyerGlow.includes(BUYER_BID), `glow=${JSON.stringify(buyerGlow)}`);

    // 5. SELLER half: the anonymized pulse arrives over live SSE → the SHOP footprint glows.
    check('SELLER: shop footprint glows fuchsia from the live SSE pulse (seller half / downlink)',
      Array.isArray(sellerGlow) && sellerGlow.includes(SHOP_BID), `glow=${JSON.stringify(sellerGlow)}`);

    // 6. PRIVACY (decision #2): the buyer's OWN building_id must NEVER reach the seller's map. Assert
    //    against the SAME glow frame that captured the shop pulse (#5) — the moment the seller was
    //    provably glowing — not a later snapshot that may have TTL-expired to empty (which would pass
    //    vacuously). If the shop glow never arrived, sellerGlow is null and this check is skipped as
    //    moot (the seller-half failure above already reports the real problem).
    check('PRIVACY: buyer building_id never leaks to the seller (no home-location leak)',
      !Array.isArray(sellerGlow) || !sellerGlow.includes(BUYER_BID), `sellerGlow=${JSON.stringify(sellerGlow)}`);

    await sellerPage.screenshot({ path: '/tmp/pair-radiate-seller.png' });
    await buyerPage.screenshot({ path: '/tmp/pair-radiate-buyer.png' });
    console.log('  screenshots → /tmp/pair-radiate-{buyer,seller}.png');

    await buyerCtx.close();
    await sellerCtx.close();
  } finally {
    await browser.close();
  }

  console.log(`\n${passed} checks passed, ${failures.length} failed`);
  if (failures.length) { console.error('\nFAILURES:\n  - ' + failures.join('\n  - ')); process.exit(1); }
}

main().catch((e) => { console.error('pair-radiate e2e crashed:', e); process.exit(1); });
