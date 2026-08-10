// Walk the layout chain from window down to the canvas and dump computed styles.
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
mkdirSync("/tmp/infra-screens", { recursive: true });

const browser = await chromium.launch({ args: ["--use-gl=swiftshader", "--enable-webgl"] });
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
const page = await ctx.newPage();
page.on("pageerror", e => console.log("[pageerror]", e.message));
await page.goto("http://localhost:5174/", { waitUntil: "networkidle" });
await page.waitForTimeout(1500);

const data = await page.evaluate(() => {
  function describe(el, label) {
    if (!el) return { label, missing: true };
    const cs = getComputedStyle(el);
    return {
      label,
      tag: el.tagName,
      class: el.className?.toString().slice(0, 100),
      clientWH: { w: el.clientWidth, h: el.clientHeight },
      offsetWH: { w: el.offsetWidth, h: el.offsetHeight },
      rect: el.getBoundingClientRect().toJSON(),
      display: cs.display,
      flex: `flex: ${cs.flex}`,
      height: cs.height,
      width: cs.width,
      position: cs.position,
      alignItems: cs.alignItems,
      flexDirection: cs.flexDirection,
    };
  }
  const root = document.getElementById("root");
  const screenDiv = root?.firstElementChild;
  const flex1 = screenDiv?.firstElementChild;
  const mapDiv = flex1?.firstElementChild;
  const canvas = mapDiv?.querySelector("canvas");
  return {
    html: describe(document.documentElement, "html"),
    body: describe(document.body, "body"),
    root: describe(root, "#root"),
    screen: describe(screenDiv, ".h-screen.w-screen.flex"),
    flex1: describe(flex1, ".flex-1.h-full.relative (map wrapper)"),
    mapDiv: describe(mapDiv, ".absolute.inset-0 (mapContainer)"),
    canvas: describe(canvas, "canvas"),
  };
});
console.log(JSON.stringify(data, null, 2));
await page.screenshot({ path: "/tmp/infra-screens/inspect.png", fullPage: false });
await browser.close();
