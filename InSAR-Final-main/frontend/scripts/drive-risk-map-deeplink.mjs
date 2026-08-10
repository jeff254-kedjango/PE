// Drive the REAL InSAR app in headless Chromium to verify the "View Building Risk Analysis"
// deep-link UX: arriving from Weespas with ?aoi=&building=, the app must (1) select + fly to
// that building so its analysis renders in the sidebar, and (2) AUTO-SCROLL the sidebar so the
// horizontal divider directly above the "Structural Threat" section is at the top — bringing
// the building's risk analysis into view without the user scrolling.
//
// This is the part the (non-existent) InSAR unit suite can't cover: there is no test runner in
// this app and jsdom can't run MapLibre/deck. So we drive a real browser. READ-ONLY: the InSAR
// map mutates nothing; we only navigate + measure the DOM.
//
// The InSAR MAP UI is access-gated ("free, but login-required" — lib/access.ts): every visit
// must carry the telemetry-scoped ?wt= token Weespas mints, and the app verifies it server-side
// (GET /api/v1/insar/verify) before rendering. A tokenless visit is bounced to the Weespas
// login. So the drive mints a real telemetry token the same way the backend does and passes it
// as ?wt= — exactly what the "View Building Risk Analysis" deep-link carries in production.
// AOI + building id are a real pair from the huruma bundle.
//
// Mint with:
//   PYTHONPATH=/home/jeff /home/jeff/PE/weespas/.venv/bin/python -c \
//     "from PE.weespas.services.auth_service import create_insar_telemetry_token as m; \
//      print(m('drive-test-user','individual'))"
// and pass via WT=... env, or let this script mint it itself (default).
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import { execFileSync } from "node:child_process";

const BASE = process.env.INSAR_URL ?? "http://localhost:5174";
const AOI = process.env.AOI ?? "huruma";
const BUILDING = process.env.BUILDING ?? "100000"; // exists in the huruma bundle

// A valid telemetry-scoped token for the gate. Prefer an injected WT; otherwise mint one
// using the Weespas backend's own helper (single source of truth — no key duplication here).
function mintToken() {
  if (process.env.WT) return process.env.WT.trim();
  const out = execFileSync(
    "/home/jeff/PE/weespas/.venv/bin/python",
    ["-c", "from PE.weespas.services.auth_service import create_insar_telemetry_token as m; print(m('drive-test-user','individual'))"],
    // cwd = weespas dir so pydantic Settings() finds its .env (database_url/secret_key).
    { cwd: "/home/jeff/PE/weespas", env: { ...process.env, PYTHONPATH: "/home/jeff" }, encoding: "utf8" },
  );
  return out.trim().split("\n").pop().trim();
}
const WT = mintToken();
const OUT_DIR = "/tmp/risk-map-deeplink-screens";
mkdirSync(OUT_DIR, { recursive: true });

const failures = [];
const fail = (m) => { console.error(`\n❌ ${m}`); failures.push(m); };
const ok = (m) => console.log(`✓ ${m}`);

// Reuse the cached full chromium (chromium-1223), same as drive-confirm-ui.mjs.
const CHROME = process.env.PLAYWRIGHT_CHROMIUM_PATH
  ?? `${process.env.HOME}/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome`;
const browser = await chromium.launch({
  executablePath: CHROME,
  args: ["--use-gl=swiftshader", "--enable-webgl", "--ignore-gpu-blocklist"],
});
// Short viewport height so the Structural Threat section starts BELOW the fold — that's the
// whole point: without the auto-scroll it would be off-screen.
const ctx = await browser.newContext({ viewport: { width: 1280, height: 720 } });
const page = await ctx.newPage();

const logs = [];
page.on("console", (m) => logs.push(`[${m.type()}] ${m.text()}`));
page.on("pageerror", (e) => logs.push(`[pageerror] ${e.message}`));
page.on("requestfailed", (r) => logs.push(`[requestfailed] ${r.url()} ${r.failure()?.errorText}`));

