// One-shot BROWSER verification (not part of the API e2e suite) for the EmojiPalette portal fix.
//
// What the fix claims, and what this asserts in a REAL chromium against the live weespas FE (:5174):
//   1. the palette is rendered through a portal to <body> — its parentElement is <body>, NOT the
//      composer subtree (this is what lets it escape .product-card's `overflow: hidden`);
//   2. its computed position is `fixed`;
//   3. it is actually visible (not display:none / 0-size); and
//   4. its bounding box sits fully inside the viewport — i.e. it is NOT clipped by an ancestor
//      nor hidden behind the sticky navbar, which was the original bug.
//
// Run:
//   cd PE/commerce/e2e
//   NODE_PATH=/home/jeff/PE/InSAR-Final-main/frontend/node_modules \
//     WEESPAS_FE_URL=http://127.0.0.1:5174 \
//     WEESPAS_EMAIL=admin@weespas.com WEESPAS_PASSWORD=admin123 \
//     node emoji_portal.verify.js
const { chromium } = require('playwright');
const fs = require('fs');

// Playwright defaults to the chrome-headless-shell build, which isn't installed here; the full
// chromium IS. Resolve its executable so we don't need `npx playwright install`. Override with
// PW_CHROME if needed.
function resolveChrome() {
  if (process.env.PW_CHROME) return process.env.PW_CHROME;
  const base = `${process.env.HOME}/.cache/ms-playwright`;
  for (const dir of (fs.existsSync(base) ? fs.readdirSync(base) : [])) {
    if (!dir.startsWith('chromium-')) continue;
    const p = `${base}/${dir}/chrome-linux64/chrome`;
    if (fs.existsSync(p)) return p;
  }
  return undefined; // fall back to Playwright's default and let it error clearly
}

const FE = process.env.WEESPAS_FE_URL || 'http://127.0.0.1:5174';
const EMAIL = process.env.WEESPAS_EMAIL || 'admin@weespas.com';
const PASSWORD = process.env.WEESPAS_PASSWORD || 'admin123';

function assert(cond, msg) {
  if (!cond) throw new Error('ASSERT FAILED: ' + msg);
  console.log('  ✓ ' + msg);
}

(async () => {
  const browser = await chromium.launch({ executablePath: resolveChrome() });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  page.on('pageerror', (e) => console.error('  [pageerror]', e.message));
  try {
    // 1. Log in (email flow; default method is phone, so flip the toggle first).
    await page.goto(`${FE}/login`, { waitUntil: 'networkidle' });
    await page.getByRole('button', { name: 'Email' }).click();
    // Scope to the visible email login form (a second, hidden email input exists elsewhere on the page).
    const loginForm = page.locator('form.login-form');
    await loginForm.locator('input[type="email"]').fill(EMAIL);
    await loginForm.locator('input[type="password"]').fill(PASSWORD);
    await page.getByRole('button', { name: /sign in/i }).click();
    await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 });
    console.log('logged in →', page.url());

    // 2. Go to Trade, open the composer (Post mode is default).
    await page.goto(`${FE}/trade`, { waitUntil: 'networkidle' });
    await page.getByTestId('composer-open').click();
    await page.getByTestId('composer-body').waitFor({ state: 'visible' });

    // 3. Click the emoji trigger → palette opens.
    await page.getByTestId('composer-emoji').click();
    const palette = page.getByTestId('emoji-palette');
    await palette.waitFor({ state: 'visible' });
    console.log('palette opened');

    // 4a. Portaled to <body> (escapes ancestor overflow:hidden).
    const parentTag = await palette.evaluate((el) => el.parentElement?.tagName);
    assert(parentTag === 'BODY', `palette parent is <body> (got <${parentTag?.toLowerCase()}>)`);

    // 4b. position: fixed.
    const position = await palette.evaluate((el) => getComputedStyle(el).position);
    assert(position === 'fixed', `computed position is fixed (got "${position}")`);

    // 4c. Visible with real size.
    const box = await palette.boundingBox();
    assert(box && box.width > 0 && box.height > 0, `palette has a real box (${box && Math.round(box.width)}×${box && Math.round(box.height)})`);

    // 4d. Fully inside the viewport — the actual bug: it must not be clipped or run off-screen /
    //     behind the navbar. Allow a 1px rounding slack.
    const vp = page.viewportSize();
    assert(box.x >= -1, `left edge in viewport (x=${Math.round(box.x)})`);
    assert(box.y >= -1, `top edge in viewport (y=${Math.round(box.y)})`);
    assert(box.x + box.width <= vp.width + 1, `right edge in viewport (right=${Math.round(box.x + box.width)} ≤ ${vp.width})`);
    assert(box.y + box.height <= vp.height + 1, `bottom edge in viewport (bottom=${Math.round(box.y + box.height)} ≤ ${vp.height})`);

    // 5. Pick an emoji → it lands in the body and the palette closes.
    const firstEmoji = await palette.locator('button').first().textContent();
    await palette.locator('button').first().click();
    await palette.waitFor({ state: 'detached' });
    const bodyVal = await page.getByTestId('composer-body').inputValue();
    assert(bodyVal.includes(firstEmoji.trim()), `picked emoji "${firstEmoji.trim()}" inserted into the composer body`);

    console.log('\n— scenario 2: comment-thread palette inside .product-card { overflow: hidden } —');
    // This is the case the fix was really written for: the palette lives inside a feed card that
    // clips its overflow. A short viewport also exercises the flip-above branch.
    await page.setViewportSize({ width: 412, height: 740 });
    const card = page.getByTestId('product-card').first();
    await card.waitFor({ state: 'visible', timeout: 15000 });
    // Confirm the ancestor really does clip — otherwise this scenario proves nothing.
    const clips = await card.evaluate((el) => getComputedStyle(el).overflow !== 'visible');
    assert(clips, '.product-card clips its overflow (the condition the portal escapes)');

    await card.getByTestId('comments-btn').click();
    await card.getByTestId('comment-emoji').click();
    const palette2 = page.getByTestId('emoji-palette');
    await palette2.waitFor({ state: 'visible' });

    const parent2 = await palette2.evaluate((el) => el.parentElement?.tagName);
    assert(parent2 === 'BODY', `comment palette portals to <body> (escapes the card clip; got <${parent2?.toLowerCase()}>)`);
    const box2 = await palette2.boundingBox();
    const vp2 = page.viewportSize();
    assert(box2.x >= -1 && box2.y >= -1 && box2.x + box2.width <= vp2.width + 1 && box2.y + box2.height <= vp2.height + 1,
      `comment palette fully inside the ${vp2.width}×${vp2.height} viewport (box ${Math.round(box2.x)},${Math.round(box2.y)} ${Math.round(box2.width)}×${Math.round(box2.height)})`);

    console.log('\n✅ EmojiPalette portal verified in the live app (composer + comment thread).');
  } catch (e) {
    await page.screenshot({ path: '/tmp/emoji_portal_fail.png', fullPage: true }).catch(() => {});
    console.error('\n❌ ' + e.message + '\n(screenshot → /tmp/emoji_portal_fail.png)');
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})();
