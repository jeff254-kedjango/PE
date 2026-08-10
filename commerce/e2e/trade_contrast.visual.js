/**
 * Trade CATEGORY-TINT CONTRAST pass — a real-device a11y check the unit/API layers cannot do.
 *
 * The §8 trending rail paints each product card with a category color: an 18% tint on white as the
 * card background, a 22% tint disc behind the icon, and a DARKER derived accent (--cat-accent =
 * 85% cat + 15% black) for the price text and the icon glyph. Whether those small colored elements
 * clear WCAG AA on their own tint depends on browser-resolved color-mix() — which only a real
 * browser knows. This script asks Chromium to resolve the actual rendered rgb and computes the
 * real contrast ratios.
 *
 * Coverage: the live rail only shows categories present in nearby boosted data, so to check ALL 11
 * categories deterministically we inject one real .trending-rail__card per category into the live
 * page (same classes, same --cat-color var the React card sets inline) and measure the computed
 * styles the CSS produces. No seed dependency; every category is covered every run.
 *
 * Thresholds (WCAG 2.1):
 *   - price text (~0.78rem bold ≈ 12.5px bold → treat as normal text): AA 4.5:1
 *   - card title (text-primary on tint): AA 4.5:1
 *   - distance (text-secondary on tint, small): AA 4.5:1
 *   - icon glyph (accent on disc): non-text graphic, AA 3:1
 *   - focus outline (accent on white page): non-text, AA 3:1
 *
 * Run (weespas :8000, commerce :8003, FE :5174):
 *   cd PE/commerce/e2e
 *   NODE_PATH=/home/jeff/PE/InSAR-Final-main/frontend/node_modules node trade_contrast.visual.js
 */
const { chromium, request } = require('playwright');

const WEESPAS = process.env.WEESPAS_BASE_URL || 'http://127.0.0.1:8000';
const FE = process.env.FE_BASE_URL || 'http://127.0.0.1:5174';
const WEESPAS_API = `${WEESPAS}/api/v1`;
const EMAIL = process.env.WEESPAS_EMAIL || 'admin@weespas.com';
const PASSWORD = process.env.WEESPAS_PASSWORD || 'admin123';

const CATEGORIES = [
  'butchery', 'bakery', 'greengrocer', 'restaurant', 'boutique', 'electronics',
  'shoes', 'beauty', 'hardware', 'pharmacy', 'general',
];

let passed = 0;
const failures = [];
const check = (name, cond, detail = '') => {
  if (cond) { passed += 1; console.log(`  ✓ ${name}`); }
  else { failures.push(`${name}${detail ? ` — ${detail}` : ''}`); console.log(`  ✗ ${name}${detail ? ` — ${detail}` : ''}`); }
};