try {
  const url = `${BASE}/?wt=${encodeURIComponent(WT)}&aoi=${encodeURIComponent(AOI)}&building=${encodeURIComponent(BUILDING)}`;
  console.log("→", url.replace(WT, `${WT.slice(0, 12)}…`)); // don't echo the full token
  await page.goto(url, { waitUntil: "networkidle", timeout: 30000 });
  // Bundle parse + deck mount + fly-to (900ms) + smooth scroll all need a beat to settle.
  await page.waitForTimeout(4000);

  // 1) The deep-linked building must be SELECTED → SelectedBuilding renders its analysis.
  const sidebar = page.locator("aside").first();
  const threatHeaders = page.getByText("Structural Threat", { exact: true });
  if (await threatHeaders.count()) ok('"Structural Threat" section rendered');
  else fail('"Structural Threat" section not found');

  const buildingTag = page.getByText(`Building #${BUILDING}`);
  if (await buildingTag.count()) ok(`deep-linked Building #${BUILDING} is selected (analysis shown)`);
  else fail(`Building #${BUILDING} not selected — SelectedBuilding did not render this building`);

  // 2) Did the sidebar auto-scroll? Measure scrollTop and where the anchor (the divider wrapper
  //    directly above the Structural-Threat header) sits relative to the aside's top edge.
  const scroll = await page.evaluate(() => {
    const aside = document.querySelector("aside");
    if (!aside) return { found: false };
    // The anchor is the wrapper <div> whose child is the divider, and whose NEXT sibling holds
    // the "Structural Threat" header. Find that header, then walk up to the aside's direct child.
    const headers = [...aside.querySelectorAll("div")].filter(
      (d) => d.textContent?.trim() === "Structural Threat",
    );
    const header = headers[0] ?? null;
    const asideRect = aside.getBoundingClientRect();
    let anchorTopRel = null;
    if (header) {
      // Direct child of aside that contains the header (the SelectedBuilding/NoSelection block).
      let block = header;
      while (block.parentElement && block.parentElement !== aside) block = block.parentElement;
      const prev = block.previousElementSibling; // the <div ref={threatAnchorRef}><Divider/></div>
      const target = prev ?? block;
      anchorTopRel = target.getBoundingClientRect().top - asideRect.top;
    }
    return {
      found: true,
      scrollTop: Math.round(aside.scrollTop),
      scrollHeight: aside.scrollHeight,
      clientHeight: aside.clientHeight,
      anchorTopRel: anchorTopRel == null ? null : Math.round(anchorTopRel),
    };
  });
  console.log("scroll:", JSON.stringify(scroll));

  if (!scroll.found) fail("no <aside> sidebar found");
  else {
    // The sidebar must actually be scrollable (content taller than the viewport) for the test
    // to be meaningful — otherwise there's nothing to scroll and the assertion is vacuous.
    if (scroll.scrollHeight > scroll.clientHeight + 4) ok(`sidebar is scrollable (${scroll.scrollHeight} > ${scroll.clientHeight})`);
    else fail(`sidebar not taller than viewport (${scroll.scrollHeight} <= ${scroll.clientHeight}) — widen content or shorten viewport`);

    if (scroll.scrollTop > 0) ok(`sidebar auto-scrolled (scrollTop=${scroll.scrollTop})`);
    else fail("sidebar did NOT auto-scroll (scrollTop=0) — Structural Threat stayed below the fold");

    // The anchor (divider above Structural Threat) should be at/near the top of the sidebar.
    if (scroll.anchorTopRel != null && Math.abs(scroll.anchorTopRel) <= 24)
      ok(`Structural-Threat divider is at the sidebar top (offset ${scroll.anchorTopRel}px)`);
    else fail(`Structural-Threat divider not at top (offset ${scroll.anchorTopRel}px)`);
  }

  await page.screenshot({ path: `${OUT_DIR}/01-deeplink-scrolled.png` });
} catch (e) {
  fail(`exception: ${e.message}`);
} finally {
  const errs = logs.filter((l) => l.startsWith("[error]") || l.startsWith("[pageerror]") || l.startsWith("[requestfailed]"));
  console.log(`\n--- console errors (${errs.length}) ---`);
  for (const l of errs.slice(-30)) console.log(l);
  console.log(`\nscreenshots → ${OUT_DIR}/`);
  await browser.close();
  console.log(`\n${failures.length ? "❌ FAIL" : "✅ PASS"} — ${failures.length} failure(s)`);
  process.exit(failures.length ? 1 : 0);
}
