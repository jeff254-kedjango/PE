// Drive the real app in headless Chromium and screenshot what renders.
// Also: capture every console message and uncaught error, dump page metrics
// about the deck canvas. This is the only honest way to verify "did anything
// actually paint".
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
page.on("console", msg => {
  logs.push(`[${msg.type()}] ${msg.text()}`);
});
page.on("pageerror", err => {
  logs.push(`[pageerror] ${err.message}\n${err.stack ?? ""}`);
});
page.on("requestfailed", req => {
  logs.push(`[requestfailed] ${req.url()} ${req.failure()?.errorText}`);
});

console.log("→", URL);
await page.goto(URL, { waitUntil: "networkidle", timeout: 20000 });
await page.waitForTimeout(2000);   // let layers mount

// Read DOM measurements
const metrics = await page.evaluate(() => {
  const root = document.getElementById("root");
  const canvases = [...document.querySelectorAll("canvas")];
  const mapContainer = document.querySelector(".maplibregl-map") || document.querySelector("[class*='maplibregl']");
  return {
    window: { w: window.innerWidth, h: window.innerHeight },
    root: root ? { w: root.clientWidth, h: root.clientHeight } : null,
    mapContainer: mapContainer
      ? { w: mapContainer.clientWidth, h: mapContainer.clientHeight, className: mapContainer.className }
      : "not found",
    canvases: canvases.map(c => ({
      w: c.clientWidth, h: c.clientHeight,
      wAttr: c.width, hAttr: c.height,
    })),
    webgl: (() => {
      const c = document.createElement("canvas");
      const gl = c.getContext("webgl2") || c.getContext("webgl");
      return gl ? "ok" : "missing";
    })(),
  };
});

await page.screenshot({ path: `${OUT_DIR}/screen.png`, fullPage: false });

console.log("\n--- METRICS ---");
console.log(JSON.stringify(metrics, null, 2));
console.log("\n--- CONSOLE LOGS (last 80) ---");
for (const l of logs.slice(-80)) console.log(l);
console.log(`\nscreenshot → ${OUT_DIR}/screen.png`);

await browser.close();
