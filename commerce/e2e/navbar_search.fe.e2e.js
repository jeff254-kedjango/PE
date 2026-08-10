/**
 * Live UI proof for the NAVBAR UNIFIED SEARCH (§search) — the inline, YouTube-style search box that
 * lives in the navbar and searches BOTH weespas properties (their existing FTS) and commerce trade
 * listings (the new GET /api/v1/search). The two backends are queried CONCURRENTLY and merged
 * client-side into ONE dropdown with two sections: "Homes" and "Shops & Products". No modal, no tabs
 * — the matches drop straight down as a list under the box.
 *
 * Why this test earns its place: the trade half is a cross-service path (weespas FE → commerce
 * /search, RS256 bridge token, nearest-first ranking) with a subtle money-unit seam — the trade
 * price is integer MINOR units (cents) and MUST be divided by 100 for display, unlike the property
 * price (major units). A raw render shows "KES 250,000" where "KES 2,500" is correct. That bug is
 * invisible to a type-check and only a live render catches it, so this spec pins it.
 *
 * It SEEDS its own uniquely-titled listing (so the search term is deterministic against live data)
 * and tears it down on any exit via registerCleanup(RUN). No committed fixtures; the shop/listing
 * carry the RUN tag the shared cleanup removes.
 *
 * Asserts:
 *   1. The inline search box is present in the signed-in desktop navbar, rests at 280px and grows
 *      to 320px on focus; and an active navbar link shows the lime underline with NO grey tint.
 *   2. Focusing + typing the unique seed term opens the dropdown with a "Shops & Products" section
 *      showing a non-zero count.
 *   3. The seeded listing renders in that section with its price in MAJOR units (cents/100),
 *      and the undivided raw-cents figure appears NOWHERE.
 *   4. Clicking the trade result deep-links to the seller storefront (/trade/sellers/:sellerId).
 *   5. Logged OUT: typing a real property term shows a "Homes" section; the "Shops & Products"
 *      section is NEVER offered (commerce needs a session). The old hero search bar is gone.
 *
 * Run (stack up — weespas :8000, commerce :8003, FE :5174):
 *   cd PE/commerce/e2e
 *   NODE_PATH=/home/jeff/PE/InSAR-Final-main/frontend/node_modules node navbar_search.fe.e2e.js
 */
const { chromium, request } = require('playwright');
const { registerCleanup } = require('./jwt');

const WEESPAS = process.env.WEESPAS_BASE_URL || 'http://127.0.0.1:8000';
const COMMERCE = process.env.COMMERCE_BASE_URL || 'http://127.0.0.1:8003';
const FE = process.env.FE_BASE_URL || 'http://127.0.0.1:5174';
const WEESPAS_API = `${WEESPAS}/api/v1`;
const COMMERCE_API = `${COMMERCE}/api/v1`;
const EMAIL = process.env.WEESPAS_EMAIL || 'admin@weespas.com';
const PASSWORD = process.env.WEESPAS_PASSWORD || 'admin123';

// A unique, unlikely-to-collide search term seeded into the listing title. Kept alphabetic (no
// regex/LIKE metacharacters) so it round-trips through the search path unescaped and unambiguous.
const RUN = `navsearch${Date.now()}`;
registerCleanup(RUN);
// Kilimani demo centroid — the same fallback the box uses when geolocation is absent, so the
// seeded listing sits at the ranking origin and surfaces first.
const KILIMANI = { lat: -1.2907, lng: 36.7895 };
// Seed price: 250000 cents = KES 2,500. The exact figures the render assertions below key on.
const PRICE_CENTS = 250000;
const PRICE_MAJOR = 'KES 2,500';
const PRICE_RAW = 'KES 250,000'; // the wrong (undivided) render — must NOT appear
// A property term that returns real seeded listings (42 at time of writing) so the anon "Homes"
// section has something to show. Discovery-only, not seeded by this test.
const PROPERTY_TERM = 'house';

