// Mint weespas-style RS256 mobility tokens for the live-server e2e, using Node's built-in crypto
// (no jsonwebtoken dependency). The private key is the shared dev keypair the mobility server
// verifies with its public half (dev/keys/insar_jwt_public.pem) — exactly the asymmetric-stateless
// trust the service expects. A token minted here is byte-compatible with a genuine weespas-issued
// one, so the e2e exercises the REAL auth path (audience guard + dispatch:eligible scope), not a
// bypass.
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const PRIVATE_KEY = fs.readFileSync(
  path.resolve(__dirname, '../../dev/keys/insar_jwt_private.pem'),
);

const b64url = (obj) => Buffer.from(JSON.stringify(obj)).toString('base64url');

// The mobility audience scope the service's guard (core/auth.py) admits, and the granular KYC scope
// the matcher requires before a driver is dispatchable (doc §16).
const MOBILITY_SCOPE = 'mobility_dispatch';
const DISPATCH_ELIGIBLE = 'dispatch:eligible';

// scopes: granular permissions array. Always carries the mobility audience scope so the service's
// audience guard admits it. ttlSec default 10 min.
function mint(sub, scopes = [], { ttlSec = 600, role = 'user', name } = {}) {
  const header = b64url({ alg: 'RS256', typ: 'JWT' });
  const payload = b64url({
    sub,
    role,
    ...(name ? { name } : {}),
    scope: MOBILITY_SCOPE,
    scopes,
    exp: Math.floor(Date.now() / 1000) + ttlSec,
  });
  const signingInput = `${header}.${payload}`;
  const sig = crypto
    .sign('RSA-SHA256', Buffer.from(signingInput), PRIVATE_KEY)
    .toString('base64url');
  return `${signingInput}.${sig}`;
}

// A rider (audience scope only — may request rides + hold their own SSE channel) and a KYC-passed
// driver (adds dispatch:eligible — the only kind the matcher dispatches to).
const rider = (sub, name) => mint(sub, [], { name });
const driver = (sub, name) => mint(sub, [DISPATCH_ELIGIBLE], { name });
// An on-shift but NOT-yet-KYC'd driver — pings a position but must never receive a dispatch.
const unverifiedDriver = (sub, name) => mint(sub, [], { name });

module.exports = { mint, rider, driver, unverifiedDriver, MOBILITY_SCOPE, DISPATCH_ELIGIBLE };
