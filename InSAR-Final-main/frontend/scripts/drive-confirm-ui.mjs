// Drive the REAL Weespas app in headless Chromium to verify the "bad pin" tap-to-confirm
// flow — the part vitest can't reach because it stubs Leaflet entirely. We:
//   1. seed an OWNER session into localStorage (token minted out-of-band, see the runner),
//   2. open the one live needs_confirmation listing,
//   3. open the "Confirm your building" modal,
//   4. assert the Leaflet 2.5D-prism map actually painted (SVG prism polygons present),
//   5. tap a candidate and assert selection + that "Confirm" enables,
//   6. screenshot + dump console/page errors.
//
// SAFETY: we deliberately STOP before clicking the final "Confirm this building" button.
// That POST writes an authoritative link and flips the listing monitored — it would destroy
// the only needs_confirmation fixture and mutate the live `commercial` DB. Read-only verify.
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = process.env.WEESPAS_URL ?? "http://localhost:5173";
const LISTING_ID = process.env.LISTING_ID;
const TOKEN = process.env.OWNER_TOKEN;
const USER_JSON = process.env.OWNER_USER; // the /auth/me payload, verbatim
const OUT_DIR = "/tmp/confirm-ui-screens";
mkdirSync(OUT_DIR, { recursive: true });

if (!LISTING_ID || !TOKEN || !USER_JSON) {
  console.error("missing env: LISTING_ID, OWNER_TOKEN, OWNER_USER are all required");
  process.exit(2);
}

const fail = (msg) => { console.error(`\n❌ ${msg}`); failures.push(msg); };
const ok = (msg) => console.log(`✓ ${msg}`);
const failures = [];

// Use the full chromium already cached (chromium-1223) rather than the headless-shell
// variant, so we don't depend on a second download. PLAYWRIGHT_CHROMIUM_PATH overrides.
const CHROME = process.env.PLAYWRIGHT_CHROMIUM_PATH
  ?? `${process.env.HOME}/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome`;
const browser = await chromium.launch({
  executablePath: CHROME,
  args: ["--use-gl=swiftshader", "--enable-webgl", "--ignore-gpu-blocklist"],
});
const ctx = await browser.newContext({ viewport: { width: 900, height: 1200 } });
const page = await ctx.newPage();

const logs = [];
page.on("console", (m) => logs.push(`[${m.type()}] ${m.text()}`));
page.on("pageerror", (e) => logs.push(`[pageerror] ${e.message}`));
page.on("requestfailed", (r) =>
  logs.push(`[requestfailed] ${r.url()} ${r.failure()?.errorText}`));

// Guard rail: if anything ever fires the confirm POST, we want to know loudly.
let confirmPosted = false;
page.on("request", (r) => {
  if (r.method() === "POST" && r.url().includes(`/listing/${LISTING_ID}/confirm`)) {
    confirmPosted = true;
  }
});

try {
  // 1) Seed the owner session BEFORE the app boots, so AuthContext restores it on mount.
  await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded", timeout: 20000 });
  await page.evaluate(([t, u]) => {
    localStorage.setItem("weespas_token", t);
    localStorage.setItem("weespas_user", u);
  }, [TOKEN, USER_JSON]);

  // 2) Deep-link straight into the confirm flow (?confirm=1 is the inbox notification path).
  await page.goto(`${BASE}/properties/${LISTING_ID}?confirm=1`, {
    waitUntil: "networkidle", timeout: 20000,
  });
  await page.waitForTimeout(2500); // let the risk query resolve + modal auto-open + map mount

  // 3) The modal should have auto-opened via the ?confirm=1 deep-link.
  const modal = page.locator('[aria-label="Confirm your building"]');
  if (await modal.count()) ok("confirm modal opened (deep-link ?confirm=1)");
  else {
    // Fallback: click the CTA if the auto-open didn't fire.
    const cta = page.locator(".pd-confirm-cta");
    if (await cta.count()) { await cta.first().click(); await page.waitForTimeout(1500);
      ok("confirm modal opened (CTA fallback)"); }
    else fail("confirm modal did not open (no deep-link modal, no CTA)");
  }

  // 4) Did the Leaflet 2.5D map actually paint? Count the SVG polygons (prism faces) and
  //    the tappable option rows. A stubbed/blank map would have zero polygons.
  const paint = await page.evaluate(() => {
    const mapEl = document.querySelector(".bcm__map");
    const polys = document.querySelectorAll(".bcm__map svg path").length;
    const options = document.querySelectorAll(".bcm__option").length;
    const tiles = document.querySelectorAll(".bcm__map img.leaflet-tile, .bcm__map .leaflet-tile").length;
    const r = mapEl?.getBoundingClientRect();
    return { hasMap: !!mapEl, mapW: r?.width ?? 0, mapH: r?.height ?? 0, polys, options, tiles };
  });
  console.log("paint:", JSON.stringify(paint));
  if (paint.hasMap && paint.mapW > 0 && paint.mapH > 0) ok(`map container painted ${paint.mapW}x${paint.mapH}`);
  else fail("map container missing or zero-size");
  if (paint.polys > 0) ok(`${paint.polys} prism polygon faces drawn`);
  else fail("no prism polygons drawn — map did not render footprints");
  if (paint.options >= 1) ok(`${paint.options} tappable candidate option(s)`);
  else fail("no candidate option rows");

  await page.screenshot({ path: `${OUT_DIR}/01-modal-open.png` });

  // 5) Tap the first candidate; assert it becomes selected and Confirm enables.
  const firstOption = page.locator(".bcm__option").first();
  if (await firstOption.count()) {
    await firstOption.click();
    await page.waitForTimeout(600);
    const pressed = await firstOption.getAttribute("aria-pressed");
    if (pressed === "true") ok("tapping a candidate marks it selected (aria-pressed)");
    else fail(`selected option aria-pressed=${pressed}, expected true`);

    const confirmBtn = page.locator(".bcm__confirm");
    const disabled = await confirmBtn.isDisabled();
    if (!disabled) ok('"Confirm this building" enabled after selection');
    else fail("confirm button still disabled after selecting a candidate");
    await page.screenshot({ path: `${OUT_DIR}/02-selected.png` });
  } else {
    fail("no candidate option to tap");
  }

  // 6) HARD STOP: do NOT click confirm. Assert we never fired the mutation.
  if (confirmPosted) fail("a confirm POST fired — fixture may have been mutated!");
  else ok("no confirm POST fired (fixture preserved)");
} catch (e) {
  fail(`exception: ${e.message}`);
} finally {
  // Surface only error/warn console lines (signal over noise).
  const errs = logs.filter((l) => l.startsWith("[error]") || l.startsWith("[pageerror]") || l.startsWith("[requestfailed]"));
  console.log(`\n--- console errors (${errs.length}) ---`);
  for (const l of errs.slice(-40)) console.log(l);
  console.log(`\nscreenshots → ${OUT_DIR}/`);
  await browser.close();
  console.log(`\n${failures.length ? "❌ FAIL" : "✅ PASS"} — ${failures.length} failure(s)`);
  process.exit(failures.length ? 1 : 0);
}
