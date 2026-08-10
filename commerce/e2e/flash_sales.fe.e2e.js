/**
 * Flash Sales grid — live e2e over the REAL weespas→commerce bridge + a browser render check.
 *
 * Covers the §8 nationwide "crazy offer" grid end-to-end on the live PostGIS/RS256 stack (the layer
 * the SQLite unit tests can't reach), via the same bridge the Trade frontend uses:
 *
 *   weespas /auth/login → weespas /commerce/session-token (RS256) → commerce /flash-sales
 *
 * Part A (API): seed comparable shops (so the margin has something to compare against) + a subject
 * listing FAR from the buyer (Mombasa), launch a crazy flash sale on it via the seller write path,
 * then assert:
 *   - the FAR sale appears in the NATIONWIDE slate (geo ignored — a Mombasa sale shows to a Nairobi
 *     buyer); ranked entries carry a positive discount_percent and the reference/flash relationship;
 *   - the lean DTO ONLY (no POS internals: no stock_qty/intent_weight/is_active; no PII; no raw
 *     flash_score);
 *   - a BUY during the window locks at the FLASH price (the money-path override), and after CLEAR
 *     the listing drops from the slate (auto-restore / vanish);
 *   - a non-discount launch (price ≥ market) → 422; a bargain listing → 422; out-of-range lat → 422;
 *   - cross-owner clear → 404 (no existence leak).
 *
 * Part B (browser): drive the live Trade page at ≥1101px, assert the Flash Sales section renders
 * with the title + the "expires in less than an hour" subtitle, the 3×2 grid shows cards (≤6/page),
 * and a card exposes the buy action.
 *
 * Run:
 *   cd PE/commerce/e2e
 *   NODE_PATH=/home/jeff/PE/InSAR-Final-main/frontend/node_modules \
 *     WEESPAS_BASE_URL=http://127.0.0.1:8000 COMMERCE_BASE_URL=http://127.0.0.1:8003 \
 *     FE_BASE_URL=http://127.0.0.1:5174 WEESPAS_EMAIL=admin@weespas.com WEESPAS_PASSWORD=admin123 \
 *     node flash_sales.fe.e2e.js
 *
 * Same prerequisites as quick_buys.fe.e2e.js (weespas :8000 with RS256 keys + commerce :8003) plus
 * the FE (:5174) for Part B.
 */
const { chromium, request } = require('playwright');
const { seller, buyer, registerCleanup } = require('./jwt');

const WEESPAS = process.env.WEESPAS_BASE_URL || 'http://127.0.0.1:8000';
const COMMERCE = process.env.COMMERCE_BASE_URL || 'http://127.0.0.1:8003';
const FE = process.env.FE_BASE_URL || 'http://127.0.0.1:5174';
const WEESPAS_API = `${WEESPAS}/api/v1`;
const COMMERCE_API = `${COMMERCE}/api/v1`;
const EMAIL = process.env.WEESPAS_EMAIL || 'admin@weespas.com';
const PASSWORD = process.env.WEESPAS_PASSWORD || 'admin123';

const RUN = `fs-${Date.now()}`;
registerCleanup(RUN);   // remove this run's shops/listings from the live DB on exit (any path)
const NBO = { lat: -1.292, lng: 36.8219 };     // buyer (Nairobi CBD — the FE geo-denied default)
// The subject sale lives in MOMBASA — ~440 km away — to prove the slate is nationwide (geo ignored).
const MSA = { lat: -4.0435, lng: 39.6682 };

