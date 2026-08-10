/**
 * Live UI proof for the crosshair brand mark + the SVG favicon.
 *
 * The owner added a crosshair SVG that closes the "weespas" wordmark (navbar + footer) and asked
 * for it to be viewport-compatible with the design loopholes closed. The loopholes the first cut
 * had — all of which are geometry, so jsdom/vitest CANNOT see them, hence a live spec:
 *   - The mark was squashed: an 18x18 SVG inside a 15px box rendered the circle as a 15x18
 *     ELLIPSE. A crosshair whose circle isn't round is the whole logo being wrong.
 *   - Two hand-tuned copies (navbar 15px vs footer 20px) against 24px and 48px wordmarks — two
 *     unrelated ratios that would drift apart on any future type change.
 *   - The footer wordmark was a fixed 48px inside a column that collapses to one at 768px, so it
 *     could overflow the narrowest viewports.
 *   - The favicon was committed as "favicon.png" while containing SVG source, and declared
 *     type="image/png" — a guaranteed broken icon.
 *
 * Asserts, at every breakpoint in VIEWS:
 *   1. The mark renders SQUARE (circle stays a circle) in navbar and footer.
 *   2. The SVG exactly fills its box (no dead space, no overflow) — proves `em` sizing works.
 *   3. The mark scales WITH its wordmark (ratio constant across sizes), never a fixed px.
 *   4. The brand never wraps and never widens the document (no horizontal overflow).
 *   5. The wordmark's accessible name stays "weespas" (decorative glyph not announced).
 *   6. The favicon is declared image/svg+xml, is served as SVG, and carries its dark-scheme rule;
 *      the old .png path serves no image.
 *
 * Read-only: seeds nothing, so there is nothing to tear down.
 *
 * Run (stack up — weespas :8000, FE :5174):
 *   cd PE/commerce/e2e
 *   NODE_PATH=/home/jeff/PE/InSAR-Final-main/frontend/node_modules node brand_mark.fe.e2e.js
 */
const { chromium } = require('playwright');

const FE = process.env.FE_BASE_URL || 'http://127.0.0.1:5174';

// Narrowest supported phone (320) through desktop. 768 is the footer-grid collapse breakpoint.
const VIEWS = [
  [1440, 900, 'desktop'],
  [1024, 768, 'tablet-l'],
  [768, 900, 'tablet'],
  [430, 932, 'mobile-l'],
  [375, 812, 'mobile'],
  [320, 640, 'mobile-xs'],
];

let passed = 0;
const failures = [];
const check = (name, cond, detail = '') => {
  if (cond) { passed += 1; console.log(`  ✓ ${name}`); }
  else { failures.push(`${name}${detail ? ` — ${detail}` : ''}`); console.log(`  ✗ ${name}${detail ? ` — ${detail}` : ''}`); }
};

// Sub-pixel layout means exact equality is the wrong test; 0.5px is tighter than any visible
// distortion while tolerating fractional rounding at fluid font sizes.
const EPS = 0.5;

/** Measure a wordmark + its .brand-mark child in the page. */
function measure(sel) {
  const el = document.querySelector(sel);
  if (!el) return null;
  const mark = el.querySelector('.brand-mark');
  const svg = mark && mark.querySelector('svg');
  if (!mark || !svg) return { missing: true };
  const mb = mark.getBoundingClientRect();
  const sb = svg.getBoundingClientRect();
  return {
    fontSize: parseFloat(getComputedStyle(el).fontSize),
    whiteSpace: getComputedStyle(el).whiteSpace,
    accessibleText: el.textContent.trim(),
    svgAriaHidden: svg.getAttribute('aria-hidden'),
    mark: { w: mb.width, h: mb.height },
    svg: { w: sb.width, h: sb.height },
  };
}

