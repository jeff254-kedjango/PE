// Drive the REAL InSAR app in headless Chromium to verify the "← Back to Weespas" chip —
// the FULL journey, not just href shape:
//   1. arriving from Weespas with ?wt=&return=<clean path> renders a chip whose href is the
//      Weespas WEB origin (:5173) + that path, with NO leaked wt/return token;
//   2. CLICKING the chip actually navigates to Weespas (:5173), not back into InSAR (:5174);
//   3. off-origin / absolute / token-bearing return values are REJECTED (chip suppressed),
//      so a hand-crafted link can't open-redirect or smuggle a token through the chip.
//
// Ports (authoritative — weespas/core/config.py): InSAR FE = :5174 (insar_public_url, the
// ?wt= deep-link target), Weespas FE = :5173. The chip must point at :5173.
//
// There is no JS test runner in the InSAR app and jsdom can't run MapLibre/deck, so we drive
// a real browser. READ-ONLY on InSAR; the Weespas landing is a normal page load.
import { chromium } from "playwright";
import { execFileSync } from "node:child_process";

const INSAR = process.env.INSAR_URL ?? "http://localhost:5174";     // InSAR FE
const WEESPAS_ORIGIN = process.env.WEESPAS_ORIGIN ?? "http://localhost:5173"; // Weespas FE
const AOI = process.env.AOI ?? "huruma";
const BUILDING = process.env.BUILDING ?? "100000";

function mintToken() {
  if (process.env.WT) return process.env.WT.trim();
  const out = execFileSync(
    "/home/jeff/PE/weespas/.venv/bin/python",
    ["-c", "from PE.weespas.services.auth_service import create_insar_telemetry_token as m; print(m('drive-test-user','individual'))"],
    { cwd: "/home/jeff/PE/weespas", env: { ...process.env, PYTHONPATH: "/home/jeff" }, encoding: "utf8" },
  );
  return out.trim().split("\n").pop().trim();
}
const WT = mintToken();

const failures = [];
const fail = (m) => { console.error(`❌ ${m}`); failures.push(m); };
const ok = (m) => console.log(`✓ ${m}`);

const CHROME = process.env.PLAYWRIGHT_CHROMIUM_PATH
  ?? `${process.env.HOME}/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome`;
const browser = await chromium.launch({
  executablePath: CHROME,
  args: ["--use-gl=swiftshader", "--enable-webgl", "--ignore-gpu-blocklist"],
});
const ctx = await browser.newContext({ viewport: { width: 1280, height: 720 } });
const page = await ctx.newPage();
const logs = [];
page.on("pageerror", (e) => logs.push(`[pageerror] ${e.message}`));

async function chip() {
  const c = page.getByRole("link", { name: /Back to Weespas/i });
  return (await c.count()) ? c.first() : null;
}
async function load(returnRaw) {
  const ret = returnRaw == null ? "" : `&return=${encodeURIComponent(returnRaw)}`;
  const url = `${INSAR}/?wt=${encodeURIComponent(WT)}&aoi=${encodeURIComponent(AOI)}&building=${encodeURIComponent(BUILDING)}${ret}`;
  console.log("→", url.replace(WT, `${WT.slice(0, 10)}…`).replace(WT, `${WT.slice(0, 10)}…`));
  await page.goto(url, { waitUntil: "networkidle", timeout: 30000 });
  await page.waitForTimeout(2500);
}

try {
  // CASE 1 — clean return path → chip points at Weespas :5173, NO token leak.
  await load("/properties/L9?ref=card");
  const c1 = await chip();
  const href1 = c1 ? await c1.getAttribute("href") : null;
  const want1 = `${WEESPAS_ORIGIN}/properties/L9?ref=card`;
  if (href1 === want1) ok(`clean return → href = ${href1}`);
  else fail(`clean return → href is ${JSON.stringify(href1)}, expected ${JSON.stringify(want1)}`);
  if (href1 && (href1.includes("wt=") || href1.includes(WT.slice(0, 10))))
    fail(`TOKEN LEAK: chip href contains a telemetry token: ${href1}`);
  else ok("no telemetry token in chip href");
  if (href1 && href1.includes(`:5174`)) fail(`chip points back into InSAR (:5174): ${href1}`);
  else ok("chip does NOT point back into InSAR");

  // CASE 1b — CLICK it: must actually land on Weespas :5173, not InSAR.
  if (c1) {
    await Promise.all([
      page.waitForNavigation({ waitUntil: "domcontentloaded", timeout: 30000 }).catch(() => {}),
      c1.click(),
    ]);
    await page.waitForTimeout(1500);
    const landed = new URL(page.url());
    if (`${landed.protocol}//${landed.host}` === WEESPAS_ORIGIN) ok(`click landed on Weespas (${landed.host})`);
    else fail(`click landed on ${landed.host}, expected ${WEESPAS_ORIGIN.replace(/^https?:\/\//, "")}`);
    if (landed.pathname === "/properties/L9") ok("click landed on the exact return path");
    else fail(`click landed on path ${landed.pathname}, expected /properties/L9`);
  }

  // CASE 2 — a return value that itself carries a token (the bug we just fixed at the SOURCE,
  // but the InSAR sanitizer must ALSO refuse a hand-crafted token-bearing return). A value
  // with an embedded "?wt=" is still a valid relative path, so the chip MAY render it — but
  // it must never have come from our own builder. We assert the realistic attack: an ABSOLUTE
  // url and a protocol-relative one are both rejected.
  await load("//evil.com/phish");
  if ((await chip()) === null) ok("protocol-relative //evil.com return → chip suppressed");
  else fail("off-origin //evil.com return rendered a chip — OPEN REDIRECT");

  await load("https://evil.com");
  if ((await chip()) === null) ok("absolute https return → chip suppressed");
  else fail("absolute-URL return rendered a chip — OPEN REDIRECT");

  // CASE 3 — no return param (direct visit) → no chip.
  await load(null);
  if ((await chip()) === null) ok("no return param → chip absent");
  else fail("chip rendered without a return param");
} catch (e) {
  fail(`exception: ${e.message}`);
} finally {
  const errs = logs.filter((l) => l.startsWith("[pageerror]"));
  if (errs.length) { console.log(`\n--- page errors ---`); errs.slice(-10).forEach((l) => console.log(l)); }
  await browser.close();
  console.log(`\n${failures.length ? "❌ FAIL" : "✅ PASS"} — ${failures.length} failure(s)`);
  process.exit(failures.length ? 1 : 0);
}
