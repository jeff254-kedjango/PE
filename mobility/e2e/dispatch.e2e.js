/**
 * LIVE 2-ACTOR DISPATCH E2E — the §5 mobility spine, end to end, against the running :8004 server.
 *
 * Mobility has no frontend (it is a realtime backend spine), so the "always run a live e2e" rule is
 * satisfied by a Node fetch + SSE driver rather than Playwright — and it exercises the exact client
 * contract the eventual driver/rider app uses: POST to act, a long-lived fetch-stream to hear (the
 * §8.1b "fetch-stream, not EventSource" decision).
 *
 * The flow, over REAL HTTP + REAL Redis (db 4, the server's own DB):
 *   1. A KYC'd driver and an UNVERIFIED driver both open their SSE channels and both /ping a
 *      position ~70 m from the pickup.
 *   2. A rider POSTs /rides at the pickup.
 *   3. ASSERT: the KYC'd driver receives a `ride_request` frame with the correct ride_id, distance,
 *      and the rider's DISPLAY name — but NEVER the rider's sub (privacy). The unverified driver
 *      receives NOTHING (eligibility gate). drivers_matched === 1.
 *   4. Revocation: the rider is denied, re-POSTs /rides → 403 (action gate, fail-closed).
 *
 * Run-scoped teardown: every Redis member this run writes is keyed by a unique RUN id, and removed
 * on EVERY exit path — so the standing loop leaves db 4 exactly as it found it.
 *
 * Run (from anywhere):
 *   node PE/mobility/e2e/dispatch.e2e.js
 */
'use strict';
const { spawnSync } = require('child_process');
const path = require('path');
const { rider, driver, unverifiedDriver } = require('./jwt');

const BASE = process.env.MOBILITY_API || 'http://127.0.0.1:8004';
const API = `${BASE}/api/v1/dispatch`;
const MOBILITY_ROOT = path.resolve(__dirname, '..');
const VENV_PY = path.resolve(MOBILITY_ROOT, '.venv/bin/python');

// Unique per run so parallel/repeat runs never collide, and teardown removes only our own members.
const RUN = `e2e-${process.pid}-${Date.now().toString(36)}`;
const DRIVER_SUB = `${RUN}-driver`;
const UNVERIFIED_SUB = `${RUN}-unverified`;
const RIDER_SUB = `${RUN}-rider`;

// Pickup + two nearby driver positions (~70 m NE of pickup — well inside the default 3 km radius).
const PICKUP = { lat: -1.29207, lng: 36.82195 };          // Nairobi CBD
const NEARBY = { lat: -1.29157, lng: 36.82245 };

let failed = false;
function check(cond, label) {
  if (cond) { console.log(`  ✓ ${label}`); }
  else { console.error(`  ✗ ${label}`); failed = true; }
}

// ── Run-scoped Redis teardown ────────────────────────────────────────────────────────────────
// Remove exactly the members this run added from the live server's db 4. Synchronous (runs on the
// 'exit' event, which forbids async work) — a short one-shot python call over the mobility venv.
const CLEANUP_SUBS = [DRIVER_SUB, UNVERIFIED_SUB, RIDER_SUB];
const CLEANUP_PY = `
import asyncio
from PE.mobility.services.event_bus import get_client, aclose
async def main():
    c = get_client()
    subs = ${JSON.stringify(CLEANUP_SUBS)}
    pipe = c.pipeline(transaction=False)
    # pos + seen are GEO/ZSET (member removal = ZREM); eligible + denylist are plain sets (SREM).
    pipe.zrem("mobility:drivers:pos", *subs)
    pipe.zrem("mobility:drivers:seen", *subs)
    pipe.srem("mobility:drivers:eligible", *subs)
    pipe.srem("mobility:denylist", *subs)
    await pipe.execute()
    await aclose()
asyncio.run(main())
`;
let cleaned = false;
function cleanup() {
  if (cleaned) return;
  cleaned = true;
  const r = spawnSync(VENV_PY, ['-c', CLEANUP_PY], {
    cwd: MOBILITY_ROOT,
    env: { ...process.env, PYTHONPATH: '/home/jeff' },
    encoding: 'utf8',
  });
  if (r.status !== 0) console.error(`  ⚠ cleanup(${RUN}) exited ${r.status}: ${(r.stderr || '').trim()}`);
}
process.on('exit', cleanup);

