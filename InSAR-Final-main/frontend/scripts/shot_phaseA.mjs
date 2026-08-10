// Phase-A proof: load the app, ensure Huruma (real InSAR) is active, wait for
// the bundle + observation date to populate, then screenshot the full app.
import { chromium } from "playwright";

const URL = process.env.SHOT_URL || "http://127.0.0.1:5174/";
const OUT = process.env.SHOT_OUT || "/tmp/phaseA_huruma.png";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });

const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });

await page.goto(URL, { waitUntil: "networkidle", timeout: 60000 });

// Make sure Huruma is the active AOI (it is aois[0] by default, but click to be safe).
const huruma = page.locator("button", { hasText: /^Huruma$/i }).first();
if (await huruma.count()) await huruma.click();

// "Data ready" signal: the Observation date stops being the em-dash placeholder.
await page.waitForFunction(() => {
  const els = [...document.querySelectorAll("div.tabular-nums")];
  return els.some((e) => e.textContent && /\d{4}-\d{2}-\d{2}/.test(e.textContent));
}, { timeout: 60000 }).catch(() => {});

// Give the map/canvas a moment to paint the building layer.
await page.waitForTimeout(2500);

await page.screenshot({ path: OUT, fullPage: false });

// Report the observation date we captured, for the log.
const obs = await page.evaluate(() => {
  const els = [...document.querySelectorAll("div.tabular-nums")];
  const hit = els.find((e) => /\d{4}-\d{2}-\d{2}/.test(e.textContent || ""));
  return hit ? hit.textContent.trim() : null;
});

console.log("screenshot:", OUT);
console.log("observation_date:", obs);
console.log("page_errors:", errors.length ? errors.slice(0, 5) : "none");

await browser.close();