async function main() {
  const browser = await chromium.launch();
  console.log('── crosshair brand mark: viewport compatibility ──');

  // Ratios collected across breakpoints; the mark must track font-size, not be pinned in px.
  const navRatios = [];
  const footRatios = [];

  for (const [width, height, label] of VIEWS) {
    const ctx = await browser.newContext({ viewport: { width, height } });
    const page = await ctx.newPage();
    await page.goto(FE, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.navbar__logo .brand-mark svg', { timeout: 20000 });

    console.log(`\n  ${label} ${width}x${height}`);

    const nav = await page.evaluate(measure, '.navbar__logo');
    check(`${label}: navbar mark present`, nav && !nav.missing);
    if (nav && !nav.missing) {
      check(`${label}: navbar mark is SQUARE (circle stays circular)`,
        Math.abs(nav.mark.w - nav.mark.h) < EPS,
        `${nav.mark.w.toFixed(1)}x${nav.mark.h.toFixed(1)}`);
      check(`${label}: navbar SVG fills its box exactly`,
        Math.abs(nav.svg.w - nav.mark.w) < EPS && Math.abs(nav.svg.h - nav.mark.h) < EPS,
        `svg ${nav.svg.w.toFixed(1)}x${nav.svg.h.toFixed(1)} vs box ${nav.mark.w.toFixed(1)}x${nav.mark.h.toFixed(1)}`);
      check(`${label}: navbar brand does not wrap`, nav.whiteSpace === 'nowrap', nav.whiteSpace);
      check(`${label}: navbar accessible name is exactly "weespas"`,
        nav.accessibleText === 'weespas', JSON.stringify(nav.accessibleText));
      check(`${label}: navbar glyph is aria-hidden (decorative)`, nav.svgAriaHidden === 'true');
      navRatios.push(nav.mark.w / nav.fontSize);
    }

    // Footer needs scrolling into view before it lays out meaningfully.
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await page.waitForTimeout(400);
    const foot = await page.evaluate(measure, '.footer__logo');
    check(`${label}: footer mark present`, foot && !foot.missing);
    if (foot && !foot.missing) {
      check(`${label}: footer mark is SQUARE`,
        Math.abs(foot.mark.w - foot.mark.h) < EPS,
        `${foot.mark.w.toFixed(1)}x${foot.mark.h.toFixed(1)}`);
      check(`${label}: footer SVG fills its box exactly`,
        Math.abs(foot.svg.w - foot.mark.w) < EPS && Math.abs(foot.svg.h - foot.mark.h) < EPS,
        `svg ${foot.svg.w.toFixed(1)}x${foot.svg.h.toFixed(1)} vs box ${foot.mark.w.toFixed(1)}x${foot.mark.h.toFixed(1)}`);
      footRatios.push(foot.mark.w / foot.fontSize);
    }

    // The brand is the widest fixed-size thing in both bars; a mark that doesn't shrink is a
    // classic source of horizontal overflow on small screens.
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    check(`${label}: no horizontal document overflow`, overflow <= 0, `overflow ${overflow}px`);

    await ctx.close();
  }

  // The mark must be defined relative to type (em), so the ratio is constant everywhere. A fixed
  // px size would make this ratio drift as the fluid footer wordmark changes size.
  console.log('\n  scaling');
  const spread = (a) => Math.max(...a) - Math.min(...a);
  check('navbar mark/font ratio is constant across viewports (em-sized, not px)',
    navRatios.length === VIEWS.length && spread(navRatios) < 0.02,
    navRatios.map((r) => r.toFixed(3)).join(', '));
  check('footer mark/font ratio is constant across viewports (em-sized, not px)',
    footRatios.length === VIEWS.length && spread(footRatios) < 0.02,
    footRatios.map((r) => r.toFixed(3)).join(', '));
  // Both placements share ONE .brand-mark rule, so their ratios must agree with each other too.
  check('navbar and footer share the same mark/font ratio (one shared rule)',
    navRatios.length && footRatios.length && Math.abs(navRatios[0] - footRatios[0]) < 0.02,
    `nav ${navRatios[0]?.toFixed(3)} vs footer ${footRatios[0]?.toFixed(3)}`);
  // The footer wordmark is fluid: it must actually shrink between widest and narrowest.
  const ctxF = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const pW = await ctxF.newPage();
  await pW.goto(FE, { waitUntil: 'domcontentloaded' });
  await pW.waitForSelector('.footer__logo', { timeout: 20000 });
  const wideFont = await pW.evaluate(
    () => parseFloat(getComputedStyle(document.querySelector('.footer__logo')).fontSize));
  await pW.setViewportSize({ width: 320, height: 640 });
  await pW.waitForTimeout(250);
  const narrowFont = await pW.evaluate(
    () => parseFloat(getComputedStyle(document.querySelector('.footer__logo')).fontSize));
  check('footer wordmark scales down on narrow viewports (fluid, not fixed 48px)',
    narrowFont < wideFont, `${wideFont}px → ${narrowFont}px`);
  await ctxF.close();

  // ── Favicon ──
  console.log('\n  favicon');
  const fctx = await browser.newContext();
  const fpage = await fctx.newPage();
  await fpage.goto(FE, { waitUntil: 'domcontentloaded' });
  const link = await fpage.evaluate(() => {
    const l = document.querySelector('link[rel="icon"]');
    return l && { href: l.getAttribute('href'), type: l.getAttribute('type') };
  });
  check('favicon link points at /favicon.svg', link && link.href === '/favicon.svg', JSON.stringify(link));
  check('favicon is declared image/svg+xml (was mislabelled image/png)',
    link && link.type === 'image/svg+xml', link && link.type);

  const res = await fpage.request.get(`${FE}/favicon.svg`);
  const body = res.ok() ? await res.text() : '';
  check('GET /favicon.svg serves 200 as SVG', res.ok() && /image\/svg\+xml/.test(res.headers()['content-type'] || ''),
    `${res.status()} ${res.headers()['content-type']}`);
  check('favicon body is real SVG source', body.trimStart().startsWith('<svg'));
  check('favicon adapts to dark browser chrome (prefers-color-scheme rule)',
    body.includes('prefers-color-scheme'));
  // The old path must not resolve to an image. Vite's SPA fallback answers unknown paths with
  // index.html, so assert on CONTENT-TYPE rather than status (a 200 here means HTML, not an icon).
  const oldRes = await fpage.request.get(`${FE}/favicon.png`);
  const oldType = oldRes.headers()['content-type'] || '';
  check('old /favicon.png no longer serves an image', !/^image\//.test(oldType), `content-type ${oldType}`);
  await fctx.close();

  await browser.close();

  console.log(`\n${passed} passed, ${failures.length} failed`);
  if (failures.length) {
    console.log('\nFailures:');
    for (const f of failures) console.log(`  ✗ ${f}`);
    process.exit(1);
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
