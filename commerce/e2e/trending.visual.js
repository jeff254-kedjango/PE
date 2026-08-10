/**
 * Visual check for the redesigned §8.5 trending rail — drives a real Chromium browser against the
 * live stack, seeds a handful of near-Nairobi LISTING boosts (varied categories so the card colors +
 * icons differ), logs in through the weespas UI, screenshots /trade, then revokes the seeded grants.
 *
 * It is a screenshot harness, not an assertion suite (the contract is covered by trending.fe.e2e.js
 * + vitest); it exists so a human can eyeball the fixed right-side card, the left-aligned feed, and
 * the category-colored product cards.
 *
 * Run (stack up — weespas :8000, commerce :8003, FE :5174):
 *   cd PE/commerce/e2e
 *   NODE_PATH=/home/jeff/PE/InSAR-Final-main/frontend/node_modules \
 *     node trending.visual.js
 *   → writes /tmp/trending-rail.png
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
const OUT = process.env.OUT || '/tmp/trending-rail.png';

// TradePage's default centre when geolocation is denied (Nairobi CBD) — seed right on it.
const NBO = { lat: -1.2921, lng: 36.8219 };
const RUN = `vis-${Date.now()}`;
registerCleanup(RUN);   // remove this run's shops/listings from the live DB on exit (any path)
const authH = (t) => ({ Authorization: `Bearer ${t}` });

// A spread of products across categories so the rail shows the color/icon variety (the lunchtime
// "Nyama Choma" case study + neighbours). title, price (cents), category, tier.
const PRODUCTS = [
  ['Nyama Choma',       35000, 'restaurant',  'sovereign'],
  ['Fresh Sukuma',       3000, 'greengrocer', 'sovereign'],
  ['Mandazi (6pc)',      5000, 'bakery',      'sovereign'],
  ['Goat Ribs / kg',    45000, 'butchery',    'sovereign'],
  ['Phone Charger',     80000, 'electronics', 'sovereign'],
  ['Ankara Dress',     250000, 'boutique',    'sovereign'],
  ['Leather Sandals',  120000, 'shoes',       'sovereign'],
  ['Paracetamol',        2000, 'pharmacy',    'sovereign'],
];

async function main() {
  const ctx = await request.newContext();
  const grants = [];

  // Authenticate up front via the weespas API (same call the AuthContext login makes) so we can
  // seed the browser session deterministically — driving the login FORM is flaky (the Email tab's
  // autoFocus races the fill, and goAfterLogin() bounces /trade back to home on success).
  let weespasToken = null;
  let weespasUser = null;
  {
    const lr = await ctx.post(`${WEESPAS_API}/auth/login`, {
      headers: { 'Content-Type': 'application/json' },
      data: { email: EMAIL, password: PASSWORD },
    });
    if (!lr.ok()) { console.error('weespas login failed:', lr.status(), await lr.text()); process.exit(1); }
    const body = await lr.json();
    weespasToken = body.token;
    weespasUser = body.user;
  }

  // Seed: one shop per category (near the buyer centre) + a boosted in-stock listing each.
  for (const [title, price, category, tier] of PRODUCTS) {
    const tok = seller(`${RUN}-${category}`);
    let r = await ctx.post(`${COMMERCE_API}/shops`, {
      headers: authH(tok),
      data: { name: `${title} Shop ${RUN}`, lat: NBO.lat, lng: NBO.lng,
              display_name: title, category },
    });
    if (r.status() !== 201) { console.error(`shop seed failed (${category}):`, r.status(), await r.text()); continue; }
    const shop = await r.json();
    r = await ctx.post(`${COMMERCE_API}/shops/${shop.id}/listings`, {
      headers: authH(tok),
      data: { title: `${title} ${RUN}`, price_cents: price, stock_qty: 9 },
    });
    if (r.status() !== 201) { console.error(`listing seed failed (${category}):`, r.status(), await r.text()); continue; }
    const listing = await r.json();
    r = await ctx.post(`${COMMERCE_API}/boosts`, {
      headers: authH(tok),
      data: { target_type: 'listing', target_id: listing.id, tier },
    });
    if (r.status() !== 201) { console.error(`boost failed (${category}):`, r.status(), await r.text()); continue; }
    grants.push({ tok, id: (await r.json()).id });
  }
  console.log(`seeded ${grants.length}/${PRODUCTS.length} boosted products`);

  // The browser drive is wrapped so a launch/login failure can NEVER skip cleanup (an orphaned
  // sovereign grant lingers 24 h in the shared DB otherwise).
  try {
    // Deny geolocation (TradePage falls back to the Nairobi default we seeded on); wide viewport so
    // the ≥1101px rail shows.
    const browser = await chromium.launch();
    try {
      const bctx = await browser.newContext({
        viewport: { width: 1440, height: 900 },
        permissions: [], // geolocation denied → default centre
      });
      // Seed the AuthContext session before any app code runs: it restores from these localStorage
      // keys on mount (then validates the token against /auth/me). This makes isAuthenticated true
      // without driving the flaky login form, and lands us straight on /trade (no home-bounce).
      await bctx.addInitScript(
        ([t, u]) => {
          localStorage.setItem('weespas_token', t);
          localStorage.setItem('weespas_user', u);
        },
        [weespasToken, JSON.stringify(weespasUser)],
      );
      const page = await bctx.newPage();

      await page.goto(`${FE}/trade`, { waitUntil: 'networkidle' });
      console.log(`  trade url: ${page.url()}`);
      await page.waitForSelector('[data-testid="trending-rail"]', { timeout: 15000 }).catch(() => {
        console.error('WARN: trending-rail not found — screenshotting anyway for diagnosis');
      });
      await page.waitForTimeout(1500);
      await page.screenshot({ path: OUT, fullPage: false });
      console.log(`screenshot → ${OUT}`);
      console.log(`visible trending cards: ${await page.locator('[data-testid="trending-card"]').count()}`);
    } finally {
      await browser.close();
    }
  } finally {
    // Cleanup: revoke every seeded grant, always.
    let revoked = 0;
    for (const g of grants) {
      const r = await ctx.delete(`${COMMERCE_API}/boosts/${g.id}`, { headers: authH(g.tok) });
      if (r.status() === 204) revoked += 1;
    }
    console.log(`revoked ${revoked}/${grants.length} grants`);
    await ctx.dispose();
  }
}

main().catch((e) => { console.error('visual crashed:', e); process.exit(1); });
