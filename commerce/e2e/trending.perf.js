/**
 * §8.5 trending rail — PERFORMANCE / EFFICIENCY harness at scale.
 *
 * Seeds 200 LIVE listing boosts near the Nairobi centre (20 sellers × 10 mtaa grants — mtaa's
 * daily free allowance is 10/seller, radius 10 km, so all 200 land in-bucket without burning a
 * separate identity per listing). The slate returns the whole queue (feed_sponsored_max_candidates
 * = 200); the client shows `visible_slots` (~9–12) and CYCLES the other ~190 through freed slots —
 * the contention path we want to stress.
 *
 * It then loads /trade and, with the mouse parked off the rail (rotation un-paused), runs an
 * extended loop while instrumenting the page:
 *   - long tasks (PerformanceObserver 'longtask' ≥ 50ms — the jank signal)
 *   - main-thread frame budget via rAF deltas (dropped frames > 50ms)
 *   - JS heap at start vs end (leak check across hundreds of swaps)
 *   - DOM card count held steady (no slot growth — fixed-slot invariant under churn)
 *   - flip coverage: how many DISTINCT listing_ids appeared over the run (queue fairness) and
 *     max simultaneous flips per tick (independence — never the whole board at once)
 *
 * Run (stack up — weespas :8000, commerce :8003, FE :5174):
 *   cd PE/commerce/e2e
 *   NODE_PATH=/home/jeff/PE/InSAR-Final-main/frontend/node_modules \
 *     [SELLERS=20 PER_SELLER=10 LOOP_S=120] node trending.perf.js
 */
const { chromium, request } = require('playwright');
const { seller, registerCleanup } = require('./jwt');

const WEESPAS = process.env.WEESPAS_BASE_URL || 'http://127.0.0.1:8000';
const COMMERCE = process.env.COMMERCE_BASE_URL || 'http://127.0.0.1:8003';
const FE = process.env.FE_BASE_URL || 'http://127.0.0.1:5174';
const WEESPAS_API = `${WEESPAS}/api/v1`;
const COMMERCE_API = `${COMMERCE}/api/v1`;
const EMAIL = process.env.WEESPAS_EMAIL || 'admin@weespas.com';
const PASSWORD = process.env.WEESPAS_PASSWORD || 'admin123';

const SELLERS = parseInt(process.env.SELLERS || '20', 10);
const PER_SELLER = parseInt(process.env.PER_SELLER || '10', 10);   // ≤ mtaa daily free (10)
const TARGET = SELLERS * PER_SELLER;
const LOOP_S = parseInt(process.env.LOOP_S || '120', 10);          // rotation observation window

const NBO = { lat: -1.2921, lng: 36.8219 };
const RUN = `perf-${Date.now()}`;
registerCleanup(RUN);   // remove this run's shops/listings from the live DB on exit (any path)
const authH = (t) => ({ Authorization: `Bearer ${t}` });
const CATS = ['restaurant', 'greengrocer', 'bakery', 'butchery', 'electronics',
  'boutique', 'shoes', 'pharmacy', 'beauty', 'hardware', 'general'];

async function seed(ctx) {
  const grants = [];
  let made = 0;
  for (let s = 0; s < SELLERS && made < TARGET; s++) {
    const tok = seller(`${RUN}-s${s}`);
    const sr = await ctx.post(`${COMMERCE_API}/shops`, {
      headers: authH(tok),
      data: { name: `Perf Shop ${s} ${RUN}`, lat: NBO.lat, lng: NBO.lng,
        display_name: `Perf ${s}`, category: CATS[s % CATS.length] },
    });
    if (sr.status() !== 201) { console.error(`shop ${s} → ${sr.status()}: ${await sr.text()}`); continue; }
    const shop = await sr.json();
    for (let j = 0; j < PER_SELLER && made < TARGET; j++) {
      const lr = await ctx.post(`${COMMERCE_API}/shops/${shop.id}/listings`, {
        headers: authH(tok),
        data: { title: `P${s}-${j} ${RUN}`, price_cents: 10000 + (s * 137 + j * 53) % 90000, stock_qty: 99 },
      });
      if (lr.status() !== 201) { console.error(`listing ${s}.${j} → ${lr.status()}`); continue; }
      const listing = await lr.json();
      // mtaa: 10 km radius (covers the seed point), 10/seller/day → exactly PER_SELLER fits.
      const br = await ctx.post(`${COMMERCE_API}/boosts`, {
        headers: authH(tok),
        data: { target_type: 'listing', target_id: listing.id, tier: 'mtaa' },
      });
      if (br.status() !== 201) { console.error(`boost ${s}.${j} → ${br.status()}: ${await br.text()}`); continue; }
      grants.push({ tok, id: (await br.json()).id });
      made++;
    }
    process.stdout.write(`\rseeded ${made}/${TARGET}`);
  }
  process.stdout.write('\n');
  return grants;
}

