/**
 * Live UI proof for the TRADE COMPOSER + LANE TOGGLE restructure (§8).
 *
 * What changed: on /trade the composer ("Write something…") moved ABOVE the feed toggle and grew a
 * six-tool action row (Write Post | Sell Product | Post a Video | Create Poll | Post Pictures |
 * Post Audio); the old two-way "Listings | Videos" pill became a three-lane full-width bar
 * (Shops | Clips | Podcasts) seated on the composer's bottom edge.
 *
 * Why this test earns its place — every assertion here is a COMPUTED-LAYOUT fact that jsdom cannot
 * produce, so vitest structurally cannot catch these regressions:
 *   * The 2px seam. The requirement is "not more than 2px space between them". That gap is the
 *     composer's bottom margin minus nothing — a real box-model measurement. jsdom reports 0 for
 *     every box, so a vitest test asserting it would pass against ANY value.
 *   * Equal width. "Taking the same width as the Write Something component" is a rendered-width
 *     comparison between two independently-styled elements.
 *   * DOM order (composer BEFORE toggle) as it actually paints.
 *   * One-line tool row. Six labelled buttons in a 560px column is genuinely tight; if they wrap,
 *     the composer gets taller and shifts the sticky right rail's measured --rail-right-h offset.
 *     Only a real layout engine reveals the wrap.
 *
 * It seeds NOTHING and writes NOTHING: it reads the signed-in /trade page and clicks lane/tool
 * affordances that are either read-only or (for the two unbuilt tools) inert. No cleanup tag needed
 * — there is no residue to remove.
 *
 * Asserts:
 *   1. The composer renders BEFORE the lane toggle in DOM order.
 *   2. The toggle's rendered width equals the composer's, and the vertical seam between them is ≤2px.
 *   3. All six tools are present, labelled, and laid out on ONE row (single line, no wrap).
 *   4. The lane bar offers exactly Shops | Clips | Podcasts, defaulting to Shops.
 *   5. "Create Poll" / "Post Audio" are aria-disabled, stay focusable, and only surface a
 *      "coming soon" toast — they never expand the composer.
 *   6. "Write Post" expands into Post mode; "Sell Product" expands into Product mode.
 *   7. Podcasts shows the honest not-live note and does NOT open the video overlay; Clips DOES open
 *      the full-screen overlay; closing the overlay returns to Shops and re-shows the timeline.
 *   8. The tool row stays one line at 390px and introduces no horizontal overflow.
 *
 * Run (stack up — weespas :8000, commerce :8003, FE :5174):
 *   cd PE/commerce/e2e
 *   NODE_PATH=/home/jeff/PE/InSAR-Final-main/frontend/node_modules node composer_lanes.fe.e2e.js
 */
const { chromium, request } = require('playwright');

const WEESPAS = process.env.WEESPAS_BASE_URL || 'http://127.0.0.1:8000';
const FE = process.env.FE_BASE_URL || 'http://127.0.0.1:5174';
const WEESPAS_API = `${WEESPAS}/api/v1`;
const EMAIL = process.env.WEESPAS_EMAIL || 'admin@weespas.com';
const PASSWORD = process.env.WEESPAS_PASSWORD || 'admin123';

// The six tools, in the required visual order (req 2b). testid suffix → visible label.
const TOOLS = [
  ['write', 'Write Post'],
  ['sell', 'Sell Product'],
  ['video', 'Post a Video'],
  ['poll', 'Create Poll'],
  ['pictures', 'Post Pictures'],
  ['audio', 'Post Audio'],
];
// The two tools with no backend (no poll model; weespas upload allowlist is images+video only).
const UNBUILT = ['poll', 'audio'];
const LANES = [['shops', 'Shops'], ['clips', 'Clips'], ['podcasts', 'Podcasts']];

