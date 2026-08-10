/**
 * Shop hovercard MOBILE TOUCH pass — a real-device a11y/reachability check the unit + API layers
 * cannot do. The §8 shop profile hovercard opens on TAP on a coarse pointer; this drives the live
 * Trade feed on a touch phone viewport (375×812, hasTouch) and asserts:
 *   - the avatar trigger is a ≥44×44 touch target (WCAG 2.5.5 / platform min),
 *   - a tap opens the profile panel,
 *   - the open panel stays fully inside the viewport (no right-edge clip) with NO h-overflow,
 *   - an outside tap dismisses it.
 *
 * Run (weespas :8000, commerce :8003, FE :5174):
 *   cd PE/commerce/e2e
 *   NODE_PATH=/home/jeff/PE/InSAR-Final-main/frontend/node_modules node shop_hovercard.touch.e2e.js
 */
const { chromium, request, devices } = require('playwright');

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
    // A real touch phone: coarse pointer (→ the tap path), touch events, mobile viewport.
    const bctx = await browser.newContext({
      viewport: { width: 375, height: 812 },
      hasTouch: true,
      isMobile: true,
      permissions: [],   // geolocation DENIED → Nairobi CBD fallback
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

    // Wait for the social feed to render a post with a seller avatar trigger.
    const trigger = page.locator('[data-testid="shop-avatar-trigger"]').first();
    await trigger.waitFor({ timeout: 15000 }).catch(() => {});
    check('a post with a shop avatar trigger renders', await trigger.count() > 0);
    if (await trigger.count() === 0) {
      console.log('  (no posts with shop avatars in range — cannot assert the touch path)');
      await bctx.close();
      throw new Error('no shop-avatar trigger to test');
    }

    // 1. Tap-target size ≥44×44.
    const tbox = await trigger.boundingBox();
    check('avatar trigger tap target ≥44×44',
      !!tbox && tbox.width >= 44 && tbox.height >= 44,
      `box=${JSON.stringify(tbox)}`);

    // 2. Tap opens the profile panel.
    await trigger.tap();
    const panel = page.locator('[data-testid="shop-hovercard"]').first();
    const opened = await panel.isVisible().catch(() => false)
      || await panel.waitFor({ timeout: 5000 }).then(() => true).catch(() => false);
    check('tap opens the shop profile panel', opened);

    // 3. Open panel fully inside the viewport + no horizontal overflow.
    if (opened) {
      const pbox = await panel.boundingBox();
      check('panel does not clip the right viewport edge',
        !!pbox && pbox.x >= 0 && pbox.x + pbox.width <= 375 + 1,
        `panel=${JSON.stringify(pbox)}`);
      const ov = await page.evaluate(() => ({
        scrollW: document.documentElement.scrollWidth,
        clientW: document.documentElement.clientWidth,
      }));
      check('no horizontal overflow while panel open (scrollW ≤ clientW + 1)',
        ov.scrollW <= ov.clientW + 1, `scrollW=${ov.scrollW} clientW=${ov.clientW}`);
    }

    await page.screenshot({ path: '/tmp/shop-hovercard-375.png', fullPage: false });
    console.log('  screenshot → /tmp/shop-hovercard-375.png');

    // 4. Outside tap dismisses it. Tap the page top-left corner, well clear of the panel.
    if (opened) {
      await page.touchscreen.tap(5, 5);
      const gone = await panel.waitFor({ state: 'hidden', timeout: 5000 }).then(() => true).catch(() => false);
      check('outside tap dismisses the panel', gone);
    }

    await bctx.close();
  } finally {
    await browser.close();
  }

  console.log(`\n${passed} checks passed, ${failures.length} failed`);
  if (failures.length) { console.error('\nFAILURES:\n  - ' + failures.join('\n  - ')); process.exit(1); }
}

main().catch((e) => { console.error('shop-hovercard touch e2e crashed:', e); process.exit(1); });
