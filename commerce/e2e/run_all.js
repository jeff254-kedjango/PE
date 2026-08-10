/**
 * STANDING LIVE-E2E LOOP — one entry point that runs every live Playwright check in sequence and
 * exits non-zero if any fails. This is the "always run Playwright alongside vitest + pytest" rule
 * made into a single command: instead of remembering a dozen `NODE_PATH=… node foo.e2e.js` lines,
 * a verification pass is `node run_all.js`.
 *
 * It preflights the services first (weespas :8000, commerce :8003, weespas FE :5174, InSAR FE :5173,
 * mobility :8004) and, if any is
 * down, SKIPS the scripts that need it (clearly reported) rather than drowning the run in identical
 * connection errors. Each child inherits a fully-populated env (defaults for the token-bridge login
 * + base URLs), so no per-script env line is needed.
 *
 * It does NOT seed any product data. The Trade sections (Feed/Timeline, Short Videos, Quick Buys,
 * Flash Sales) are driven by REAL data — products a seller actually uploaded — so a bare section is
 * a true empty state, not something to paper over with placeholders. The one exception is the
 * trending rail, whose fixed demo pool is maintained by the standalone trending_demo process
 * (PE/dev/commerce-trending-demo.sh), not by this loop.
 *
 * Scope: the FAST, deterministic live checks that belong in every pass. Deliberately EXCLUDED and
 * gated behind flags because they are slow or need non-HTTP access:
 *   --with-perf   also run trending.perf.js  (seeds ~200 boosted listings + a 120s rotation window)
 *   --with-sweep  also run expiry_sweep_live.py (drives the live PostGIS directly; needs .env DB creds)
 * `jwt.js` is a token-minting helper (required by the others), never run on its own.
 *
 * Run (from anywhere):
 *   NODE_PATH=/home/jeff/PE/InSAR-Final-main/frontend/node_modules node PE/commerce/e2e/run_all.js
 *   …/node run_all.js --with-perf --with-sweep     # include the heavy/DB checks too
 *
 * Exit code: 0 iff every script that actually ran passed. Skipped-for-service-down does NOT fail the
 * run (the stack simply wasn't fully up); a script that ran and failed DOES.
 */
const { spawnSync } = require('child_process');
const http = require('http');
const path = require('path');

const HERE = __dirname;
const WEESPAS = process.env.WEESPAS_BASE_URL || 'http://127.0.0.1:8000';
const COMMERCE = process.env.COMMERCE_BASE_URL || 'http://127.0.0.1:8003';
// The FE the Trade e2e drives is the WEESPAS frontend (:5174 — pinned in weespas-frontend/
// vite.config.ts). :5173 is the InSAR FE (a different app), so defaulting there silently tests
// the wrong SPA. Override with FE_BASE_URL only if your weespas FE runs elsewhere.
const FE = process.env.FE_BASE_URL || 'http://127.0.0.1:5174';
// The INSAR frontend — a DIFFERENT app from the weespas FE above. Only the §8.1a shops-on-map
// spec drives it (:5173, the ?wt= deep-link target). Kept separate so a down InSAR FE skips only
// that spec, not the whole weespas-FE suite.
const INSAR_FE = process.env.INSAR_FE_URL || 'http://127.0.0.1:5173';
// The MOBILITY dispatch service — a DIFFERENT service from commerce (:8004, own Redis db 4). Its
// live e2e (a Node fetch+SSE driver, no browser — mobility has no FE) lives in PE/mobility/e2e and
// is registered here so the standing loop covers all three trading-layer pillars in one command.
const MOBILITY = process.env.MOBILITY_BASE_URL || 'http://127.0.0.1:8004';
const MOBILITY_E2E_DIR = path.resolve(HERE, '../../mobility/e2e');

const argv = process.argv.slice(2);
const withPerf = argv.includes('--with-perf');
const withSweep = argv.includes('--with-sweep');

// Node needs to find `playwright`, which lives only in the InSAR frontend node_modules. If the
// caller didn't set NODE_PATH we default it (and the emoji script also wants HOME, already set).
const NODE_PATH = process.env.NODE_PATH || '/home/jeff/PE/InSAR-Final-main/frontend/node_modules';

