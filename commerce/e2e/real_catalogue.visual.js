/**
 * Live proof that the REAL, human-uploaded catalogue reaches the SCREEN — DB → API → render.
 *
 * After the clean-slate wipe (only genuine weespas users remain: Elite Kicks, Conso, Eva — no
 * seeded/admin/trending data), this drives the real weespas frontend at /trade signed in through the
 * real token bridge, forces the buyer's geolocation next to a real shop, and asserts that a real
 * product the owner uploaded actually renders as a feed card WITH a decoded image (naturalWidth > 0,
 * not a broken tag). This is the acceptance check the owner asked for: "all commerce pipelines
 * working e2e from database to screen."
 *
 * It asserts REAL data specifically — it looks for a known real product title (e.g. a Sneakers /
 * Blazer listing) rather than "any card", so a green run means a genuine upload surfaced, not filler.
 *
 * Run (weespas :8000, commerce :8003, FE :5174):
 *   NODE_PATH=/home/jeff/PE/InSAR-Final-main/frontend/node_modules node real_catalogue.visual.js
 */
const { chromium, request } = require('playwright');

const WEESPAS = process.env.WEESPAS_BASE_URL || 'http://127.0.0.1:8000';
const FE = process.env.FE_BASE_URL || 'http://127.0.0.1:5174';
const WEESPAS_API = `${WEESPAS}/api/v1`;
const EMAIL = process.env.WEESPAS_EMAIL || 'admin@weespas.com';
const PASSWORD = process.env.WEESPAS_PASSWORD || 'admin123';

// A buyer standing between the two Nairobi shops (Conso ~Kangemi, Elite Kicks ~Langata) so both are
// within the default feed radius. These are the REAL shops' coordinates from the DB.
const BUYER = { lat: -1.30, lng: 36.82 };

// Real product-title fragments that exist ONLY because a human uploaded them (post-wipe there is no
// seeded data that could contain these). Seeing any one on screen proves the real pipeline.
const REAL_TITLE_FRAGMENTS = ['Sneakers', 'Blazer', 'New Balance', 'Puma', 'Adidas', 'AirForce'];

let passed = 0;
const failures = [];
const check = (name, cond, detail = '') => {
  if (cond) { passed += 1; console.log(`  ✓ ${name}`); }
  else { failures.push(`${name}${detail ? ` — ${detail}` : ''}`); console.log(`  ✗ ${name}${detail ? ` — ${detail}` : ''}`); }
};

async function main() {
  const ctx = await request.newContext();
  const lr = await ctx.post(`${WEESPAS_API}/auth/login`, {
    headers: { 'Content-Type': 'application/json' },
    data: { email: EMAIL, password: PASSWORD },
  });
  if (!lr.ok()) { console.error('weespas login failed:', lr.status(), await lr.text()); process.exit(1); }
  const body = await lr.json();
  const token = body.token, user = body.user;
  await ctx.dispose();

  const browser = await chromium.launch();
  try {
    // Grant + fix geolocation so the proximity feed resolves next to the real shops deterministically
    // (not wherever the CI host geolocates to).
    const bctx = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      geolocation: { latitude: BUYER.lat, longitude: BUYER.lng },
      permissions: ['geolocation'],
    });
    await bctx.addInitScript(([t, u]) => {
      localStorage.setItem('weespas_token', t);
      localStorage.setItem('weespas_user', u);
    }, [token, JSON.stringify(user)]);
    const page = await bctx.newPage();
    await page.goto(`${FE}/trade`, { waitUntil: 'networkidle' });

    // Feed cards render at all.
    await page.locator('[data-testid="product-card"]').first().waitFor({ timeout: 15000 }).catch(() => {});
    const cards = await page.locator('[data-testid="product-card"]').count();
    check('real catalogue renders feed cards', cards > 0, `cards=${cards}`);

    // A KNOWN real product title is on screen (proves it's genuine data, not filler).
    const bodyText = await page.locator('body').innerText();
    const foundTitle = REAL_TITLE_FRAGMENTS.find((frag) => bodyText.includes(frag));
    check('a real human-uploaded product is visible on screen', !!foundTitle,
      foundTitle ? `matched "${foundTitle}"` : `none of ${REAL_TITLE_FRAGMENTS.join(', ')} found`);

    // Its image actually DECODED (naturalWidth > 0) — the DB media URL resolved through the weespas
    // /uploads host and the bytes loaded. This is the full DB→screen media path.
    await page.waitForTimeout(700); // let lazy images decode
    const decoded = await page.$$eval('.media-carousel__img',
      (imgs) => imgs.filter((im) => im.complete && im.naturalWidth > 0).length);
    check('at least one real product image decoded on screen', decoded > 0, `decoded imgs=${decoded}`);

    await page.screenshot({ path: '/tmp/real-catalogue.png', fullPage: false });
    console.log('  screenshot → /tmp/real-catalogue.png');
  } finally {
    await browser.close();
  }

  console.log(`\n${passed} checks passed, ${failures.length} failed`);
  if (failures.length) { console.error('\nFAILURES:\n  - ' + failures.join('\n  - ')); process.exit(1); }
}

main().catch((e) => { console.error('e2e crashed:', e); process.exit(1); });