async function main() {
  const ctx = await request.newContext();
  const lr = await ctx.post(`${WEESPAS_API}/auth/login`, {
    headers: { 'Content-Type': 'application/json' },
    data: { email: EMAIL, password: PASSWORD },
  });
  const body = await lr.json();
  const weespasToken = body.token, weespasUser = body.user;

  const t0 = Date.now();
  const grants = await seed(ctx);
  console.log(`seed wall time: ${((Date.now() - t0) / 1000).toFixed(1)}s for ${grants.length} boosts`);

  try {
    // Confirm the slate actually carries the full queue (server-side scale check).
    const sess = await ctx.get(`${WEESPAS_API}/commerce/session-token`, { headers: authH(weespasToken) });
    const { token: commerceToken } = await sess.json();
    const tStart = Date.now();
    const sr = await ctx.get(`${COMMERCE_API}/trending?lat=${NBO.lat}&lng=${NBO.lng}`, { headers: authH(commerceToken) });
    const slateMs = Date.now() - tStart;
    const slate = await sr.json();
    console.log(`slate: active_count=${slate.active_count} cards=${slate.cards.length} visible_slots=${slate.visible_slots} slot_seconds=${slate.slot_seconds} (build+xfer ${slateMs}ms)`);

    const browser = await chromium.launch({ args: ['--enable-precise-memory-info'] });
    try {
      const bctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, permissions: [] });
      await bctx.addInitScript(([t, u]) => {
        localStorage.setItem('weespas_token', t); localStorage.setItem('weespas_user', u);
      }, [weespasToken, JSON.stringify(weespasUser)]);
      const page = await bctx.newPage();

      // Install instrumentation BEFORE navigation so we capture from first paint.
      await page.addInitScript(() => {
        window.__perf = { longTasks: 0, longTaskMs: 0, maxTaskMs: 0, frames: 0, droppedFrames: 0, lastFrame: 0 };
        try {
          const po = new PerformanceObserver((list) => {
            for (const e of list.getEntries()) {
              window.__perf.longTasks++;
              window.__perf.longTaskMs += e.duration;
              if (e.duration > window.__perf.maxTaskMs) window.__perf.maxTaskMs = e.duration;
            }
          });
          po.observe({ entryTypes: ['longtask'] });
        } catch (_) { /* longtask unsupported */ }
        const tick = (ts) => {
          const p = window.__perf;
          if (p.lastFrame) {
            const d = ts - p.lastFrame;
            p.frames++;
            if (d > 50) p.droppedFrames++;   // > 3 frames @60Hz = visible hitch
          }
          p.lastFrame = ts;
          requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
      });

      await page.goto(`${FE}/trade`, { waitUntil: 'networkidle' });
      await page.waitForSelector('.trending-rail__card', { timeout: 15000 });
      await page.mouse.move(1435, 895);   // park off the rail so rotation isn't paused

      const heap0 = await page.evaluate(() => performance.memory ? performance.memory.usedJSHeapSize : null);
      const cards0 = await page.$$eval('.trending-rail__card', els => els.length);

      // Sample visible titles every 1s; track flip independence + queue coverage.
      const seen = new Set();
      const counts = new Set();
      let prev = null, flipMoments = 0, totalFlips = 0, maxFlips = 0, whole = 0;
      for (let i = 0; i < LOOP_S; i++) {
        const titles = await page.$$eval('.trending-rail__name', els => els.map(e => e.textContent));
        counts.add(titles.length);
        titles.forEach(t => seen.add(t));
        if (prev) {
          let flips = 0;
          for (let k = 0; k < Math.min(prev.length, titles.length); k++) if (prev[k] !== titles[k]) flips++;
          if (flips > 0) { flipMoments++; totalFlips += flips; maxFlips = Math.max(maxFlips, flips); }
          if (flips === titles.length && titles.length > 0) whole++;
        }
        prev = titles;
        await page.waitForTimeout(1000);
      }

      const perf = await page.evaluate(() => window.__perf);
      const heap1 = await page.evaluate(() => performance.memory ? performance.memory.usedJSHeapSize : null);
      const cards1 = await page.$$eval('.trending-rail__card', els => els.length);
      const mb = (b) => b == null ? 'n/a' : (b / 1048576).toFixed(1) + 'MB';

      console.log('\n=== EFFICIENCY @ ' + TARGET + ' boosted listings, ' + LOOP_S + 's loop ===');
      console.log(`visible slots: start=${cards0} end=${cards1} sampledCounts={${[...counts].sort((a,b)=>a-b).join(',')}}  (fixed-slot invariant: should be one stable value)`);
      console.log(`rotation: flipMoments=${flipMoments}/${LOOP_S - 1}  totalFlips=${totalFlips}  maxFlipsInOneTick=${maxFlips}  wholeBoardFlips=${whole} (want 0)`);
      console.log(`queue coverage: ${seen.size} distinct products surfaced of ${slate.cards.length} queued (fairness via skip-visible pointer)`);
      console.log(`long tasks (≥50ms): count=${perf.longTasks}  total=${perf.longTaskMs.toFixed(0)}ms  worst=${perf.maxTaskMs.toFixed(0)}ms`);
      console.log(`frames: ${perf.frames} observed, droppedFrames(>50ms)=${perf.droppedFrames} (${(100*perf.droppedFrames/Math.max(1,perf.frames)).toFixed(2)}%)`);
      console.log(`JS heap: start=${mb(heap0)} end=${mb(heap1)} delta=${heap0&&heap1?mb(heap1-heap0):'n/a'} (leak check across ~${totalFlips} swaps)`);

      await page.screenshot({ path: '/tmp/trending-perf.png', clip: { x: 0, y: 0, width: 1440, height: 760 } });
      console.log('screenshot → /tmp/trending-perf.png');
      await bctx.close();
    } finally { await browser.close(); }
  } finally {
    process.stdout.write('cleaning up grants… ');
    let revoked = 0;
    for (const g of grants) {
      const r = await ctx.delete(`${COMMERCE_API}/boosts/${g.id}`, { headers: authH(g.tok) });
      if (r.ok()) revoked++;
    }
    console.log(`revoked ${revoked}/${grants.length}`);
    await ctx.dispose();
  }
}
main().catch(e => { console.error(e); process.exit(1); });
