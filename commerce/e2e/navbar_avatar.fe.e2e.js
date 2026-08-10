/**
 * Live UI proof for the signed-in navbar account chip (ProfileMenu).
 *
 * The owner's request: when a user is signed in, their avatar/profile picture shows in the navbar
 * slot where the "Sign Up" CTA sits for logged-out visitors (replacing the old first-name link).
 * Hovering the avatar reveals a small popup reading "My Profile"; clicking it routes to /profile.
 * The avatar itself never navigates.
 *
 * Drives real Chromium against the live weespas frontend and asserts:
 *   1. Logged OUT: the navbar shows the "Sign Up" CTA and NO avatar chip.
 *   2. Logged IN: the avatar chip (.navbar__avatar) renders where the name/CTA was; the old plain
 *      first-name link is gone; "Sign Up" is gone.
 *   3. Hovering the avatar reveals the "My Profile" popup, which links to /profile.
 *   4. Clicking "My Profile" navigates to /profile (SPA).
 *
 * Run (stack up — weespas :8000, FE :5174):
 *   cd PE/commerce/e2e
 *   NODE_PATH=/home/jeff/PE/InSAR-Final-main/frontend/node_modules node navbar_avatar.fe.e2e.js
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

async function main() {
  // Weespas login → token + user for localStorage injection (same as the other FE e2es).
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
    // ── 1) Logged OUT: Sign Up CTA present, no avatar chip. ──
    const anon = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const anonPage = await anon.newPage();
    await anonPage.goto(`${FE}/`, { waitUntil: 'networkidle' });
    const signUp = anonPage.locator('.navbar__link--cta', { hasText: 'Sign Up' });
    await signUp.first().waitFor({ timeout: 10000 }).catch(() => {});
    check('logged-out navbar shows the "Sign Up" CTA', await signUp.count() > 0);
    check('logged-out navbar has NO avatar chip', await anonPage.locator('.navbar__avatar').count() === 0);
    await anon.close();

    // ── 2) Logged IN: avatar chip replaces the name/CTA. ──
    const bctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await bctx.addInitScript(([t, u]) => {
      localStorage.setItem('weespas_token', t);
      localStorage.setItem('weespas_user', u);
    }, [token, JSON.stringify(user)]);
    const page = await bctx.newPage();
    await page.goto(`${FE}/`, { waitUntil: 'networkidle' });

    const trigger = page.locator('[data-testid="navbar-avatar-trigger"]');
    await trigger.waitFor({ timeout: 10000 }).catch(() => {});
    check('signed-in navbar shows the avatar chip', await page.locator('.navbar__avatar').count() > 0);
    check('avatar trigger is a button (never navigates on its own)',
      (await trigger.evaluate((el) => el.tagName).catch(() => '')) === 'BUTTON');
    check('the old "Sign Up" CTA is gone when signed in',
      await page.locator('.navbar__link--cta', { hasText: 'Sign Up' }).count() === 0);

    // ── 3) Hover reveals the "My Profile" popup linking to /profile. ──
    await trigger.hover();
    const menu = page.locator('[data-testid="navbar-profile-menu"]');
    await menu.waitFor({ timeout: 5000 }).catch(() => {});
    check('hovering the avatar reveals the account popup', await menu.count() > 0);
    const item = page.locator('.profile-menu__item', { hasText: 'My Profile' });
    check('popup reads "My Profile"', await item.count() > 0);
    check('"My Profile" links to /profile',
      (await item.first().getAttribute('href')) === '/profile');

    await page.screenshot({ path: '/tmp/navbar-avatar.png' });
    console.log('  screenshot → /tmp/navbar-avatar.png');

    // ── 4) Clicking "My Profile" navigates to /profile. ──
    await item.first().click();
    await page.waitForURL('**/profile', { timeout: 8000 }).catch(() => {});
    check('clicking "My Profile" navigates to /profile', page.url().endsWith('/profile'));
  } finally {
    await browser.close();
  }

  console.log(`\n${passed} checks passed, ${failures.length} failed`);
  if (failures.length) { console.error('\nFAILURES:\n  - ' + failures.join('\n  - ')); process.exit(1); }
}

main().catch((e) => { console.error('e2e crashed:', e); process.exit(1); });