// Env every child inherits: base URLs + bridge-login creds, resolved once here.
const CHILD_ENV = {
  ...process.env,
  NODE_PATH,
  WEESPAS_BASE_URL: WEESPAS,
  COMMERCE_BASE_URL: COMMERCE,
  FE_BASE_URL: FE,
  INSAR_FE_URL: INSAR_FE,
  MOBILITY_API: MOBILITY,
  WEESPAS_FE_URL: process.env.WEESPAS_FE_URL || FE,
  WEESPAS_EMAIL: process.env.WEESPAS_EMAIL || 'admin@weespas.com',
  WEESPAS_PASSWORD: process.env.WEESPAS_PASSWORD || 'admin123',
};

// Liveness probe: GET the given URL, resolve true on any HTTP response (even 4xx — the port is up).
function ping(url) {
  return new Promise((resolve) => {
    const req = http.get(url, { timeout: 3000 }, (res) => {
      res.resume();
      resolve(true);
    });
    req.on('error', () => resolve(false));
    req.on('timeout', () => { req.destroy(); resolve(false); });
  });
}

// The suite. `needs` lists which services must be up for the script to run.
//   W = weespas API, C = commerce API, F = weespas frontend, I = InSAR frontend (a real browser
//   drives the page), M = mobility dispatch API (:8004).
// A script in a DIFFERENT directory than HERE (commerce/e2e) carries an explicit `dir` so it runs
// with the correct cwd (its own jwt.js / teardown resolve relative to __dirname).
const SUITE = [
  { file: 'commerce.e2e.js', needs: ['C'], desc: 'settlement · receipts · reviews · storefront (API)' },
  { file: 'trade.fe.e2e.js', needs: ['W', 'C'], desc: 'FE-1 buyer-feed weespas→commerce bridge' },
  { file: 'seller.fe.e2e.js', needs: ['W', 'C'], desc: 'FE-2 seller write path (two-token) + boost' },
  { file: 'edit_delete_promote.fe.e2e.js', needs: ['W', 'C'], desc: 'listing edit/soft-delete + global promotion + shop logo/banner' },
  { file: 'trending.fe.e2e.js', needs: ['W', 'C'], desc: 'trending rail + comment moderation matrix' },
  { file: 'quick_buys.fe.e2e.js', needs: ['W', 'C', 'F'], desc: 'Quick Buys grid (near/interest mix + filter + render)' },
  { file: 'flash_sales.fe.e2e.js', needs: ['W', 'C', 'F'], desc: 'Flash Sales grid (nationwide crazy offers + margin rank + buy override)' },
  { file: 'trending.visual.js', needs: ['W', 'C', 'F'], desc: 'trending rail visual/layout' },
  { file: 'trade_contrast.visual.js', needs: ['W', 'F'], desc: 'category-tint WCAG contrast (11 cats)' },
  { file: 'trade_responsive.visual.js', needs: ['W', 'F'], desc: 'Trade responsive breakpoints' },
  { file: 'trade_sticky_rail.visual.js', needs: ['W', 'C', 'F'], desc: 'bottom-anchored sticky Trade right column (pin + release)' },
  { file: 'shop_hovercard.touch.e2e.js', needs: ['W', 'F'], desc: 'shop hovercard mobile tap-target (375px)' },
  { file: 'navbar_avatar.fe.e2e.js', needs: ['W', 'F'], desc: 'signed-in navbar avatar chip + "My Profile" hover popup' },
  { file: 'brand_mark.fe.e2e.js', needs: ['W', 'F'], desc: 'crosshair brand mark across 6 viewports (square glyph, em-scaled, no overflow) + SVG favicon' },
  { file: 'navbar_search.fe.e2e.js', needs: ['W', 'C', 'F'], desc: '§search unified navbar search (Properties + Trade tabs, cents→major price, storefront deep-link, anon gating)' },
  { file: 'composer_lanes.fe.e2e.js', needs: ['W', 'C', 'F'], desc: 'Trade composer tool row + Shops|Clips|Podcasts lane bar (2px seam, matched width, one-line row)' },
  { file: 'shop_video_strip.visual.js', needs: ['W', 'F'], desc: 'shop video strip shelf + player' },
  { file: 'emoji_portal.verify.js', needs: ['W', 'C', 'F'], desc: 'emoji palette portal (no overflow clip)' },
  { file: 'shops_on_map.fe.e2e.js', needs: ['W', 'C', 'I'], desc: '§8.1a shops-on-InSAR-map cross-DB seed → deck.gl pin render (confirmed/plain)' },
  { file: 'dispatch.e2e.js', dir: MOBILITY_E2E_DIR, needs: ['M'], desc: '§5 mobility dispatch spine (ping → match → SSE, eligibility + revocation gates)' },
];