let passed = 0;
const failures = [];
const check = (name, cond, detail = '') => {
  if (cond) { passed += 1; console.log(`  ✓ ${name}`); }
  else { failures.push(`${name}${detail ? ` — ${detail}` : ''}`); console.log(`  ✗ ${name}${detail ? ` — ${detail}` : ''}`); }
};
const eq = (name, a, b) => check(name, a === b, `expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
const authH = (t) => ({ Authorization: `Bearer ${t}` });

// Focus the inline navbar box and type — the desktop variant opens its dropdown on focus, so we
// click the input (which sets focus) then fill. Shared by both flows.
async function typeInBox(page, term) {
  const input = page.locator('.navbar-search--inline .navbar-search__input');
  await input.waitFor({ timeout: 10000 });
  await input.click();
  await input.fill(term);
  return input;
}

async function main() {
  const ctx = await request.newContext();

  // 1) Weespas login → token + user (for FE localStorage injection AND the commerce bridge).
  let r = await ctx.post(`${WEESPAS_API}/auth/login`, {
    headers: { 'Content-Type': 'application/json' },
    data: { email: EMAIL, password: PASSWORD },
  });
  check('weespas login 200', r.ok(), `status ${r.status()}`);
  const login = await r.json();
  const token = login.token, user = login.user;

  // 2) Bridge → commerce RS256 session token.
  r = await ctx.get(`${WEESPAS_API}/commerce/session-token`, { headers: authH(token) });
  check('commerce session-token 200', r.ok(), `status ${r.status()}`);
  const cTok = (await r.json()).token;

  // 3) Seed a shop + a uniquely-titled listing at the Kilimani origin.
  r = await ctx.post(`${COMMERCE_API}/shops`, {
    headers: { ...authH(cTok), 'Content-Type': 'application/json' },
    data: { name: `Search Shop ${RUN}`, lat: KILIMANI.lat, lng: KILIMANI.lng, display_name: 'Search Seller' },
  });
  eq('create shop 201', r.status(), 201);
  const shop = await r.json();
  const sellerId = shop.seller_id;

  const title = `Widget ${RUN}`;
  r = await ctx.post(`${COMMERCE_API}/shops/${shop.id}/listings`, {
    headers: { ...authH(cTok), 'Content-Type': 'application/json' },
    data: { title, description: `A findable item ${RUN}.`, price_cents: PRICE_CENTS, stock_qty: 5, pricing_mode: 'fixed' },
  });
  eq('create listing 201', r.status(), 201);
  const listing = await r.json();

  // 3b) API sanity: the search endpoint itself returns the seeded listing (isolates FE from backend
  //     if the render step later fails — we know the data path works).
  r = await ctx.get(`${COMMERCE_API}/search?q=${encodeURIComponent(RUN)}&lat=${KILIMANI.lat}&lng=${KILIMANI.lng}`,
    { headers: authH(cTok) });
  eq('search endpoint 200', r.status(), 200);
  const apiResults = (await r.json()).results || [];
  check('search endpoint returns the seeded listing', apiResults.some((x) => x.listing_id === listing.id),
    `got ${apiResults.length} results`);
  await ctx.dispose();

  const browser = await chromium.launch();
  try {
    // ── Signed-in flow (desktop, inline box) ──
    const bctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await bctx.addInitScript(([t, u]) => {
      localStorage.setItem('weespas_token', t);
      localStorage.setItem('weespas_user', u);
    }, [token, JSON.stringify(user)]);
    const page = await bctx.newPage();
    await page.goto(`${FE}/`, { waitUntil: 'networkidle' });

    // 1) The inline search box is present in the navbar.
    const box = page.locator('.navbar-search--inline');
    await box.waitFor({ timeout: 10000 });
    check('inline navbar search box is present (signed-in desktop)', await box.count() > 0);

    // 1b) Desktop width: the box was widened by 10px (270→280 resting, 310→320 on focus, so the
    //     40px focus-grow delta is unchanged). Widths are computed layout, invisible to vitest.
    const restingW = Math.round((await box.boundingBox()).width);
    check('inline search box rests at 280px (widened by 10px)', restingW === 280, `${restingW}px`);

    // 1c) Active-link wayfinding: the lime underline carries the state on its own — the old grey
    //     background tint is gone (hover keeps its own tint via higher specificity).
    const activeLink = page.locator('.navbar__icon-btn--active').first();
    await activeLink.waitFor({ timeout: 8000 });
    const activeBg = await activeLink.evaluate((el) => getComputedStyle(el).backgroundColor);
    check('active navbar link has NO grey background tint',
      activeBg === 'rgba(0, 0, 0, 0)' || activeBg === 'transparent', activeBg);
    const underline = await activeLink.evaluate((el) => {
      const s = getComputedStyle(el, '::after');
      return { bg: s.backgroundColor, h: s.height };
    });
    check('active navbar link keeps its lime underline',
      underline.bg === 'rgb(191, 255, 0)' && underline.h === '2px', JSON.stringify(underline));

    // 2) Type the unique term; the dropdown opens with a populated "Shops & Products" section.
    await typeInBox(page, title);
    const focusedW = Math.round((await box.boundingBox()).width);
    check('inline search box grows to 320px while focused', focusedW === 320, `${focusedW}px`);
    const dropdown = page.locator('.navbar-search--inline .navbar-search__dropdown');
    await dropdown.waitFor({ timeout: 8000 });
    check('typing opens the anchored dropdown', await dropdown.count() > 0);
    // Section title carries a live count "Shops & Products · N"; wait for a non-zero N.
    await page.waitForFunction(() => {
      const s = Array.from(document.querySelectorAll('.navbar-search__section-title'))
        .find((e) => /Shops\s*&\s*Products/.test(e.textContent || ''));
      return s && /·\s*(\d+)/.test(s.textContent || '') && !/·\s*0\b/.test(s.textContent || '');
    }, { timeout: 8000 }).catch(() => {});
    const tradeSection = page.locator('.navbar-search__section-title', { hasText: 'Shops' });
    check('Shops & Products section is offered to the signed-in user', await tradeSection.count() > 0);

    // 3) The seeded listing renders with its title and the MAJOR-unit price (cents/100).
    const row = page.locator('.navbar-search__row', { hasText: title });
    await row.first().waitFor({ timeout: 8000 });
    check('seeded listing renders in the Shops & Products section', await row.count() > 0);
    check(`price shown in MAJOR units (${PRICE_MAJOR}, not raw cents)`,
      (await row.first().locator('.navbar-search__row-price').textContent() || '').includes(PRICE_MAJOR));
    check('no undivided raw-cents price anywhere in the dropdown',
      await dropdown.filter({ hasText: PRICE_RAW }).count() === 0);

    await page.screenshot({ path: '/tmp/navbar-search.png' });
    console.log('  screenshot → /tmp/navbar-search.png');

    // 4) Click the trade result → seller storefront deep-link.
    await row.first().click();
    await page.waitForURL(`**/trade/sellers/${sellerId}`, { timeout: 8000 }).catch(() => {});
    check('trade result deep-links to the seller storefront',
      page.url().includes(`/trade/sellers/${sellerId}`), page.url());
    await bctx.close();

    // ── 5) Logged-out flow: Homes section shows; Shops & Products is withheld. ──
    const anon = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const anonPage = await anon.newPage();
    await anonPage.goto(`${FE}/`, { waitUntil: 'networkidle' });
    // The hero's own search bar was removed once the navbar unified search shipped — discovery now
    // lives solely in the navbar. Assert the old hero search region is gone from the landing page.
    check('landing hero no longer carries its own search bar',
      await anonPage.locator('.hero__search-region, .hero__search-bar').count() === 0);

    await typeInBox(anonPage, PROPERTY_TERM);
    const anonDropdown = anonPage.locator('.navbar-search--inline .navbar-search__dropdown');
    await anonDropdown.waitFor({ timeout: 8000 });
    // Homes section appears (property search is public); wait for a non-zero count.
    await anonPage.waitForFunction(() => {
      const s = Array.from(document.querySelectorAll('.navbar-search__section-title'))
        .find((e) => /Homes/.test(e.textContent || ''));
      return s && /·\s*(\d+)/.test(s.textContent || '') && !/·\s*0\b/.test(s.textContent || '');
    }, { timeout: 8000 }).catch(() => {});
    check('anon: Homes section is offered',
      await anonPage.locator('.navbar-search__section-title', { hasText: 'Homes' }).count() > 0);
    check('anon: Shops & Products section is NOT offered',
      await anonPage.locator('.navbar-search__section-title', { hasText: 'Shops' }).count() === 0);
    await anon.close();
  } finally {
    await browser.close();
  }

  console.log(`\n${passed} checks passed, ${failures.length} failed`);
  if (failures.length) { console.error('\nFAILURES:\n  - ' + failures.join('\n  - ')); process.exit(1); }
}

main().catch((e) => { console.error('e2e crashed:', e); process.exit(1); });
