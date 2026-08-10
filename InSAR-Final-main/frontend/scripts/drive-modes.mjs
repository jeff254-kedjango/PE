// Drive both Subsidence and Drift modes across both AOIs, screenshot each,
// and click a building in drift mode to verify HeightCard + Horizontal Drift
// metrics render. Output to /tmp/infra-screens/*.png.
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const URL = process.argv[2] ?? "http://localhost:5174/";
const OUT_DIR = "/tmp/infra-screens";
mkdirSync(OUT_DIR, { recursive: true });

const browser = await chromium.launch({
  args: ["--use-gl=swiftshader", "--enable-webgl", "--ignore-gpu-blocklist"],
});
const ctx = await browser.newContext({
  viewport: { width: 1600, height: 1000 },
  deviceScaleFactor: 1,
});
const page = await ctx.newPage();

const logs = [];
page.on("console", msg => logs.push(`[${msg.type()}] ${msg.text()}`));
page.on("pageerror", err => logs.push(`[pageerror] ${err.message}`));
page.on("requestfailed", req =>
  logs.push(`[requestfailed] ${req.url()} ${req.failure()?.errorText}`),
);

console.log("→", URL);
await page.goto(URL, { waitUntil: "networkidle", timeout: 30000 });
// Give the PMTiles probe + first bundle parse a beat to finish.
await page.waitForTimeout(3500);

// --- AOI 1 (default, Huruma) ---------------------------------------------
await page.screenshot({ path: `${OUT_DIR}/01-huruma-subsidence.png` });

const driftBtn = page.getByRole("button", { name: /^Drift$/i }).first();
await driftBtn.click();
await page.waitForTimeout(1200);
await page.screenshot({ path: `${OUT_DIR}/02-huruma-drift.png` });

// Click somewhere likely to hit a building in drift mode.
await page.mouse.click(800, 500);
await page.waitForTimeout(600);
await page.screenshot({ path: `${OUT_DIR}/03-huruma-drift-inspected.png` });

const subBtn = page.getByRole("button", { name: /^Subsidence$/i }).first();
await subBtn.click();
await page.waitForTimeout(800);
await page.screenshot({ path: `${OUT_DIR}/04-huruma-subsidence-inspected.png` });

// --- AOI 2 (Mombasa) ------------------------------------------------------
// AOI buttons render with the registry's `name` text. Find anything matching
// "Mombasa" in the topbar.
const mombasaBtn = page.getByRole("button", { name: /Mombasa/i }).first();
const hasMombasa = await mombasaBtn.count().catch(() => 0);
if (hasMombasa) {
  await mombasaBtn.click();
  // flyTo takes ~1.2 s; bundle reload is local + already cached.
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${OUT_DIR}/05-mombasa-subsidence.png` });

  await driftBtn.click();
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${OUT_DIR}/06-mombasa-drift.png` });

  await page.mouse.click(800, 500);
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${OUT_DIR}/07-mombasa-drift-inspected.png` });
} else {
  console.log("⚠ Mombasa AOI button not found — registry may only expose one AOI.");
}

// Sample a few center pixels per mode to sanity-check the ramp:
// red-ish in Huruma subsidence, blue/orange divergent in drift.
const pixelSample = await page.evaluate(() => {
  const c = document.querySelectorAll("canvas")[1] || document.querySelector("canvas");
  const gl = c.getContext("webgl2") || c.getContext("webgl");
  if (!gl) return null;
  const px = new Uint8Array(4);
  gl.readPixels(c.width / 2, c.height / 2, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
  return Array.from(px);
});

console.log("\n--- CENTER PIXEL (final frame) ---");
console.log(JSON.stringify(pixelSample));

console.log("\n--- CONSOLE (last 40) ---");
for (const l of logs.slice(-40)) console.log(l);

console.log(`\nscreenshots → ${OUT_DIR}/*.png`);

await browser.close();