function runScript(file, dir) {
  const started = Date.now();
  const base = dir || HERE;
  const r = spawnSync(process.execPath, [path.join(base, file)], {
    cwd: base,
    env: CHILD_ENV,
    stdio: 'inherit',
  });
  const secs = ((Date.now() - started) / 1000).toFixed(1);
  return { ok: r.status === 0, code: r.status, secs, signal: r.signal };
}

async function main() {
  console.log('── Preflighting services ──');
  const up = {
    W: await ping(`${WEESPAS}/health`),
    C: await ping(`${COMMERCE}/health`),
    F: await ping(`${FE}/`),
    I: await ping(`${INSAR_FE}/`),
    M: await ping(`${MOBILITY}/health`),
  };
  // Print the RESOLVED URLs (not hardcoded port labels) so a wrong override or a stale default is
  // visible at a glance — e.g. FE pointing at :5173 (InSAR) instead of :5174 (weespas).
  console.log(`  weespas  ${WEESPAS}  ${up.W ? 'UP' : 'DOWN'}`);
  console.log(`  commerce ${COMMERCE}  ${up.C ? 'UP' : 'DOWN'}`);
  console.log(`  weespas FE ${FE}  ${up.F ? 'UP' : 'DOWN'}`);
  console.log(`  InSAR FE ${INSAR_FE}  ${up.I ? 'UP' : 'DOWN'}`);
  console.log(`  mobility ${MOBILITY}  ${up.M ? 'UP' : 'DOWN'}`);
  console.log('');

  const suite = SUITE.slice();
  if (withSweep) suite.push({ file: 'expiry_sweep_live.py', needs: ['C'], desc: 'TTL expiry sweep (DB-direct)', python: true });
  if (withPerf) suite.push({ file: 'trending.perf.js', needs: ['W', 'C'], desc: 'boost reach/efficiency benchmark (~120s)' });

  const results = [];
  for (const t of suite) {
    const missing = t.needs.filter((s) => !up[s]);
    if (missing.length) {
      const names = { W: 'weespas', C: 'commerce', F: 'FE', I: 'InSAR FE', M: 'mobility' };
      console.log(`\n▷ SKIP ${t.file} — needs ${missing.map((s) => names[s]).join(' + ')} (down)`);
      results.push({ ...t, skipped: true });
      continue;
    }
    console.log(`\n▶ ${t.file} — ${t.desc}`);
    if (t.python) {
      // The sweep is a Python script against the live DB, not a Node/Playwright test — call it out
      // rather than trying to run it here (it needs .env DB creds sourced). Report as a directed skip.
      console.log('  (run separately: see README "Expiry sweep" — needs .env DB creds sourced)');
      results.push({ ...t, skipped: true, note: 'python/DB — run per README' });
      continue;
    }
    const r = runScript(t.file, t.dir);
    results.push({ ...t, ...r });
    console.log(`  ${r.ok ? '✓ PASS' : `✗ FAIL (exit ${r.code}${r.signal ? `/${r.signal}` : ''})`} — ${r.secs}s`);
  }

  const ran = results.filter((r) => !r.skipped);
  const failed = ran.filter((r) => !r.ok);
  const skipped = results.filter((r) => r.skipped);

  console.log('\n═══════════════ STANDING E2E SUMMARY ═══════════════');
  for (const r of results) {
    const status = r.skipped ? 'SKIP' : r.ok ? 'PASS' : 'FAIL';
    console.log(`  ${status.padEnd(4)}  ${r.file.padEnd(30)} ${r.skipped ? (r.note || 'service down') : `${r.secs}s`}`);
  }
  console.log('────────────────────────────────────────────────────');
  console.log(`  ${ran.length} ran · ${ran.length - failed.length} passed · ${failed.length} failed · ${skipped.length} skipped`);

  if (failed.length) {
    console.error(`\nFAILED: ${failed.map((r) => r.file).join(', ')}`);
    process.exit(1);
  }
  if (!ran.length) {
    console.error('\nNothing ran — is the stack up? (weespas :8000, commerce :8003, weespas FE :5174)');
    process.exit(2);
  }
  console.log('\nAll live e2e green.');
}

main().catch((e) => { console.error('run_all crashed:', e); process.exit(1); });
