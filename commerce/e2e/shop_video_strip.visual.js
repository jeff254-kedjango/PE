/**
 * Live UI proof for the §8 Trade short-video surface — the genuine REUSE of the weespas shorts UI.
 *
 * Drives a real Chromium against the live stack and asserts the behaviour the owner asked for:
 *   1. The right-rail strip is the REUSED <ShortsShelf> (`.shorts-shelf.shop-video-strip`), showing
 *      SEVERAL 9:16 tiles at once (not one full-width screen), with the chevron nav bottom-right.
 *   2. NO card chrome around it (the bare variant — no .shorts-shelf__header, the strip is flush).
 *   3. Clicking a tile OPENS the REUSED full-screen vertical feed (`.vertical-video-feed`, the same
 *      component the home page uses) jumped to that video, with a <video> element mounted + playing.
 *   4. The close button dismisses the feed.
 *
 * Relies on the seeded playable clips (PE/commerce/scripts/seed_trade_videos.py) near the CBD default
 * centre. Screenshots both states for a human eyeball.
 *
 * Run (stack up — weespas :8000, commerce :8003, FE :5174):
 *   cd PE/commerce/e2e
 *   NODE_PATH=/home/jeff/PE/InSAR-Final-main/frontend/node_modules \
 *     node shop_video_strip.visual.js
 *   → writes /tmp/shop-video-strip.png + /tmp/shop-video-feed.png
 */
const { chromium, request } = require('playwright');

const WEESPAS = process.env.WEESPAS_BASE_URL || 'http://127.0.0.1:8000';
const FE = process.env.FE_BASE_URL || 'http://127.0.0.1:5174';
const WEESPAS_API = `${WEESPAS}/api/v1`;
const EMAIL = process.env.WEESPAS_EMAIL || 'admin@weespas.com';
const PASSWORD = process.env.WEESPAS_PASSWORD || 'admin123';
const SHELF_OUT = process.env.SHELF_OUT || '/tmp/shop-video-strip.png';
const FEED_OUT = process.env.FEED_OUT || '/tmp/shop-video-feed.png';

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
    const bctx = await browser.newContext({
      viewport: { width: 1440, height: 900 }, // ≥1101px so the right rail shows
      permissions: [],                        // geolocation denied → Nairobi CBD default (seeded)
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
    console.log(`  trade url: ${page.url()}`);

    // 1) The REUSED shelf renders as the bare strip variant.
    const shelf = page.locator('.shorts-shelf.shop-video-strip');
    await shelf.waitFor({ timeout: 15000 }).catch(() => {
      console.error('WARN: shop-video-strip not found — screenshotting for diagnosis');
    });
    const shelfPresent = await shelf.count() > 0;
    check('reused ShortsShelf renders as the .shop-video-strip variant', shelfPresent);

    // 2) Several tiles at once (not one screen). The strip is 3-up; seeded data has ≥3 near CBD.
    const tiles = page.locator('.shorts-shelf.shop-video-strip .short-card');
    const tileCount = await tiles.count();
    check('shelf shows MULTIPLE tiles (≥3, a row not one screen)', tileCount >= 3, `got ${tileCount}`);

    // 3) NO card chrome: the header (eyebrow + See-all) is suppressed.
    const headerCount = await page.locator('.shorts-shelf.shop-video-strip .shorts-shelf__header').count();
    check('no card chrome — shelf header suppressed (bare strip)', headerCount === 0, `headers=${headerCount}`);

    // 4) Bottom-right chevron nav present (from the reused shelf).
    const navBtns = await page.locator('.shorts-shelf.shop-video-strip .shorts-shelf__nav-btn').count();
    check('bottom-right chevron nav present', navBtns === 2, `nav buttons=${navBtns}`);

    await page.waitForTimeout(800);
    await page.screenshot({ path: SHELF_OUT, fullPage: false });
    console.log(`shelf screenshot → ${SHELF_OUT}`);

    // 5) Click the first tile → the REUSED full-screen vertical feed opens.
    if (tileCount > 0) {
      await tiles.first().click();
      const feed = page.locator('.vertical-video-feed');
      await feed.waitFor({ timeout: 8000 }).catch(() => {});
      check('clicking a tile opens the reused VerticalVideoFeed (full-screen)', await feed.count() > 0);

      // A <video> is mounted inside the feed (the vertical player), and it's the full-screen variant
      // (NOT the embedded one).
      const videoCount = await page.locator('.vertical-video-feed .short-item__video').count();
      check('vertical feed mounts a <video> element', videoCount > 0, `videos=${videoCount}`);
      const embedded = await page.locator('.vertical-video-feed--embedded').count();
      check('feed is the full-screen overlay (not embedded)', embedded === 0);

      // The active video is actually playing (autoplay muted). POLL to a deadline rather than a
      // single fixed-wait probe: muted-autoplay start time varies with decode + load, so a one-shot
      // check at a fixed 1200ms flaked (~playback occasionally not yet advanced). Poll every 200ms
      // up to 5s and pass as soon as it's advancing — deterministic, no slower on the happy path.
      let playing = false;
      for (let i = 0; i < 25 && !playing; i += 1) {
        playing = await page.evaluate(() => {
          const v = document.querySelector('.vertical-video-feed .short-item__video');
          return !!v && !v.paused && v.currentTime > 0;
        });
        if (!playing) await page.waitForTimeout(200);
      }
      check('active video is playing', playing);

      await page.screenshot({ path: FEED_OUT, fullPage: false });
      console.log(`feed screenshot → ${FEED_OUT}`);

      // 6) Close it.
      await page.locator('.vertical-video-feed__close').click();
      await page.waitForTimeout(400);
      check('close button dismisses the feed', await page.locator('.vertical-video-feed').count() === 0);
    }
  } finally {
    await browser.close();
  }

  console.log(`\n${passed} checks passed, ${failures.length} failed`);
  if (failures.length) { console.error('\nFAILURES:\n  - ' + failures.join('\n  - ')); process.exit(1); }
}

main().catch((e) => { console.error('visual crashed:', e); process.exit(1); });
