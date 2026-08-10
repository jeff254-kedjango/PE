/**
 * Trade page RESPONSIVE pass — drives the live Trade surface at several viewports and asserts the
 * layout invariants the CSS breakpoints promise, plus captures a screenshot per width for a human
 * eyeball. This is the "real-device" check the API + unit tests structurally cannot do.
 *
 * Viewports (matched to TradePage.css / TrendingRail.css breakpoints):
 *   - mobile   375×812  (<600: halved gutters; both side rails hidden)
 *   - tablet   834×1112 (600–1100: single centred feed; both side rails hidden)
 *   - edge    1100×900  (the cutoff: rails still hidden at 1100)
 *   - edge    1101×900  (rails appear at 1101)
 *   - wide    1440×900  (full 3-column layout)
 *
 * Asserts at each width:
 *   - the feed column is present and does NOT overflow the viewport horizontally (no h-scroll);
 *   - the trending rail + right column are HIDDEN <1101 and VISIBLE ≥1101 (the exact contract);
 *   - a re-request-location control is reachable (the mobile gap check — see FINDINGS).
 *
 * Run (stack up — weespas :8000, commerce :8003, FE :5174):
 *   cd PE/commerce/e2e
 *   NODE_PATH=/home/jeff/PE/InSAR-Final-main/frontend/node_modules node trade_responsive.visual.js
 */
const { chromium, request } = require('playwright');

const WEESPAS = process.env.WEESPAS_BASE_URL || 'http://127.0.0.1:8000';
const FE = process.env.FE_BASE_URL || 'http://127.0.0.1:5174';
const WEESPAS_API = `${WEESPAS}/api/v1`;
const EMAIL = process.env.WEESPAS_EMAIL || 'admin@weespas.com';
const PASSWORD = process.env.WEESPAS_PASSWORD || 'admin123';

let passed = 0;
const failures = [];
const check = (name, cond, detail = '') => {
  if (cond) { passed += 1; console.log(`  ✓ ${name}`); }
  else { failures.push(`${name}${detail ? ` — ${detail}` : ''}`); console.log(`  ✗ ${name}${detail ? ` — ${detail}` : ''}`); }
};

const VIEWPORTS = [
  { name: 'mobile-375', w: 375, h: 812, railVisible: false },
  { name: 'tablet-834', w: 834, h: 1112, railVisible: false },
  { name: 'edge-1100', w: 1100, h: 900, railVisible: false },
  { name: 'edge-1101', w: 1101, h: 900, railVisible: true },
  { name: 'wide-1440', w: 1440, h: 900, railVisible: true },
];

async function main() {
  const ctx = await request.newContext();
  const lr = await ctx.post(`${WEESPAS_API}/auth/login`, {
    headers: { 'Content-Type': 'application/json' },
    data: { email: EMAIL, password: PASSWORD },
  });
  if (!lr.ok()) { console.error('weespas login failed:', lr.status(), await lr.text()); process.exit(1); }
  const body = await lr.json();
  const weespasToken = body.token;
  const weespasUser = body.user;
  await ctx.dispose();

  const browser = await chromium.launch();
  try {
    for (const vp of VIEWPORTS) {
      console.log(`\n── ${vp.name} (${vp.w}×${vp.h}) ──`);
      const bctx = await browser.newContext({
        viewport: { width: vp.w, height: vp.h },
        permissions: [],  // geolocation DENIED → the app falls back to the Nairobi CBD default
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

      // The feed column should always render (auth + session ok).
      const feedCol = page.locator('.trade-page__feed-col');
      await feedCol.waitFor({ timeout: 15000 }).catch(() => {});
      check('feed column renders', await feedCol.count() > 0);

      // No horizontal overflow: scrollWidth must not exceed the viewport width (a common responsive
      // bug — a fixed-width child pushing the page wider than the screen).
      const overflow = await page.evaluate(() => {
        const d = document.documentElement;
        return { scrollW: d.scrollWidth, clientW: d.clientWidth };
      });
      check('no horizontal overflow (scrollWidth ≤ clientWidth + 1)',
        overflow.scrollW <= overflow.clientW + 1, `scrollW=${overflow.scrollW} clientW=${overflow.clientW}`);

      // Rail visibility contract: hidden <1101, visible ≥1101. Use bounding box (display:none → null).
      const railBox = await page.locator('.trending-rail').first().boundingBox().catch(() => null);
      const rightBox = await page.locator('.trade-page__rail-right').first().boundingBox().catch(() => null);
      // The trending rail only renders when there ARE boosted products; treat "no box" as hidden.
      const railShown = !!railBox && railBox.width > 0;
      const rightShown = !!rightBox && rightBox.width > 0;
      if (vp.railVisible) {
        check('right control column visible ≥1101', rightShown, `rightBox=${JSON.stringify(rightBox)}`);
      } else {
        check('right control column hidden <1101', !rightShown);
        check('trending rail hidden <1101', !railShown);
      }

      // Re-request-location reachability. At ANY width a user who denied geo must be able to retry.
      // ≥1101: the "Search my location" rail button. <1101 that rail is display:none, so the retry must
      // live in the always-visible feed column — the inline .trade-page__geo-retry (or the
      // ProductFeed CTA when the feed itself renders the location-denied state).
      const railLocBtn = page.locator('.trade-page__rail-btn--ghost', { hasText: /location/i });
      const railLocVisible = await railLocBtn.isVisible().catch(() => false);
      const feedLocBtn = page.locator('.product-feed__cta', { hasText: /location/i });
      const feedLocVisible = await feedLocBtn.isVisible().catch(() => false);
      const geoRetryBtn = page.locator('.trade-page__geo-retry', { hasText: /location/i });
      const geoRetryVisible = await geoRetryBtn.isVisible().catch(() => false);
      const anyLocControl = railLocVisible || feedLocVisible || geoRetryVisible;
      check('a re-request-location control is reachable', anyLocControl,
        `railBtn=${railLocVisible} feedCta=${feedLocVisible} geoRetry=${geoRetryVisible}`);

      const out = `/tmp/trade-responsive-${vp.name}.png`;
      await page.screenshot({ path: out, fullPage: false });
      console.log(`  screenshot → ${out}`);
      await bctx.close();
    }
  } finally {
    await browser.close();
  }

  console.log(`\n${passed} checks passed, ${failures.length} failed`);
  if (failures.length) { console.error('\nFAILURES:\n  - ' + failures.join('\n  - ')); process.exit(1); }
}

main().catch((e) => { console.error('responsive visual crashed:', e); process.exit(1); });