// ── HTTP helpers ─────────────────────────────────────────────────────────────────────────────
async function post(pathname, token, body) {
  const res = await fetch(`${API}${pathname}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  let json = null;
  try { json = text ? JSON.parse(text) : null; } catch { /* leave null */ }
  return { status: res.status, json, text };
}

// Open an SSE channel and collect the FIRST data frame (skipping ": connected" / ": keep-alive"
// comment lines). Resolves null on timeout so a driver that SHOULD hear nothing is provable.
function collectFirstEvent(token, timeoutMs) {
  const controller = new AbortController();
  return new Promise(async (resolve) => {
    const timer = setTimeout(() => { controller.abort(); resolve(null); }, timeoutMs);
    try {
      const res = await fetch(`${API}/events`, {
        headers: { authorization: `Bearer ${token}`, accept: 'text/event-stream' },
        signal: controller.signal,
      });
      if (!res.ok) { clearTimeout(timer); resolve({ __httpError: res.status }); return; }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf('\n\n')) !== -1) {
          const frame = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          const dataLines = frame.split('\n').filter((l) => l.startsWith('data: '));
          if (dataLines.length) {
            const payload = dataLines.map((l) => l.slice(6)).join('\n');
            clearTimeout(timer);
            controller.abort();
            try { resolve(JSON.parse(payload)); } catch { resolve({ __raw: payload }); }
            return;
          }
          // else: ": connected" / ": keep-alive" comment — keep waiting.
        }
      }
      clearTimeout(timer);
      resolve(null);
    } catch (e) {
      clearTimeout(timer);
      if (e.name === 'AbortError') resolve(null);
      else { console.error(`  ⚠ SSE error: ${e.message}`); resolve(null); }
    }
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  console.log(`\n▶ mobility §5 dispatch e2e  (run ${RUN}, ${BASE})`);

  // Preflight: is the server up?
  try {
    const h = await fetch(`${BASE}/health`);
    if (!h.ok) throw new Error(`health ${h.status}`);
  } catch (e) {
    console.error(`  ⚠ mobility server not reachable at ${BASE} (${e.message}) — is it running on :8004?`);
    process.exit(2);
  }

  const driverTok = driver(DRIVER_SUB, 'Otieno');
  const unverifiedTok = unverifiedDriver(UNVERIFIED_SUB, 'Wanjiku');
  const riderTok = rider(RIDER_SUB, 'Amina');

  // 1. Both drivers ping a nearby position (server stamps eligibility from the token scope).
  const p1 = await post('/ping', driverTok, NEARBY);
  const p2 = await post('/ping', unverifiedTok, NEARBY);
  check(p1.status === 200 && p1.json && p1.json.ok, 'KYC driver ping accepted (200)');
  check(p2.status === 200 && p2.json && p2.json.ok, 'unverified driver ping accepted (200)');

  // Negative auth over the LIVE path: an unauthenticated /ping must be refused (401).
  const noAuth = await fetch(`${API}/ping`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(NEARBY),
  });
  check(noAuth.status === 401, `unauthenticated /ping refused (got ${noAuth.status})`);

  // 2. Open both driver SSE channels BEFORE the rider requests (so neither misses the publish).
  const driverHeard = collectFirstEvent(driverTok, 4000);
  const unverifiedHeard = collectFirstEvent(unverifiedTok, 2500);
  await sleep(300);  // let both subscriptions establish before publishing

  // 3. Rider requests a ride at the pickup.
  const ride = await post('/rides', riderTok, PICKUP);
  check(ride.status === 200, 'rider POST /rides accepted (200)');
  check(ride.json && ride.json.drivers_matched === 1, `only the KYC driver matched (drivers_matched=${ride.json && ride.json.drivers_matched})`);
  check(ride.json && ride.json.drivers_notified === 1, 'exactly one driver notified over the bus');
  const rideId = ride.json && ride.json.ride_id;

  // 4. Assert the KYC driver heard the dispatch — correct correlation, distance, name; NO rider sub.
  const evt = await driverHeard;
  check(evt && evt.kind === 'ride_request', 'KYC driver received a ride_request frame');
  check(evt && evt.ride_id === rideId, 'dispatch ride_id matches the /rides response');
  check(evt && evt.pickup && Math.abs(evt.pickup.lat - PICKUP.lat) < 1e-6, 'dispatch carries the pickup location');
  check(evt && typeof evt.distance_m === 'number' && evt.distance_m > 0 && evt.distance_m < 200, `dispatch distance is plausible (${evt && evt.distance_m} m)`);
  check(evt && evt.rider_name === 'Amina', 'dispatch carries the rider DISPLAY name');
  const leaked = evt && JSON.stringify(evt).includes(RIDER_SUB);
  check(!leaked, 'dispatch does NOT leak the rider sub (privacy)');

  // 5. The unverified driver heard NOTHING (eligibility gate).
  const noEvt = await unverifiedHeard;
  check(noEvt === null, 'unverified driver received no dispatch (eligibility gate)');

  // 6. Revocation: deny the rider, then a fresh /rides must be refused at the action gate.
  const dr = spawnSync(VENV_PY, ['-c',
    `import asyncio\nfrom PE.mobility.services.denylist import deny\nfrom PE.mobility.services.event_bus import aclose\nasync def m():\n    await deny(${JSON.stringify(RIDER_SUB)})\n    await aclose()\nasyncio.run(m())`,
  ], { cwd: MOBILITY_ROOT, env: { ...process.env, PYTHONPATH: '/home/jeff' }, encoding: 'utf8' });
  check(dr.status === 0, 'rider added to denylist (ops action)');
  const denied = await post('/rides', riderTok, PICKUP);
  check(denied.status === 403, `denied rider refused at action gate (got ${denied.status})`);

  console.log(failed ? '\n✗ dispatch e2e FAILED\n' : '\n✓ dispatch e2e PASSED\n');
  process.exit(failed ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
