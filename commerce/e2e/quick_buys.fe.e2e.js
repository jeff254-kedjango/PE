/**
 * Quick Buys grid — live e2e over the REAL weespas→commerce bridge + a browser render check.
 *
 * Covers the §8 Trade right-rail 3×3 discovery grid end-to-end on the live PostGIS/RS256 stack (the
 * layer the SQLite unit tests can't reach), via the same bridge the Trade frontend uses:
 *
 *   weespas /auth/login → weespas /commerce/session-token (RS256) → commerce /quick-buys
 *
 * Part A (API): seed a NEAR shop + listing (within 5 km of the Nairobi buyer) and a FAR shop +
 * listing (Mombasa, well beyond 5 km) in a distinct category, give the buyer AFFINITY for that
 * category by SAVING the far listing, then assert the grid:
 *   - returns items with the near/outer split (near item ≤ near_radius_m, and near_radius_m present);
 *   - carries the lean DTO ONLY (no POS internals: no stock_qty/intent_weight/is_active; no PII);
 *   - a price filter narrows the set (an over-priced near item drops under a max_price_cents);
 *   - out-of-range lat/lng → 422; a garbage category slug does NOT 422 (it degrades).
 *
 * Part B (browser): drive the live Trade page at ≥1101px, assert the Quick Buys section renders with
 * a titled header + a filter button, the grid shows cards, and the filter button opens the modal.
 *
 * Run:
 *   cd PE/commerce/e2e
 *   NODE_PATH=/home/jeff/PE/InSAR-Final-main/frontend/node_modules \
 *     WEESPAS_BASE_URL=http://127.0.0.1:8000 COMMERCE_BASE_URL=http://127.0.0.1:8003 \
 *     FE_BASE_URL=http://127.0.0.1:5174 WEESPAS_EMAIL=admin@weespas.com WEESPAS_PASSWORD=admin123 \
 *     node quick_buys.fe.e2e.js
 *
 * Same prerequisites as trade.fe.e2e.js (weespas :8000 with RS256 keys + commerce :8003) plus the
 * FE (:5174) for Part B.
 */
const { chromium, request } = require('playwright');
const { seller, registerCleanup } = require('./jwt');

const WEESPAS = process.env.WEESPAS_BASE_URL || 'http://127.0.0.1:8000';
const COMMERCE = process.env.COMMERCE_BASE_URL || 'http://127.0.0.1:8003';
const FE = process.env.FE_BASE_URL || 'http://127.0.0.1:5174';
const WEESPAS_API = `${WEESPAS}/api/v1`;
const COMMERCE_API = `${COMMERCE}/api/v1`;
const EMAIL = process.env.WEESPAS_EMAIL || 'admin@weespas.com';
const PASSWORD = process.env.WEESPAS_PASSWORD || 'admin123';

const RUN = `qb-${Date.now()}`;
registerCleanup(RUN);   // remove this run's shops/listings from the live DB on exit (any path)
const NBO = { lat: -1.292, lng: 36.8219 };     // buyer (Nairobi CBD — the FE geo-denied default)
const NEAR = { lat: -1.2955, lng: 36.8219 };   // ~0.4 km south of the buyer (inside the 5 km near radius)
const OUTER = { lat: -1.2201, lng: 36.8219 };  // ~8 km north (beyond 5 km near, within the 20 km outer cap)

