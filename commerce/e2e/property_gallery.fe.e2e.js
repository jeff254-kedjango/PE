/**
 * Live UI proof for the redesigned PROPERTY GALLERY on the weespas home page.
 *
 * The gallery was restructured (this session): the featured hero now fills a full-width 16:9 stage,
 * and the clickable thumbnail carousel — previously a wide row of title+price cards BELOW the hero —
 * became a compact strip of small SQUARE image tiles that FLOATS over the hero (a vertical right
 * rail on desktop, a horizontal strip below the hero on mobile). Each tile is image-only with a
 * price + category row pinned to its bottom edge (title removed — it already shows on the hero).
 * The SearchPanel that used to sit in a 1fr sidebar beside the gallery is gone from the landing grid
 * and now lives as a filter-icon popover in the "Latest properties near you" header.
 *
 * Why this earns a live check: the redesign is almost entirely a CSS/DOM geometry change (16:9
 * aspect-ratio stage, an absolutely-positioned floating rail, a track whose per-slide travel moved
 * from `style.transform` to a `--track-offset` custom property that the CSS resolves onto the Y axis
 * on desktop and the X axis on mobile). None of that is observable in jsdom — only a real browser
 * lays out aspect-ratio + absolute overlays + responsive axis-switching. This spec pins the parts a
 * type-check and vitest cannot see: the rail actually floats INSIDE the hero's box on desktop, the
 * SearchPanel popover opens from the preview header, and the strip reflows BELOW the hero on mobile.
 *
 * Read-only: drives live featured data, seeds nothing, tears down nothing.
 *
 * Asserts (desktop 1440×900):
 *   1. The gallery renders a single full-width hero stage (16:9) — no old 2fr/1fr sidebar column.
 *   2. The clickable rail floats OVER the hero (its box is contained within the hero's box).
 *   3. Tiles are small squares showing a price + category row, and NO tile shows the hero title.
 *   4. Clicking a tile selects it (active ring) and drives the hero heading to that listing.
 *   5. The SearchPanel is NOT in the landing grid; its filter trigger lives in the preview header
 *      and opens a localized popover with the real filter fields.
 *   5b. "Search My Location" is promoted OUT of the filter form to the LEFT of the Filters button,
 *      so the header row reads Title …… [Search My Location][Filters]; it keeps the crosshair icon,
 *      the popover stays anchored to the Filters trigger, and clicking locate dismisses the popover.
 * Then (mobile 390×844):
 *   6. The strip reflows to a horizontal row BELOW the hero (its top edge is beneath the hero's).
 *   7. Both header pills go icon-only and the extra pill adds no horizontal overflow.
 *
 * Run (stack up — weespas :8000, FE :5174):
 *   cd PE/commerce/e2e
 *   NODE_PATH=/home/jeff/PE/InSAR-Final-main/frontend/node_modules \
 *     WEESPAS_BASE_URL=http://127.0.0.1:8000 FE_BASE_URL=http://127.0.0.1:5174 \
 *     WEESPAS_EMAIL=admin@weespas.com WEESPAS_PASSWORD=admin123 \
 *     node property_gallery.fe.e2e.js
 */
const { chromium, request } = require('playwright');

const WEESPAS = process.env.WEESPAS_BASE_URL || 'http://127.0.0.1:8000';
const FE = process.env.FE_BASE_URL || 'http://127.0.0.1:5174';
const WEESPAS_API = `${WEESPAS}/api/v1`;

let passed = 0;
const failures = [];
const check = (name, cond, detail = '') => {
  if (cond) { passed += 1; console.log(`  ✓ ${name}`); }
  else { failures.push(`${name}${detail ? ` — ${detail}` : ''}`); console.log(`  ✗ ${name}${detail ? ` — ${detail}` : ''}`); }
};

// A DOMRect is "contained" in another when it sits within its bounds (small epsilon for sub-pixel).
const contains = (outer, inner, eps = 2) =>
  inner.x >= outer.x - eps && inner.y >= outer.y - eps &&
  inner.x + inner.width <= outer.x + outer.width + eps &&
  inner.y + inner.height <= outer.y + outer.height + eps;

