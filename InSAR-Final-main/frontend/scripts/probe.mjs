// Probe deeper than drive.mjs: sample pixels at the map center, list all
// canvases with their z-order and bbox, and inspect the deck overlay state.
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
page.on("pageerror", err => logs.push(`[pageerror] ${err.message}\n${err.stack ?? ""}`));
page.on("requestfailed", req => logs.push(`[requestfailed] ${req.url()} ${req.failure()?.errorText}`));

console.log("→", URL);
await page.goto(URL, { waitUntil: "networkidle", timeout: 20000 });
await page.waitForTimeout(2500);

const metrics = await page.evaluate(() => {
  const canvases = [...document.querySelectorAll("canvas")];
  const list = canvases.map((c, i) => {
    const r = c.getBoundingClientRect();
    const cs = getComputedStyle(c);
    // Try to read a center pixel via getImageData on a 2D copy.
    let centerPx = null;
    try {
      const tmp = document.createElement("canvas");
      tmp.width = 1; tmp.height = 1;
      const ctx2 = tmp.getContext("2d");
      // Draw the GL canvas into the 2d canvas (this requires preserveDrawingBuffer
      // typically; deck.gl/maplibre may not preserve, so this can be black).
      ctx2.drawImage(c, c.width / 2, c.height / 2, 1, 1, 0, 0, 1, 1);
      const d = ctx2.getImageData(0, 0, 1, 1).data;
      centerPx = [d[0], d[1], d[2], d[3]];
    } catch (e) {
      centerPx = `err:${e.message}`;
    }
    return {
      i,
      tag: c.className || "(no class)",
      parentClass: c.parentElement?.className ?? "",
      bbox: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
      drawBuf: { w: c.width, h: c.height },
      style: { position: cs.position, zIndex: cs.zIndex, opacity: cs.opacity, visibility: cs.visibility, display: cs.display, transform: cs.transform },
      centerPx,
    };
  });

  // Pull deck overlay internals if reachable via global.
  let deckInfo = "n/a";
  // No direct global, but maplibre map is on window only if app exposed it.
  return { canvases: list, deckInfo };
});

// Sample actual screen pixel at viewport center (800, 500) via screenshot decode.
const buf = await page.screenshot({ path: `${OUT_DIR}/screen.png`, fullPage: false });

// Read RGB at a few key points by re-loading the image into the page.
const samples = await page.evaluate(async (b64) => {
  const img = new Image();
  img.src = "data:image/png;base64," + b64;
  await new Promise(r => (img.onload = r));
  const c = document.createElement("canvas");
  c.width = img.width; c.height = img.height;
  const cx = c.getContext("2d");
  cx.drawImage(img, 0, 0);
  const at = (x, y) => {
    const d = cx.getImageData(x, y, 1, 1).data;
    return [d[0], d[1], d[2], d[3]];
  };
  return {
    center: at(800, 500),
    upperLeftMap: at(300, 300),
    bottomMid: at(800, 800),
    aboveCenter: at(800, 400),
    rightThird: at(1100, 500),
    leftEdge: at(60, 500),
  };
}, buf.toString("base64"));

console.log("\n--- CANVAS LIST ---");
console.log(JSON.stringify(metrics, null, 2));
console.log("\n--- SCREENSHOT PIXEL SAMPLES (rgba) ---");
console.log(JSON.stringify(samples, null, 2));
console.log("\n--- CONSOLE LOGS (last 100) ---");
for (const l of logs.slice(-100)) console.log(l);
console.log(`\nscreenshot → ${OUT_DIR}/screen.png`);

await browser.close();