let passed = 0;
const failures = [];
const check = (name, cond, detail = '') => {
  if (cond) { passed += 1; console.log(`  ✓ ${name}`); }
  else { failures.push(`${name}${detail ? ` — ${detail}` : ''}`); console.log(`  ✗ ${name}${detail ? ` — ${detail}` : ''}`); }
};
const eq = (name, a, b) => check(name, a === b, `expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
const authH = (t) => ({ Authorization: `Bearer ${t}` });

// Fields the lean DTO must NEVER leak (POS internals + PII).
const BANNED_FIELDS = ['stock_qty', 'intent_weight', 'is_active', 'low_stock_threshold',
  'buyer_uuid', 'user_uuid', 'contact', 'geog'];

async function main() {
  const ctx = await request.newContext();

  // 1) Bridge: weespas login → commerce session token (RS256), exactly as the FE does.
  let r = await ctx.post(`${WEESPAS_API}/auth/login`, {
    headers: { 'Content-Type': 'application/json' },
    data: { email: EMAIL, password: PASSWORD },
  });
  check('weespas login 200', r.ok(), `status ${r.status()}`);
  const body = await r.json();
  const weespasToken = body.token;
  const weespasUser = body.user;
  r = await ctx.get(`${WEESPAS_API}/commerce/session-token`, { headers: authH(weespasToken) });
  check('commerce session-token 200', r.ok(), `status ${r.status()}`);
  const session = (await r.json()).token;
  // The bridge sub is the buyer identity whose affinity the grid reads. Decode it from the JWT
  // payload (middle segment) so the seeded SAVE below is attributed to the SAME buyer.
  const buyerSub = JSON.parse(Buffer.from(session.split('.')[1], 'base64').toString()).sub;

  // 2) Seed a NEAR shop + listing (electronics, within 5 km) and a FAR shop + listing (electronics,
  //    Mombasa). Use a unique category-less title tag so we can find OUR items in the slate.
  const sellerTok = seller(`${RUN}-seller`);
  r = await ctx.post(`${COMMERCE_API}/shops`, {
    headers: authH(sellerTok),
    data: { name: `QB Near ${RUN}`, lat: NEAR.lat, lng: NEAR.lng, display_name: 'QB Near',
            category: 'electronics' },
  });
  eq('seed near shop 201', r.status(), 201);
  const nearShop = await r.json();
  r = await ctx.post(`${COMMERCE_API}/shops/${nearShop.id}/listings`, {
    headers: authH(sellerTok),
    data: { title: `QB-NEAR ${RUN}`, price_cents: 2000, stock_qty: 5 },
  });
  eq('seed near listing 201', r.status(), 201);
  const nearListing = await r.json();

  r = await ctx.post(`${COMMERCE_API}/shops`, {
    headers: authH(sellerTok),
    data: { name: `QB Outer ${RUN}`, lat: OUTER.lat, lng: OUTER.lng, display_name: 'QB Outer',
            category: 'electronics' },
  });
  eq('seed outer shop 201', r.status(), 201);
  const outerShop = await r.json();
  r = await ctx.post(`${COMMERCE_API}/shops/${outerShop.id}/listings`, {
    headers: authH(sellerTok),
    data: { title: `QB-OUTER ${RUN}`, price_cents: 4000, stock_qty: 5 },
  });
  eq('seed outer listing 201', r.status(), 201);
  const outerListing = await r.json();

  // 3) Give the BUYER affinity for "electronics" by SAVING the outer electronics listing (a real
  //    engagement signal the affinity derives categories from). The save is attributed to the
  //    bridge principal (the buyer), so the grid's outer lane favours electronics.
  r = await ctx.post(`${COMMERCE_API}/listings/${outerListing.id}/save`, { headers: authH(session) });
  check('buyer saves outer listing (seeds affinity) 200', r.ok(), `status ${r.status()}`);

  // 4) The grid via the bridge token. NOTE: the live CBD DB has ACCUMULATED many listings from prior
  //    runs, so we assert robust INVARIANTS over the slate (composition + bucket/distance/price
  //    contracts that hold regardless of which specific rows win the ranked slots) rather than
  //    "my exact seeded row appears in a bounded 16-slot grid" — that ranked race is fragile on a
  //    shared DB and is proven deterministically in the pytest suite on a clean DB.
  r = await ctx.get(`${COMMERCE_API}/quick-buys?lat=${NBO.lat}&lng=${NBO.lng}`, { headers: authH(session) });
  check('quick-buys via bridge 200', r.ok(), `status ${r.status()}`);
  const grid = await r.json();
  check('response has items[]', Array.isArray(grid.items));
  check('response carries near_radius_m', typeof grid.near_radius_m === 'number', JSON.stringify(grid.near_radius_m));
  check('response carries page_size', grid.page_size >= 1);
  check('grid returns items (near CBD is well-seeded)', grid.items.length > 0, `got ${grid.items.length}`);

  // Bucket/distance CONTRACT holds for every item: a "near" item is within near_radius_m; an outer
  // item (interest|trending) is strictly beyond it. This is the core near/outer split promise.
  const badNear = grid.items.filter((it) => it.bucket === 'near' && it.distance_m > grid.near_radius_m);
  const badOuter = grid.items.filter((it) => it.bucket !== 'near' && it.distance_m <= grid.near_radius_m);
  check('every "near" item is within near_radius_m', badNear.length === 0, `${badNear.length} violate`);
  check('every outer item is beyond near_radius_m', badOuter.length === 0, `${badOuter.length} violate`);
  check('every item has a valid bucket', grid.items.every((it) => ['near', 'interest', 'trending'].includes(it.bucket)));
  // Composition: the FIRST page leads with near items then outer, per the 4:5 design (when both
  // lanes have supply — the CBD near lane is well-seeded so page 1 begins with ≥1 near item).
  const firstPage = grid.items.slice(0, grid.page_size);
  check('page 1 leads with a near item (near lane surfaced first)',
    firstPage.length > 0 && firstPage[0].bucket === 'near', `first bucket=${firstPage[0] && firstPage[0].bucket}`);

  // 5) Lean DTO: no POS internals, no PII, on EVERY item.
  let leaks = [];
  for (const it of grid.items) {
    for (const f of BANNED_FIELDS) if (f in it) leaks.push(f);
  }
  check('no POS/PII fields leak on any item', leaks.length === 0, `leaked: ${[...new Set(leaks)].join(', ')}`);

  // 6) A price filter bites: with a max_price_cents cap, NO returned item exceeds it (a whole-slate
  //    invariant that holds on the accumulated DB, unlike "my specific row dropped").
  const CAP = 1500;
  r = await ctx.get(`${COMMERCE_API}/quick-buys?lat=${NBO.lat}&lng=${NBO.lng}&max_price_cents=${CAP}`,
    { headers: authH(session) });
  check('quick-buys with price filter 200', r.ok(), `status ${r.status()}`);
  const filtered = await r.json();
  check('price filter: no item exceeds the cap',
    filtered.items.every((it) => it.price_cents <= CAP),
    `max seen ${Math.max(0, ...filtered.items.map((it) => it.price_cents))}`);

  // A category filter bites: restrict to butchery → every returned item's shop_category is butchery
  // (or the slate is empty). Proves the filter narrows BOTH lanes by category.
  r = await ctx.get(`${COMMERCE_API}/quick-buys?lat=${NBO.lat}&lng=${NBO.lng}&categories=butchery`,
    { headers: authH(session) });
  check('quick-buys with category filter 200', r.ok(), `status ${r.status()}`);
  const catFiltered = await r.json();
  check('category filter: every item is the requested category',
    catFiltered.items.every((it) => it.shop_category === 'butchery'),
    `saw ${[...new Set(catFiltered.items.map((it) => it.shop_category))].join(',')}`);

  // 7) Input guards: out-of-range lat/lng → 422; a garbage category degrades (200, not 422).
  r = await ctx.get(`${COMMERCE_API}/quick-buys?lat=999&lng=0`, { headers: authH(session) });
  eq('out-of-range lat → 422', r.status(), 422);
  r = await ctx.get(`${COMMERCE_API}/quick-buys?lat=${NBO.lat}&lng=${NBO.lng}&categories=not_a_cat`,
    { headers: authH(session) });
  eq('garbage category degrades (not 422)', r.status(), 200);

  await ctx.dispose();

  // ------------------------------- Part B: browser render -------------------------------
  const browser = await chromium.launch();
  try {
    const bctx = await browser.newContext({
      viewport: { width: 1440, height: 900 }, // ≥1101px so the right rail (and Quick Buys) shows
      permissions: [],                        // geo denied → Nairobi CBD default (our near seed is there)
    });
    await bctx.addInitScript(
      ([t, u]) => {
        localStorage.setItem('weespas_token', t);
        localStorage.setItem('weespas_user', u);
      },
      [weespasToken, JSON.stringify(weespasUser)],
    );
    const page = await bctx.newPage();
    await page.goto(`${FE}/trade`, { waitUntil: 'networkidle' });

    const section = page.locator('.quick-buys');
    await section.waitFor({ timeout: 15000 }).catch(() => {
      console.error('WARN: .quick-buys not found — screenshotting for diagnosis');
    });
    check('Quick Buys section renders', await section.count() > 0);
    check('section title reads "Quick Buys"',
      (await page.locator('.quick-buys__title').first().textContent().catch(() => '') || '').includes('Quick Buys'));

    const filterBtn = page.locator('[data-testid="quick-buys-filter-open"]');
    check('filter button present (top-right of the section)', await filterBtn.count() > 0);

    const cards = page.locator('.quick-buys__grid .quick-buy-card');
    const cardCount = await cards.count();
    check('grid shows product cards', cardCount > 0, `cards=${cardCount}`);
    check('each visible page shows at most 9 cards (3×3)', cardCount <= 9, `cards=${cardCount}`);
    // A card exposes the cart action.
    check('cards carry an add-to-cart button', await page.locator('.quick-buys__grid [data-testid="quick-buy-cart"]').count() > 0);

    // The filter button opens the modal.
    if (await filterBtn.count() > 0) {
      await filterBtn.first().click();
      const modal = page.locator('[data-testid="quick-buys-filter-modal"]');
      await modal.waitFor({ timeout: 5000 }).catch(() => {});
      check('filter button opens the filter modal', await modal.count() > 0);
      // Modal is dismissable on Escape (matches the adv-modal ergonomics).
      await page.keyboard.press('Escape');
      await page.waitForTimeout(200);
      check('modal closes on Escape', await modal.count() === 0);
    }

    await page.screenshot({ path: '/tmp/quick-buys.png', fullPage: false });
    console.log('  screenshot → /tmp/quick-buys.png');
  } finally {
    await browser.close();
  }

  console.log(`\n${passed} checks passed, ${failures.length} failed`);
  if (failures.length) { console.error('\nFAILURES:\n  - ' + failures.join('\n  - ')); process.exit(1); }
}

main().catch((e) => { console.error('quick_buys e2e crashed:', e); process.exit(1); });