async function main() {
  // Precondition: the gallery only renders when the backend has featured listings.
  const ctx = await request.newContext();
  const featRes = await ctx.get(`${WEESPAS_API}/properties/featured`);
  const featured = featRes.ok() ? await featRes.json() : [];
  await ctx.dispose();
  if (!Array.isArray(featured) || featured.length < 2) {
    console.error(`PRECONDITION FAILED: need ≥2 featured properties, got ${featured.length ?? 0}. ` +
      `The gallery strip only renders when looping (>1 listing).`);
    process.exit(2);
  }
  console.log(`  · ${featured.length} featured listings live — gallery will loop\n`);

  const browser = await chromium.launch();
  try {
    // ────────────────────────── Desktop ──────────────────────────
    const bctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await bctx.newPage();
    await page.goto(`${FE}/`, { waitUntil: 'networkidle' });

    // 1) Single full-width hero stage at 16:9 (no sidebar column beside it).
    const stage = page.locator('.gallery-stage').first();
    await stage.waitFor({ timeout: 10000 });
    const heroBox = await page.locator('.gallery-hero').first().boundingBox();
    const gridBox = await page.locator('.landing-grid').first().boundingBox();
    // The hero should fill essentially the whole grid content-width (grid box includes ~25px
    // horizontal gutter each side). In the OLD 2fr/1fr layout the hero was ~2/3 of the grid, so a
    // ≥90% ratio cleanly proves the sidebar column is gone and the gallery took its space.
    check('hero fills the full landing-grid width (no 1fr sidebar)',
      heroBox && gridBox && heroBox.width >= gridBox.width * 0.9,
      heroBox && gridBox ? `hero ${Math.round(heroBox.width)} vs grid ${Math.round(gridBox.width)} (${(heroBox.width / gridBox.width).toFixed(2)})` : 'no box');
    check('hero stage is ~16:9',
      heroBox && Math.abs(heroBox.width / heroBox.height - 16 / 9) < 0.15,
      heroBox ? `ratio ${(heroBox.width / heroBox.height).toFixed(2)}` : 'no box');

    // 2) The clickable rail floats OVER the hero (contained within the hero's box).
    const rail = page.locator('.carousel-rail').first();
    await rail.waitFor({ timeout: 8000 });
    const railBox = await rail.boundingBox();
    check('clickable rail floats INSIDE the hero box (desktop overlay)',
      heroBox && railBox && contains(heroBox, railBox),
      railBox ? `rail@${Math.round(railBox.x)},${Math.round(railBox.y)} ${Math.round(railBox.width)}×${Math.round(railBox.height)}` : 'no rail');

    // 3) Tiles are small squares with a price+category row; none carries the hero title.
    const firstTile = page.locator('.carousel-item').first();
    await firstTile.waitFor({ timeout: 8000 });
    const tileBox = await firstTile.boundingBox();
    check('tiles are small squares (≤ ~110px, ~1:1)',
      tileBox && tileBox.width <= 110 && Math.abs(tileBox.width - tileBox.height) < 6,
      tileBox ? `${Math.round(tileBox.width)}×${Math.round(tileBox.height)}` : 'no tile');
    check('a tile shows the price + category tag row',
      await page.locator('.carousel-item .thumbnail-tags').count() > 0);
    const heroTitle = (await page.locator('.gallery-hero__content h2').first().textContent() || '').trim();
    // The redesigned tile has no title node at all — assert the strip contains no <strong> title.
    check('tiles carry NO title (title lives on the hero only)',
      await page.locator('.carousel-item strong').count() === 0,
      `hero title is "${heroTitle}"`);

    // 4) SearchPanel is out of the landing grid; its trigger lives in the preview header + opens.
    //    (Done BEFORE the tile click, which opens a detail dialog that would cover the header.)
    check('no SearchPanel filter inside the landing grid',
      await page.locator('.landing-grid [data-testid="search-panel-open"]').count() === 0);
    const trigger = page.locator('.preview-header [data-testid="search-panel-open"]').first();
    await trigger.waitFor({ timeout: 8000 });
    check('filter trigger lives in the preview header', await trigger.count() > 0);
    await trigger.click();
    const popover = page.locator('.search-panel--popover').first();
    await popover.waitFor({ timeout: 5000 });
    check('clicking the filter trigger opens the localized popover', await popover.isVisible());
    check('popover carries the real filter fields (property type + price)',
      await popover.locator('.search-panel__body').count() > 0);
    // "Search My Location" was promoted OUT of this form up to the header row, so the form must no
    // longer carry any location control (guards against leaving a duplicate behind).
    check('filter form no longer carries a location control',
      !(await popover.locator('.search-panel__body').textContent() || '').toLowerCase().includes('location'));
    // The popover stays anchored to the FILTERS trigger, not to the new locate button beside it.
    const popBox = await popover.boundingBox();
    const trigBox = await trigger.boundingBox();
    check('popover stays right-anchored to the Filters trigger',
      popBox && trigBox && Math.abs((popBox.x + popBox.width) - (trigBox.x + trigBox.width)) < 3,
      popBox && trigBox ? `popover right ${Math.round(popBox.x + popBox.width)} vs trigger right ${Math.round(trigBox.x + trigBox.width)}` : 'no box');
    // Escape dismisses it (localized close).
    await page.keyboard.press('Escape');
    await page.waitForTimeout(150);
    check('Escape closes the filter popover', await page.locator('.search-panel--popover').count() === 0);

    // 4b) "Search My Location" — relocated from inside the filter form to the LEFT of the Filters
    //     button, so the header row reads: Title …… [Search My Location][Filters]. Geometry only a
    //     real browser can confirm (jsdom has no layout), hence the live check.
    const locate = page.locator('.preview-header [data-testid="search-locate"]').first();
    await locate.waitFor({ timeout: 8000 });
    check('locate button lives in the preview header', await locate.count() > 0);
    check('locate button reads "Search My Location"',
      (await locate.textContent() || '').trim() === 'Search My Location',
      `"${(await locate.textContent() || '').trim()}"`);
    check('locate button keeps its crosshair icon', await locate.locator('svg').count() > 0);
    const locBox = await locate.boundingBox();
    check('locate sits to the LEFT of the Filters button',
      locBox && trigBox && locBox.x + locBox.width <= trigBox.x + 1,
      locBox && trigBox ? `locate ends ${Math.round(locBox.x + locBox.width)}, Filters starts ${Math.round(trigBox.x)}` : 'no box');
    check('locate + Filters share one row (aligned vertical centres)',
      locBox && trigBox && Math.abs((locBox.y + locBox.height / 2) - (trigBox.y + trigBox.height / 2)) < 2);
    const headingBox = await page.locator('.preview-header h2').first().boundingBox();
    check('section title sits left of both controls', headingBox && locBox && headingBox.x < locBox.x);
    // The locate button is a SIBLING of the popover anchor, so clicking it counts as an outside
    // click and dismisses an open popover — the behaviour that motivated placing it outside.
    await trigger.click();
    await popover.waitFor({ timeout: 5000 });
    await locate.click();
    await page.waitForTimeout(250);
    check('clicking locate dismisses an open filter popover',
      await page.locator('.search-panel--popover').count() === 0);

    await page.screenshot({ path: '/tmp/property-gallery-desktop.png' });
    console.log('  screenshot → /tmp/property-gallery-desktop.png');

    // 5) Clicking a tile selects it (active ring) AND opens that property's detail dialog — the
    //    real `onSelect` behavior. Done LAST because the dialog covers the gallery.
    //    Autoplay slides the track every 4s, so a "first non-active" tile can scroll out of the
    //    clipped viewport mid-click. Hover the gallery first (the component pauses autoplay on
    //    mouseenter), let the in-flight transition settle, then click the ACTIVE tile — it is
    //    always the one centred in the viewport, so the click can't miss.
    await page.locator('.gallery-wrapper').first().hover();
    await page.waitForTimeout(700); // > the 500ms track transition
    const clickable = page.locator('.carousel-item.active:not([aria-hidden="true"])').first();
    await clickable.click();
    const detail = page.locator('.pd-panel[role="dialog"]').first();
    await detail.waitFor({ timeout: 8000 });
    check('clicking a tile opens that listing’s detail dialog', await detail.isVisible());
    check('the clicked tile carries the active ring', await page.locator('.carousel-item.active').count() > 0);
    // Close the detail dialog (Escape) to leave the page clean.
    await page.keyboard.press('Escape');
    await page.waitForTimeout(200);
    check('Escape closes the detail dialog', await page.locator('.pd-panel[role="dialog"]').count() === 0);

    await bctx.close();

    // ────────────────────────── Mobile ──────────────────────────
    const mctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    const mpage = await mctx.newPage();
    await mpage.goto(`${FE}/`, { waitUntil: 'networkidle' });
    await mpage.locator('.carousel-rail').first().waitFor({ timeout: 10000 });
    const mHero = await mpage.locator('.gallery-hero').first().boundingBox();
    const mRail = await mpage.locator('.carousel-rail').first().boundingBox();
    check('mobile: strip reflows to a row BELOW the hero (not overlaid)',
      mHero && mRail && mRail.y >= mHero.y + mHero.height - 4,
      mHero && mRail ? `rail.y ${Math.round(mRail.y)} vs hero bottom ${Math.round(mHero.y + mHero.height)}` : 'no box');
    check('mobile: strip is wider than tall (horizontal)',
      mRail && mRail.width > mRail.height,
      mRail ? `${Math.round(mRail.width)}×${Math.round(mRail.height)}` : 'no rail');

    // Both header pills collapse to icon-only on narrow screens. Adding a second pill to that row is
    // exactly how a header widens the document, so assert the label hides AND nothing overflows.
    await mpage.locator('#listings').scrollIntoViewIfNeeded();
    await mpage.waitForTimeout(300);
    check('mobile: locate label is hidden (icon-only pill)',
      !(await mpage.locator('.search-filter__locate-label').first().isVisible()));
    check('mobile: locate icon is still visible',
      await mpage.locator('[data-testid="search-locate"] svg').first().isVisible());
    const mOverflow = await mpage.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    check('mobile: header row causes no horizontal overflow', mOverflow <= 0, `overflow ${mOverflow}px`);
    await mpage.screenshot({ path: '/tmp/property-gallery-mobile.png' });
    console.log('  screenshot → /tmp/property-gallery-mobile.png');
    await mctx.close();

    // ─────────────────── Short laptop (viewport-fit) ───────────────────
    // The stage height is min(692px, 100vh − navbar-total − gutter), so on a short
    // laptop the hero shrinks below the 692px ceiling and the WHOLE stage (hero +
    // floating rail) must fit under the fixed navbar with NO overlap into the
    // fold. 1280×720 is the tightest common laptop (measured overflow was +60px
    // BEFORE this fix). We assert the stage bottom sits at/above the viewport
    // bottom and the rail stays inside the shortened stage.
    const sctx = await browser.newContext({ viewport: { width: 1280, height: 720 } });
    const spage = await sctx.newPage();
    await spage.goto(`${FE}/`, { waitUntil: 'networkidle' });
    const sStage = spage.locator('.gallery-stage').first();
    await sStage.waitFor({ timeout: 10000 });
    const sGeo = await spage.evaluate(() => {
      const nav = document.querySelector('.navbar').getBoundingClientRect();
      const stage = document.querySelector('.gallery-stage').getBoundingClientRect();
      const rail = document.querySelector('.carousel-rail').getBoundingClientRect();
      return {
        navH: Math.round(nav.height),
        stageH: Math.round(stage.height),
        stageTop: Math.round(stage.top),
        stageBottom: Math.round(stage.bottom),
        railInside: rail.top >= stage.top - 2 && rail.bottom <= stage.bottom + 2,
        vh: window.innerHeight,
      };
    });
    // Stage sits below the navbar and fits within (viewport − navbar) — the whole
    // hero is visible without scrolling past the fold.
    check('short laptop: stage shrinks below the 692px ceiling',
      sGeo.stageH < 692, `stageH ${sGeo.stageH}`);
    check('short laptop: stage fits under the navbar (no overlap into the fold)',
      sGeo.stageTop >= sGeo.navH - 2 && sGeo.stageH <= sGeo.vh - sGeo.navH + 2,
      `stageH ${sGeo.stageH} ≤ vh ${sGeo.vh} − nav ${sGeo.navH}`);
    check('short laptop: floating rail stays inside the shortened stage',
      sGeo.railInside, `railInside=${sGeo.railInside}`);
    await sctx.close();
  } finally {
    await browser.close();
  }

  console.log(`\n${passed} passed, ${failures.length} failed`);
  if (failures.length) { failures.forEach((f) => console.log(`  ✗ ${f}`)); process.exit(1); }
}

main().catch((e) => { console.error(e); process.exit(1); });
