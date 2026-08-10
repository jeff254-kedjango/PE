// Mint weespas-style RS256 commerce tokens for the live-server e2e, using Node's built-in
// crypto (no jsonwebtoken dependency). The private key is the dev keypair the commerce server
// verifies with its public half — exactly the asymmetric-stateless trust the service expects.
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const PRIVATE_KEY = fs.readFileSync(
  path.resolve(__dirname, '../../dev/keys/insar_jwt_private.pem'),
);

const b64url = (obj) => Buffer.from(JSON.stringify(obj)).toString('base64url');

// scopes: granular permissions array. Always carries the commerce_trade audience scope so the
// service's audience guard (S2) admits it. ttlSec default 10 min.
function mint(sub, scopes = ['read:feed'], { ttlSec = 600, role = 'user' } = {}) {
  const header = b64url({ alg: 'RS256', typ: 'JWT' });
  const payload = b64url({
    sub,
    role,
    scope: 'commerce_trade',
    scopes,
    exp: Math.floor(Date.now() / 1000) + ttlSec,
  });
  const signingInput = `${header}.${payload}`;
  const sig = crypto
    .sign('RSA-SHA256', Buffer.from(signingInput), PRIVATE_KEY)
    .toString('base64url');
  return `${signingInput}.${sig}`;
}

// A buyer (audience scope only) and a seller (adds create:trades) — the two roles the flows need.
const buyer = (sub) => mint(sub, ['read:feed']);
const seller = (sub) => mint(sub, ['read:feed', 'create:trades']);
// A staff principal (role='staff') — platform moderation (e.g. hiding an abusive comment).
const staff = (sub) => mint(sub, ['read:feed'], { role: 'staff' });

// A Weespas INSAR-TELEMETRY-scoped token — the ONLY credential the stateless InSAR SPA holds
// (handed to it on the "Risk Map" deep-link as ?wt=). It has a DIFFERENT scope ('insar_telemetry',
// not 'commerce_trade'), so it is minted directly here rather than via mint() (whose payload pins
// the commerce audience). Signed RS256 with the SAME dev keypair weespas mints with and the InSAR
// access-gate / aggregator verify against (auth_service.create_insar_telemetry_token) — so a token
// minted here is byte-compatible with a genuine weespas-issued one. Used by the §8.1a
// shops-on-map e2e to (a) pass the InSAR access gate and (b) call the shops aggregator.
function telemetry(sub, { ttlSec = 600, role = 'user' } = {}) {
  const header = b64url({ alg: 'RS256', typ: 'JWT' });
  const payload = b64url({
    sub,
    role,
    scope: 'insar_telemetry',
    exp: Math.floor(Date.now() / 1000) + ttlSec,
  });
  const signingInput = `${header}.${payload}`;
  const sig = crypto
    .sign('RSA-SHA256', Buffer.from(signingInput), PRIVATE_KEY)
    .toString('base64url');
  return `${signingInput}.${sig}`;
}

// Run-scoped teardown. The write-path e2e create REAL shops/listings in the live commerce DB; this
// removes exactly the rows a run created (keyed by its unique RUN id) so the loop leaves the buyer
// feed clean instead of accumulating fake products. Delegates to e2e/cleanup_run.py (dev-only,
// FK-safe, guarded) via the commerce venv — the same DB the server uses, out-of-band (no HTTP).
//
// Register it ONCE per script, right after RUN is defined:
//     registerCleanup(RUN);
// It runs on EVERY exit path — normal end, process.exit(1) on a failed check, or an uncaught throw —
// because it hooks process 'exit'. Synchronous by necessity (the 'exit' event forbids async work),
// which is fine: it's a single short DB call. A cleanup failure is reported but never flips a green
// run red (the test's own result stands); residue from a failed cleanup is caught by the next run.
const { spawnSync } = require('child_process');
const COMMERCE_ROOT = path.resolve(__dirname, '..');           // PE/commerce
const VENV_PY = path.resolve(COMMERCE_ROOT, '.venv/bin/python');

function cleanupRun(run) {
  if (!run || typeof run !== 'string' || run.length < 6) return;
  const r = spawnSync(VENV_PY, [path.join(__dirname, 'cleanup_run.py'), run], {
    cwd: COMMERCE_ROOT,
    env: { ...process.env, PYTHONPATH: '/home/jeff' },
    encoding: 'utf8',
  });
  const out = (r.stdout || '').trim();
  if (out) console.log(`  ⤺ ${out}`);
  if (r.status !== 0) console.error(`  ⚠ cleanup(${run}) exited ${r.status}: ${(r.stderr || '').trim()}`);
}

// Idempotent guard so a script that both registers the hook AND calls it explicitly never double-runs.
function registerCleanup(run) {
  let done = false;
  process.on('exit', () => { if (!done) { done = true; cleanupRun(run); } });
}

module.exports = { mint, buyer, seller, staff, telemetry, cleanupRun, registerCleanup };