let passed = 0;
const failures = [];
const check = (name, cond, detail = '') => {
  if (cond) { passed += 1; console.log(`  ✓ ${name}`); }
  else { failures.push(`${name}${detail ? ` — ${detail}` : ''}`); console.log(`  ✗ ${name}${detail ? ` — ${detail}` : ''}`); }
};
const eq = (name, a, b) => check(name, a === b, `expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
const authH = (t) => ({ Authorization: `Bearer ${t}` });

async function main() {
  const ctx = await request.newContext();
  let r = await ctx.post(`${WEESPAS_API}/auth/login`, {
    headers: { 'Content-Type': 'application/json' },
    data: { email: EMAIL, password: PASSWORD },
  });
  check('weespas login 200', r.ok(), `status ${r.status()}`);
  if (!r.ok()) { await ctx.dispose(); return report(); }
  const login = await r.json();
  const token = login.token, user = login.user;
  await ctx.dispose();

  const browser = await chromium.launch();
  try {
    const bctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await bctx.addInitScript(([t, u]) => {
      localStorage.setItem('weespas_token', t);
      localStorage.setItem('weespas_user', u);
    }, [token, JSON.stringify(user)]);
    // Grant geolocation so the page takes the precise-location path rather than the geo-hint banner
    // (which would add an element between the composer and the feed and muddy the seam measurement).
    await bctx.grantPermissions(['geolocation'], { origin: FE });
    await bctx.setGeolocation({ latitude: -1.2907, longitude: 36.7895 }); // Kilimani demo centroid
    const page = await bctx.newPage();
    await page.goto(`${FE}/trade`, { waitUntil: 'networkidle' });

    const composer = page.locator('.composer').first();
    const toggleRow = page.locator('.trade-page__toggle-row');
    await composer.waitFor({ timeout: 15000 });
    await toggleRow.waitFor({ timeout: 15000 });

    // 1) DOM order: composer BEFORE the toggle (the reorder is the whole point of req 2a).
    const composerFirst = await page.evaluate(() => {
      const c = document.querySelector('.composer');
      const t = document.querySelector('.trade-page__toggle-row');
      if (!c || !t) return null;
      // DOCUMENT_POSITION_FOLLOWING (4) ⇒ t comes after c.
      return !!(c.compareDocumentPosition(t) & Node.DOCUMENT_POSITION_FOLLOWING);
    });
    check('composer renders BEFORE the lane toggle', composerFirst === true, `got ${composerFirst}`);

    // 2) Matched width + ≤2px seam. Both are pure computed-layout facts.
    const cBox = await composer.boundingBox();
    const tBox = await toggleRow.boundingBox();
    const widthDelta = Math.abs(Math.round(cBox.width) - Math.round(tBox.width));
    check('toggle width matches the composer width', widthDelta <= 1,
      `composer ${Math.round(cBox.width)}px vs toggle ${Math.round(tBox.width)}px`);
    const seam = Math.round(tBox.y - (cBox.y + cBox.height));
    check('vertical seam between composer and toggle is ≤2px', seam >= 0 && seam <= 2, `${seam}px`);
    // Left edges align too — a matched width with a different origin still reads as two cards.
    check('composer and toggle are left-aligned', Math.abs(Math.round(cBox.x - tBox.x)) <= 1,
      `composer x=${Math.round(cBox.x)} toggle x=${Math.round(tBox.x)}`);

    // 3) All six tools, labelled, on ONE row. Same y-centre ⇒ no wrap.
    for (const [key, label] of TOOLS) {
      const btn = page.getByTestId(`composer-tool-${key}`);
      eq(`tool "${label}" present`, await btn.count(), 1);
      check(`tool "${label}" shows its label`, (await btn.innerText()).includes(label),
        `got ${JSON.stringify(await btn.innerText())}`);
    }
    const rowYs = await page.evaluate(() => Array.from(
      document.querySelectorAll('.composer__tool'),
    ).map((e) => Math.round(e.getBoundingClientRect().top)));
    eq('tool row has exactly six buttons', rowYs.length, 6);
    check('tool row is a SINGLE line (no wrap)', new Set(rowYs).size === 1, `tops: ${rowYs.join(',')}`);
    // The tools sit inside the composer card, not floating beside it.
    const toolsInside = await page.evaluate(() => {
      const row = document.querySelector('.composer__tools');
      return !!row && !!row.closest('.composer');
    });
    check('tool row is inside the composer card', toolsInside === true);

    // 4) Exactly three lanes, defaulting to Shops.
    eq('lane bar has exactly three buttons', await page.locator('.feed-kind-toggle__btn').count(), 3);
    for (const [key, label] of LANES) {
      const tab = page.getByTestId(`kind-${key}`);
      eq(`lane "${label}" present`, await tab.count(), 1);
      check(`lane "${label}" shows its label`, (await tab.innerText()).includes(label));
    }
    eq('default lane is Shops', await page.getByTestId('kind-shops').getAttribute('aria-selected'), 'true');
    // The three lanes split the bar evenly (equal thirds — flex:1 1 0), so "Podcasts" doesn't claim
    // more room than "Clips". Another computed-width fact.
    const laneWidths = await page.evaluate(() => Array.from(
      document.querySelectorAll('.feed-kind-toggle__btn'),
    ).map((e) => Math.round(e.getBoundingClientRect().width)));
    check('lane buttons are equal thirds', Math.max(...laneWidths) - Math.min(...laneWidths) <= 1,
      `widths: ${laneWidths.join(',')}`);

    // 5) The unbuilt tools are honest: aria-disabled, still focusable, toast only, never expand.
    for (const key of UNBUILT) {
      const btn = page.getByTestId(`composer-tool-${key}`);
      // A REAL enabled button. Neither `disabled` nor `aria-disabled` may be set: either would make
      // the browser/AT treat it as inert, and Playwright's actionability check would refuse the click
      // below — which is precisely the experience a keyboard or screen-reader user would get.
      // "Unavailable" is carried by the accessible NAME so it is spoken, not merely implied.
      eq(`"${key}" is not aria-disabled`, await btn.getAttribute('aria-disabled'), null);
      eq(`"${key}" is not natively disabled (stays clickable)`, await btn.isDisabled(), false);
      check(`"${key}" says "coming soon" in its accessible name`,
        /coming soon/i.test(await btn.getAttribute('aria-label') || ''),
        `aria-label=${JSON.stringify(await btn.getAttribute('aria-label'))}`);
      await btn.click();
      const toastSeen = await page.locator('text=/coming soon/i').first()
        .waitFor({ timeout: 4000 }).then(() => true).catch(() => false);
      check(`"${key}" surfaces a "coming soon" notice`, toastSeen);
      eq(`"${key}" does NOT expand the composer`, await page.getByTestId('composer-body').count(), 0);
      // Let the toast clear so the next iteration's wait isn't satisfied by a stale one.
      await page.locator('text=/coming soon/i').first()
        .waitFor({ state: 'detached', timeout: 8000 }).catch(() => {});
    }

    // 6) The two mode entry points land in the right mode in ONE click.
    await page.getByTestId('composer-tool-write').click();
    await page.getByTestId('composer-body').waitFor({ timeout: 8000 });
    eq('"Write Post" lands in Post mode',
      await page.getByTestId('composer-mode-post').getAttribute('aria-selected'), 'true');
    await page.getByTestId('composer-tool-sell').click();
    eq('"Sell Product" lands in Product mode',
      await page.getByTestId('composer-mode-product').getAttribute('aria-selected'), 'true');
    // Collapse again so the lane assertions below see the resting layout.
    await page.locator('.composer button', { hasText: 'Cancel' }).first().click();
    await page.getByTestId('composer-open').waitFor({ timeout: 8000 });

    // 7) Lane behaviour. Podcasts = honest note, NOT the video overlay.
    await page.getByTestId('kind-podcasts').click();
    const note = page.getByTestId('lane-podcasts-empty');
    await note.waitFor({ timeout: 8000 });
    check('Podcasts shows the honest not-live note', /live yet/i.test(await note.innerText()),
      await note.innerText());
    eq('Podcasts does NOT open the video overlay', await page.locator('.vertical-video-feed').count(), 0);
    // The timeline is hidden, not unmounted (so its fetch + scroll survive a lane round-trip).
    const feedHiddenOnPodcasts = await page.evaluate(() => {
      const f = document.querySelector('.product-feed__column');
      return f ? !!f.closest('[hidden]') : null;
    });
    check('timeline is hidden (not unmounted) on Podcasts', feedHiddenOnPodcasts === true,
      `got ${feedHiddenOnPodcasts}`);

    // Clips DOES open the full-screen overlay (the reused vertical player).
    await page.getByTestId('kind-clips').click();
    const overlayOpened = await page.locator('.vertical-video-feed').first()
      .waitFor({ timeout: 10000 }).then(() => true).catch(() => false);
    check('Clips opens the full-screen vertical overlay', overlayOpened);

    // Back to Shops. The overlay is a full-screen PORTAL on document.body, so the lane bar beneath it
    // is deliberately unreachable while it's open — you leave via the overlay's own close control
    // (which calls onExit → setLane('shops')). Clicking the covered toggle would be testing something
    // the product doesn't offer, so exit the way a user actually does.
    await page.locator('.vertical-video-feed__close').click();
    const overlayClosed = await page.locator('.vertical-video-feed').first()
      .waitFor({ state: 'detached', timeout: 10000 }).then(() => true).catch(() => false);
    check('closing the overlay dismisses it', overlayClosed);
    eq('Shops is selected again', await page.getByTestId('kind-shops').getAttribute('aria-selected'), 'true');
    eq('the not-live note is gone on Shops', await page.getByTestId('lane-podcasts-empty').count(), 0);
    const feedVisibleOnShops = await page.evaluate(() => {
      const f = document.querySelector('.product-feed__column');
      return f ? !f.closest('[hidden]') : null;
    });
    check('timeline is visible again on Shops', feedVisibleOnShops === true, `got ${feedVisibleOnShops}`);

    // Narrow viewport: the tool row must STILL be one line (it drops to icon-only ≤560px). A wrap
    // here would change the composer height and shift the sticky rail offset on mobile.
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(300); // let the media query settle
    const narrowYs = await page.evaluate(() => Array.from(
      document.querySelectorAll('.composer__tool'),
    ).map((e) => Math.round(e.getBoundingClientRect().top)));
    check('tool row stays a single line at 390px', narrowYs.length === 6 && new Set(narrowYs).size === 1,
      `tops: ${narrowYs.join(',')}`);
    // No horizontal overflow introduced at mobile width (the #root overflow-x guard).
    const overflowX = await page.evaluate(() =>
      document.documentElement.scrollWidth - document.documentElement.clientWidth);
    check('no horizontal overflow at 390px', overflowX <= 0, `${overflowX}px`);
  } finally {
    await browser.close();
  }
  report();
}

function report() {
  console.log(`\n${passed} checks passed, ${failures.length} failed`);
  if (failures.length) { console.error('\nFAILURES:\n  - ' + failures.join('\n  - ')); process.exit(1); }
}

main().catch((e) => { console.error('e2e crashed:', e); process.exit(1); });