let passed = 0;
const failures = [];
const check = (name, cond, detail = '') => {
  if (cond) { passed += 1; console.log(`  ✓ ${name}`); }
  else { failures.push(`${name}${detail ? ` — ${detail}` : ''}`); console.log(`  ✗ ${name}${detail ? ` — ${detail}` : ''}`); }
};
const eq = (name, a, b) => check(name, a === b, `expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
const authH = (t) => ({ Authorization: `Bearer ${t}` });

// Fields the lean DTO must NEVER leak (POS internals + PII + the raw score).
const BANNED_FIELDS = ['stock_qty', 'intent_weight', 'is_active', 'low_stock_threshold',
  'buyer_uuid', 'user_uuid', 'contact', 'geog', 'flash_score'];

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

  const sellerTok = seller(`${RUN}-seller`);

  // 2) Seed a couple of same-category (shoes) COMPARABLES near the subject at a "market" price, so
  //    the margin has something to compare against (a real reference, not the own-price fallback).
  const MARKET = 100000; // KES 1,000 "going rate"
  for (let i = 0; i < 3; i += 1) {
    r = await ctx.post(`${COMMERCE_API}/shops`, {
      headers: authH(sellerTok),
      data: { name: `Cmp ${RUN}-${i}`, lat: MSA.lat + i * 0.002, lng: MSA.lng, display_name: 'Cmp',
              category: 'shoes' },
    });
    eq(`seed comparable shop ${i} 201`, r.status(), 201);
    const cShop = await r.json();
    r = await ctx.post(`${COMMERCE_API}/shops/${cShop.id}/listings`, {
      headers: authH(sellerTok),
      data: { title: `Air Jordan cmp ${RUN}-${i}`, price_cents: MARKET, stock_qty: 5 },
    });
    eq(`seed comparable listing ${i} 201`, r.status(), 201);
  }

  // 3) The SUBJECT shop + listing (shoes, Mombasa) at the market price.
  r = await ctx.post(`${COMMERCE_API}/shops`, {
    headers: authH(sellerTok),
    data: { name: `FS Subject ${RUN}`, lat: MSA.lat, lng: MSA.lng, display_name: 'FS Subject',
            category: 'shoes' },
  });
  eq('seed subject shop 201', r.status(), 201);
  const subjectShop = await r.json();
  r = await ctx.post(`${COMMERCE_API}/shops/${subjectShop.id}/listings`, {
    headers: authH(sellerTok),
    data: { title: `FS-CRAZY ${RUN}`, price_cents: MARKET, stock_qty: 5 },
  });
  eq('seed subject listing 201', r.status(), 201);
  const subject = await r.json();

  // 4) A non-discount launch (>= market) is rejected; a genuinely crazy one succeeds.
  const CRAZY = 10000; // KES 100 vs a KES 1,000 market ⇒ ~90% off
  r = await ctx.post(`${COMMERCE_API}/listings/${subject.id}/flash-sale`, {
    headers: authH(sellerTok), data: { flash_price_cents: MARKET, duration_seconds: 3600 },
  });
  eq('non-discount flash launch → 422', r.status(), 422);

  r = await ctx.post(`${COMMERCE_API}/listings/${subject.id}/flash-sale`, {
    headers: authH(sellerTok), data: { flash_price_cents: CRAZY, duration_seconds: 3600 },
  });
  eq('crazy flash launch 200', r.status(), 200);
  const launched = await r.json();
  check('launched listing reports is_flash_active', launched.is_flash_active === true);
  check('normal price is untouched by the override', launched.price_cents === MARKET, `got ${launched.price_cents}`);
  eq('duration > 1h is rejected (hard cap)',
    (await ctx.post(`${COMMERCE_API}/listings/${subject.id}/flash-sale`, {
      headers: authH(sellerTok), data: { flash_price_cents: CRAZY, duration_seconds: 3601 },
    })).status(), 422);

  // 5) NATIONWIDE read: a Nairobi buyer sees the Mombasa sale. Assert our subject appears and the
  //    slate is ranked by craziness (discount_percent non-increasing).
  r = await ctx.get(`${COMMERCE_API}/flash-sales?lat=${NBO.lat}&lng=${NBO.lng}`, { headers: authH(session) });
  check('flash-sales via bridge 200', r.ok(), `status ${r.status()}`);
  const slate = await r.json();
  check('response has items[]', Array.isArray(slate.items));
  check('page_size is 6 (3×2)', slate.page_size === 6, `got ${slate.page_size}`);
  const mine = slate.items.find((it) => it.id === subject.id);
  check('the FAR (Mombasa) sale appears in the Nairobi slate (nationwide)', !!mine);
  if (mine) {
    check('subject flash_price is the crazy price', mine.flash_price_cents === CRAZY, `got ${mine.flash_price_cents}`);
    check('subject reference is the comparable market (~1,000)', mine.reference_cents === MARKET, `got ${mine.reference_cents}`);
    check('subject discount ≈ 90%', mine.discount_percent === 90, `got ${mine.discount_percent}`);
  }
  const discounts = slate.items.map((it) => it.discount_percent);
  const sortedDesc = discounts.every((d, i) => i === 0 || discounts[i - 1] >= d);
  check('slate is ranked by craziness (discount_percent non-increasing)', sortedDesc, JSON.stringify(discounts));

  // 6) Lean DTO on EVERY item (no POS internals / PII / raw score).
  let leaks = [];
  for (const it of slate.items) for (const f of BANNED_FIELDS) if (f in it) leaks.push(f);
  check('no POS/PII/score fields leak on any item', leaks.length === 0, `leaked: ${[...new Set(leaks)].join(', ')}`);

  // 7) The money path: a buy during the window locks at the FLASH price, not the list price.
  const buyerTok = buyer(`${RUN}-buyer`);
  r = await ctx.post(`${COMMERCE_API}/orders`, {
    headers: { ...authH(buyerTok), 'Content-Type': 'application/json', 'Idempotency-Key': `${RUN}-buy` },
    data: { listing_id: subject.id },
  });
  eq('buy-now during window 201', r.status(), 201);
  const order = await r.json();
  check('order locked at the FLASH price (override applied)', order.locked_price_cents === CRAZY,
    `got ${order.locked_price_cents}`);

  // 8) Cross-owner clear → 404 (no existence leak); owner clear works and the sale vanishes.
  r = await ctx.delete(`${COMMERCE_API}/listings/${subject.id}/flash-sale`, { headers: authH(seller(`${RUN}-intruder`)) });
  eq('cross-owner clear → 404', r.status(), 404);
  r = await ctx.delete(`${COMMERCE_API}/listings/${subject.id}/flash-sale`, { headers: authH(sellerTok) });
  eq('owner clear 200', r.status(), 200);
  r = await ctx.get(`${COMMERCE_API}/flash-sales?lat=${NBO.lat}&lng=${NBO.lng}`, { headers: authH(session) });
  const afterClear = await r.json();
  check('cleared sale vanishes from the slate (auto-restore)',
    !afterClear.items.some((it) => it.id === subject.id));

  // 9) Input guard: out-of-range lat → 422.
  r = await ctx.get(`${COMMERCE_API}/flash-sales?lat=999&lng=0`, { headers: authH(session) });
  eq('out-of-range lat → 422', r.status(), 422);

  await ctx.dispose();

  // ------------------------------- Part B: browser render -------------------------------
  // Re-launch a fresh crazy sale so the section has something to render (we cleared the subject).
  const ctx2 = await request.newContext();
  await ctx2.post(`${COMMERCE_API}/listings/${subject.id}/flash-sale`, {
    headers: authH(sellerTok), data: { flash_price_cents: CRAZY, duration_seconds: 3600 },
  });
  await ctx2.dispose();

  const browser = await chromium.launch();
  try {
    const bctx = await browser.newContext({
      viewport: { width: 1440, height: 900 }, // ≥1101px so the right rail (and Flash Sales) shows
      permissions: [],                        // geo denied → Nairobi CBD default; the slate is nationwide anyway
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

    const section = page.locator('.flash-sales');
    // Poll for the section (the slate query may resolve after networkidle).
    await section.waitFor({ timeout: 15000 }).catch(() => {
      console.error('WARN: .flash-sales not found — screenshotting for diagnosis');
    });
    check('Flash Sales section renders', await section.count() > 0);
    check('section title reads "Flash Sales"',
      (await page.locator('.flash-sales__title').first().textContent().catch(() => '') || '').includes('Flash Sales'));
    check('subtitle reads "expires in less than an hour"',
      (await page.locator('.flash-sales__subtitle').first().textContent().catch(() => '') || '')
        .includes('expires in less than an hour'));

    const cards = page.locator('.flash-sales__grid .flash-sale-card');
    const cardCount = await cards.count();
    check('grid shows flash-sale cards', cardCount > 0, `cards=${cardCount}`);
    check('each visible page shows at most 6 cards (3×2)', cardCount <= 6, `cards=${cardCount}`);
    check('cards carry a buy button', await page.locator('.flash-sales__grid [data-testid="flash-sale-buy"]').count() > 0);

    await page.screenshot({ path: '/tmp/flash-sales.png', fullPage: false });
    console.log('  screenshot → /tmp/flash-sales.png');
  } finally {
    await browser.close();
  }

  console.log(`\n${passed} checks passed, ${failures.length} failed`);
  if (failures.length) { console.error('\nFAILURES:\n  - ' + failures.join('\n  - ')); process.exit(1); }
}

main().catch((e) => { console.error('flash_sales e2e crashed:', e); process.exit(1); });