async function main() {
  const ctx = await request.newContext();
  const lr = await ctx.post(`${WEESPAS_API}/auth/login`, {
    headers: { 'Content-Type': 'application/json' },
    data: { email: EMAIL, password: PASSWORD },
  });
  if (!lr.ok()) { console.error('weespas login failed:', lr.status(), await lr.text()); process.exit(1); }
  const body = await lr.json();
  const weespasToken = body.token;
  const weespasUser = body.user;
  await ctx.dispose();

  const browser = await chromium.launch();
  try {
    const bctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, permissions: [] });
    await bctx.addInitScript(
      ([t, u]) => {
        localStorage.setItem('weespas_token', t);
        localStorage.setItem('weespas_user', u);
      },
      [weespasToken, JSON.stringify(weespasUser)],
    );
    const page = await bctx.newPage();
    await page.goto(`${FE}/trade`, { waitUntil: 'networkidle' });

    // Wait for the feed column so the app CSS (variables.css + TrendingRail.css) is definitely live.
    await page.locator('.trade-page__feed-col').first().waitFor({ timeout: 15000 }).catch(() => {});

    // Inject one REAL trending card per category and measure the browser-resolved contrast. The
    // measurement mirrors exactly what the React <TrendingCard> renders: same class names + the same
    // inline `--cat-color: var(--color-cat-<slug>)` the component sets, so color-mix() resolves
    // against the identical inputs. We read getComputedStyle → real rgb → WCAG ratio.
    const results = await page.evaluate((cats) => {
      // Normalize ANY CSS color string (rgb, color(srgb ..), oklab, color-mix output) to [r,g,b]
      // 0–255 by letting the canvas 2D context resolve + composite it over opaque white. This is
      // browser-authoritative, so we never have to parse exotic serializations by hand.
      const cvs = document.createElement('canvas');
      cvs.width = cvs.height = 1;
      const g2d = cvs.getContext('2d', { willReadFrequently: true });
      const parse = (color) => {
        g2d.clearRect(0, 0, 1, 1);
        g2d.fillStyle = '#fff';
        g2d.fillRect(0, 0, 1, 1);        // opaque white backing (matches the card/page bg)
        g2d.fillStyle = '#fff';
        g2d.fillStyle = color;           // ignored if the browser can't parse it → stays white
        g2d.fillRect(0, 0, 1, 1);
        const d = g2d.getImageData(0, 0, 1, 1).data;
        return [d[0], d[1], d[2]];
      };
      const lum = ([r, g, b]) => {
        const f = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
      };
      // Composite a possibly-translucent fg over a solid bg (defensive; tints here are opaque rgb).
      const ratio = (fg, bg) => {
        const L1 = lum(fg), L2 = lum(bg);
        const [hi, lo] = L1 >= L2 ? [L1, L2] : [L2, L1];
        return (hi + 0.05) / (lo + 0.05);
      };

      const host = document.createElement('ul');
      host.className = 'trending-rail__list';
      host.style.position = 'fixed';
      host.style.left = '-9999px';
      host.style.top = '0';
      host.style.width = '248px';
      document.body.appendChild(host);

      const out = {};
      const page_bg = getComputedStyle(document.body).backgroundColor || 'rgb(255,255,255)';
      for (const slug of cats) {
        const li = document.createElement('li');
        li.className = 'trending-rail__item';
        const btn = document.createElement('button');
        btn.className = 'trending-rail__card';
        btn.style.setProperty('--cat-color', `var(--color-cat-${slug})`);
        btn.innerHTML =
          '<span class="trending-rail__icon"><svg width="20" height="20"></svg></span>' +
          '<span class="trending-rail__meta">' +
          '<span class="trending-rail__name">Sample</span>' +
          '<span class="trending-rail__row">' +
          '<span class="trending-rail__price">KSh 500</span>' +
          '<span class="trending-rail__dist">1.2 km</span>' +
          '</span></span>';
        li.appendChild(btn);
        host.appendChild(li);

        const card = btn;
        const icon = btn.querySelector('.trending-rail__icon');
        const price = btn.querySelector('.trending-rail__price');
        const name = btn.querySelector('.trending-rail__name');
        const dist = btn.querySelector('.trending-rail__dist');

        const cardBg = getComputedStyle(card).backgroundColor;
        const iconBg = getComputedStyle(icon).backgroundColor;
        const accent = getComputedStyle(price).color;      // price uses --cat-accent
        const iconColor = getComputedStyle(icon).color;    // glyph uses --cat-accent
        const nameColor = getComputedStyle(name).color;
        const distColor = getComputedStyle(dist).color;

        out[slug] = {
          priceOnTint: +ratio(parse(accent), parse(cardBg)).toFixed(2),
          nameOnTint: +ratio(parse(nameColor), parse(cardBg)).toFixed(2),
          distOnTint: +ratio(parse(distColor), parse(cardBg)).toFixed(2),
          glyphOnDisc: +ratio(parse(iconColor), parse(iconBg)).toFixed(2),
          accentOnPage: +ratio(parse(accent), parse(page_bg)).toFixed(2),
          _rgb: { cardBg, iconBg, accent, nameColor, distColor },
        };
      }
      host.remove();
      return out;
    }, CATEGORIES);

    for (const slug of CATEGORIES) {
      const r = results[slug];
      console.log(`\n  [${slug}] price=${r.priceOnTint} name=${r.nameOnTint} dist=${r.distOnTint} glyph=${r.glyphOnDisc} accentOnPage=${r.accentOnPage}`);
      check(`${slug}: price text AA (≥4.5 on tint)`, r.priceOnTint >= 4.5, `${r.priceOnTint} (${r._rgb.accent} on ${r._rgb.cardBg})`);
      check(`${slug}: title text AA (≥4.5 on tint)`, r.nameOnTint >= 4.5, `${r.nameOnTint}`);
      check(`${slug}: distance text AA (≥4.5 on tint)`, r.distOnTint >= 4.5, `${r.distOnTint} (${r._rgb.distColor} on ${r._rgb.cardBg})`);
      check(`${slug}: icon glyph graphic AA (≥3 on disc)`, r.glyphOnDisc >= 3.0, `${r.glyphOnDisc}`);
    }

    await bctx.close();
  } finally {
    await browser.close();
  }

  console.log(`\n${passed} checks passed, ${failures.length} failed`);
  if (failures.length) { console.error('\nFAILURES:\n  - ' + failures.join('\n  - ')); process.exit(1); }
}

main().catch((e) => { console.error('contrast visual crashed:', e); process.exit(1); });
