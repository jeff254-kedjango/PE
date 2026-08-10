/**
 * FE-1 live e2e — the BUYER FEED data path the Trade frontend actually uses.
 *
 * Unlike commerce.e2e.js (which mints commerce tokens in-process with the dev private key), this
 * exercises the REAL weespas→commerce bridge end-to-end over HTTP, exactly as the weespas
 * frontend does:
 *
 *   weespas /auth/login (email+password)  →  weespas /commerce/session-token (mints RS256)
 *     →  commerce /feed + /sellers/{id}/storefront  (verifies RS256 with the public key only)
 *
 * This is the layer that catches cross-service auth/config bugs the per-service tests can't — e.g.
 * the bridge minting HS256 (RS256 disabled) which the commerce verifier rejects. It seeds a
 * sponsored far-listing through a SECOND, in-process commerce token (a far seller), then asserts
 * the bridge-authed buyer sees it labelled sponsored — proving the §8.3 lane over the real path.
 *
 * Run:
 *   cd PE/commerce/e2e
 *   NODE_PATH=/home/jeff/PE/InSAR-Final-main/frontend/node_modules \
 *     WEESPAS_BASE_URL=http://127.0.0.1:8000 COMMERCE_BASE_URL=http://127.0.0.1:8003 \
 *     WEESPAS_EMAIL=admin@weespas.com WEESPAS_PASSWORD=admin123 \
 *     node trade.fe.e2e.js
 */
const { request } = require('playwright');
const { seller, registerCleanup } = require('./jwt');

const WEESPAS = process.env.WEESPAS_BASE_URL || 'http://127.0.0.1:8000';
const COMMERCE = process.env.COMMERCE_BASE_URL || 'http://127.0.0.1:8003';
const WEESPAS_API = `${WEESPAS}/api/v1`;
const COMMERCE_API = `${COMMERCE}/api/v1`;
const EMAIL = process.env.WEESPAS_EMAIL || 'admin@weespas.com';
const PASSWORD = process.env.WEESPAS_PASSWORD || 'admin123';

const RUN = `fe1-${Date.now()}`;
registerCleanup(RUN);   // remove this run's shops/listings from the live DB on exit (any path)
const NBO = { lat: -1.292, lng: 36.8219 };          // buyer (Nairobi)
const COAST = { lat: -4.0435, lng: 39.6682 };       // far seller (Mombasa, out of every radius)
// A REMOTE, EMPTY locality (Lodwar, NW Kenya) far from ALL real + test data. Querying the sponsored
// lane from here is deterministic: with no organic content to contend for the bounded slots, the
// empty-organic floor surfaces the nationwide (sovereign) boosts on their own — so this run's own
// sovereign listing is guaranteed to appear, regardless of real shops' closer boosts in Nairobi
// (which correctly out-rank a distant test listing when the buyer is IN Nairobi).
const REMOTE = { lat: 3.1191, lng: 35.5970 };
// An ISOLATED empty locality (NE Kenya desert) far from ALL real + test data AND from REMOTE, used
// to prove the auto-widen contract deterministically: probing here at the 2 km default finds nothing
// in the immediate radius, so the feed must widen ONCE to the server max and surface the nearest
// content with an honest distance. Its own seeded shop sits ~5 km north (0.045° lat ≈ 5 km). Kept
// clear of REMOTE (>200 km away) so seeding here never pollutes REMOTE's sponsored-floor assertion.
const WIDEN_BUYER = { lat: 2.3, lng: 40.0 };
const WIDEN_SHOP = { lat: 2.345, lng: 40.0 };

let passed = 0;
const failures = [];
const check = (name, cond, detail = '') => {
  if (cond) { passed += 1; console.log(`  ✓ ${name}`); }
  else { failures.push(`${name}${detail ? ` — ${detail}` : ''}`); console.log(`  ✗ ${name}${detail ? ` — ${detail}` : ''}`); }
};
const eq = (name, a, b) => check(name, a === b, `expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
const authH = (t) => ({ Authorization: `Bearer ${t}` });

async function main() {
  const ctx = await request.newContext();

  // 1) Weespas login (email+password → token directly, no OTP).
  let r = await ctx.post(`${WEESPAS_API}/auth/login`, {
    headers: { 'Content-Type': 'application/json' },
    data: { email: EMAIL, password: PASSWORD },
  });
  check('weespas login 200', r.ok(), `status ${r.status()}`);
  const weespasToken = (await r.json()).token;
  check('weespas token present', typeof weespasToken === 'string' && weespasToken.length > 20);

  // 2) The bridge: mint a commerce-scoped session token. THIS is where the HS256-vs-RS256 bug
  //    lived — assert the minted token is RS256 (the alg commerce will accept).
  r = await ctx.get(`${WEESPAS_API}/commerce/session-token`, { headers: authH(weespasToken) });
  check('commerce session-token 200', r.ok(), `status ${r.status()}`);
  const session = await r.json();
  check('session has commerce_url', typeof session.commerce_url === 'string');
  const alg = JSON.parse(Buffer.from(session.token.split('.')[0], 'base64').toString()).alg;
  eq('bridge mints RS256 (commerce-acceptable)', alg, 'RS256');

  // 3) Seed a far, Sovereign-boosted listing via a SEPARATE in-process commerce token (a distinct
  //    far seller). This is test scaffolding for the sponsored lane — independent of the buyer.
  const farTok = seller(`${RUN}-farseller`);
  let far = await ctx.post(`${COMMERCE_API}/shops`, {
    headers: authH(farTok),
    data: { name: 'FE Coast Shop', lat: COAST.lat, lng: COAST.lng, display_name: 'FE Coast' },
  });
  eq('seed far shop 201', far.status(), 201);
  const farShop = await far.json();
  far = await ctx.post(`${COMMERCE_API}/shops/${farShop.id}/listings`, {
    headers: authH(farTok),
    data: { title: `FE Coast Mangoes ${RUN}`, price_cents: 3000, stock_qty: 8, pricing_mode: 'fixed' },
  });
  eq('seed far listing 201', far.status(), 201);
  const farListing = await far.json();
  far = await ctx.post(`${COMMERCE_API}/boosts`, {
    headers: authH(farTok),
    data: { target_type: 'listing', target_id: farListing.id, tier: 'sovereign' },
  });
  eq('far sovereign boost 201', far.status(), 201);
  const farGrantId = (await far.json()).id;

  // 4) The buyer feed THROUGH THE BRIDGE TOKEN (the real FE path). Two things to prove:
  //    (a) the RS256 bridge authenticates at all — a 200 from a Nairobi buyer is enough; and
  //    (b) this run's Sovereign-boosted far listing actually reaches a distant buyer, labelled
  //        sponsored. For (b) we query from a REMOTE empty locality (Lodwar), NOT Nairobi: a
  //        nationwide boost must reach everyone, but when the buyer is IN Nairobi the platform
  //        correctly ranks real Nairobi shops' own closer boosts ahead of a distant test listing
  //        for the bounded sponsored slots. Lodwar has no local organic content, so the
  //        empty-organic floor surfaces the nationwide sovereigns on their own — deterministic
  //        regardless of what real sellers are boosting elsewhere.
  r = await ctx.get(`${COMMERCE_API}/feed?lat=${NBO.lat}&lng=${NBO.lng}&radius_m=2000`, {
    headers: authH(session.token),
  });
  check('feed via bridge token 200', r.ok(), `status ${r.status()} (RS256 bridge auth)`);

  r = await ctx.get(`${COMMERCE_API}/feed?lat=${REMOTE.lat}&lng=${REMOTE.lng}&radius_m=2000`, {
    headers: authH(session.token),
  });
  check('remote feed via bridge token 200', r.ok(), `status ${r.status()}`);
  const feed = await r.json();
  check('remote feed returns items (the nationwide sponsored floor)',
    Array.isArray(feed.items) && feed.items.length > 0, `${feed.items?.length} items`);
  const sponsored = feed.items.find((i) => i.id === farListing.id);
  check('this run\'s sovereign-boosted listing reaches the remote buyer', !!sponsored);
  if (sponsored) {
    eq('flagged is_sponsored', sponsored.is_sponsored, true);
    eq('boost_tier sovereign', sponsored.boost_tier, 'sovereign');
  }
  // Every item the empty-organic floor surfaces is a labelled sponsored item (the lane is pure —
  // no organic content exists this far out to be mislabelled).
  check('remote floor carries only labelled sponsored items',
    feed.items.every((i) => i.is_sponsored === true));

  // 5) Public storefront via the bridge token (the card→seller tap path).
  r = await ctx.get(`${COMMERCE_API}/sellers/${farShop.seller_id}/storefront`, {
    headers: authH(session.token),
  });
  eq('storefront via bridge 200', r.status(), 200);
  const sf = await r.json();
  const titles = sf.shops.flatMap((s) => s.listings.map((l) => l.title));
  check('storefront lists the seeded item', titles.some((t) => t.includes(RUN)));
  // No POS-internal leak in the public DTO (the buyer card never sees stock internals).
  const leaked = ['stock_qty', 'low_stock_threshold', 'is_active', 'intent_weight']
    .filter((k) => sf.shops[0]?.listings[0] && k in sf.shops[0].listings[0]);
  check('no POS-internal leak in storefront', leaked.length === 0, `leaked=${JSON.stringify(leaked)}`);

  // 6) §8 SOCIAL FEED through the bridge token: a NEAR seller seeds an ordinary listing + a
  //    short-video post; the bridge-authed buyer comments (public thread) and uses the
  //    Listings|Videos toggle (?kind=). Seeded via an in-process near-seller token (the buyer is
  //    the bridge identity — what the FE actually does).
  const nearTok = seller(`${RUN}-nearseller`);
  let near = await ctx.post(`${COMMERCE_API}/shops`, {
    headers: authH(nearTok),
    data: { name: 'FE Near Shop', lat: NBO.lat, lng: NBO.lng, display_name: 'FE Near' },
  });
  eq('seed near shop 201', near.status(), 201);
  const nearShop = await near.json();
  near = await ctx.post(`${COMMERCE_API}/shops/${nearShop.id}/listings`, {
    headers: authH(nearTok),
    data: { title: `FE Near Maize ${RUN}`, price_cents: 2000, stock_qty: 5 },
  });
  eq('seed near listing 201', near.status(), 201);
  const nearListing = await near.json();
  near = await ctx.post(`${COMMERCE_API}/shops/${nearShop.id}/listings`, {
    headers: authH(nearTok),
    data: { title: `FE Near Reel ${RUN}`, price_cents: 2500, stock_qty: 5, is_short_video: true },
  });
  eq('seed near reel 201', near.status(), 201);
  const nearReel = await near.json();
  eq('reel flagged is_short_video', nearReel.is_short_video, true);

  // Buyer posts a PUBLIC comment via the bridge token, then reads the thread back.
  r = await ctx.post(`${COMMERCE_API}/listings/${nearListing.id}/comments`, {
    headers: { ...authH(session.token), 'Content-Type': 'application/json' },
    data: { body: 'Available this evening?' },
  });
  eq('post comment via bridge 201', r.status(), 201);
  const postedComment = await r.json();
  // The commenter's NAME (snapshotted from the bridge token's name claim) comes back — NOT the raw
  // user id. This is the cross-service identity-display fix: commerce owns no identity, so the name
  // rides the token and is stored at write time.
  check('comment carries a display name (not the raw uuid)',
    typeof postedComment.author_name === 'string'
    && postedComment.author_name.length > 0
    && postedComment.author_name !== postedComment.author_uuid,
    `author_name=${JSON.stringify(postedComment.author_name)} author_uuid=${postedComment.author_uuid}`);
  r = await ctx.get(`${COMMERCE_API}/listings/${nearListing.id}/comments`, { headers: authH(session.token) });
  const thread = await r.json();
  check('comment thread returns the posted comment', thread.items.some((c) => c.body === 'Available this evening?'));
  check('thread comment also carries the display name',
    thread.items.some((c) => c.body === 'Available this evening?' && c.author_name === postedComment.author_name));

  // comment_count surfaces on the feed item (display-only).
  r = await ctx.get(`${COMMERCE_API}/feed?lat=${NBO.lat}&lng=${NBO.lng}&radius_m=2000`, { headers: authH(session.token) });
  const withComment = (await r.json()).items.find((i) => i.id === nearListing.id);
  check('feed item carries comment_count >= 1', withComment && withComment.comment_count >= 1, `got ${withComment && withComment.comment_count}`);

  // Listings|Videos toggle (?kind=) over the bridge token.
  const kindIds = async (kind) => {
    const fr = await ctx.get(`${COMMERCE_API}/feed?lat=${NBO.lat}&lng=${NBO.lng}&radius_m=2000${kind ? `&kind=${kind}` : ''}`, { headers: authH(session.token) });
    return new Set((await fr.json()).items.map((i) => i.id));
  };
  const listOnly = await kindIds('listings');
  check('kind=listings excludes the reel', listOnly.has(nearListing.id) && !listOnly.has(nearReel.id));
  const vidOnly = await kindIds('videos');
  check('kind=videos keeps only the reel', vidOnly.has(nearReel.id) && !vidOnly.has(nearListing.id));
  r = await ctx.get(`${COMMERCE_API}/feed?lat=${NBO.lat}&lng=${NBO.lng}&kind=bogus`, { headers: authH(session.token) });
  eq('invalid kind 422 via bridge', r.status(), 422);

  // 6b) AUTO-WIDEN CONTRACT via the bridge token. Change A widened the trigger: the feed now widens
  //     when the immediate radius is THIN (fewer than one page — feed_sparse_threshold=20), not only
  //     when it is empty. The Nairobi buyer HAS near content but a sparse locality, so the honest
  //     invariant to assert here is the SPARSE branch, not "never widens": (1) the local listing is
  //     never dropped from the feed, and (2) immediate_count reflects the real local items (> 0),
  //     which is precisely what tells the client this is "only a few nearby" — NOT the empty-area
  //     branch ("nothing in your area"). We do NOT hard-assert widened's boolean: it is a pure
  //     function of (immediate_count < 20) and would flake only if >20 shops ever sat within 2 km of
  //     the CBD probe — so the count itself is the deterministic, meaningful signal.
  r = await ctx.get(`${COMMERCE_API}/feed?lat=${NBO.lat}&lng=${NBO.lng}&radius_m=2000`, { headers: authH(session.token) });
  const nboFeed = await r.json();
  check('near feed still surfaces the local listing (widen never drops local content)',
    nboFeed.items.some((i) => i.id === nearListing.id), `${nboFeed.items?.length} items`);
  check('near feed immediate_count is honest (>0 ⇒ sparse branch, never the empty-area claim)',
    nboFeed.immediate_count > 0, `got ${nboFeed.immediate_count}`);

  //     A buyer in an ISOLATED empty locality gets nothing in the 2 km default, so the feed widens
  //     ONCE to the server max and surfaces the ~5 km shop with an honest nearest_distance_m. This is
  //     the data behind the "closest shops are within X km" note (distance only — never delivery).
  const widenTok = seller(`${RUN}-widenseller`);
  let ws = await ctx.post(`${COMMERCE_API}/shops`, {
    headers: authH(widenTok),
    data: { name: 'FE Widen Shop', lat: WIDEN_SHOP.lat, lng: WIDEN_SHOP.lng, display_name: 'FE Widen' },
  });
  eq('seed widen shop 201', ws.status(), 201);
  const widenShop = await ws.json();
  ws = await ctx.post(`${COMMERCE_API}/shops/${widenShop.id}/listings`, {
    headers: authH(widenTok),
    data: { title: `FE Widen Beans ${RUN}`, price_cents: 1500, stock_qty: 4 },
  });
  eq('seed widen listing 201', ws.status(), 201);
  const widenListing = await ws.json();

  r = await ctx.get(`${COMMERCE_API}/feed?lat=${WIDEN_BUYER.lat}&lng=${WIDEN_BUYER.lng}&radius_m=2000`, { headers: authH(session.token) });
  const widenFeed = await r.json();
  eq('empty-radius feed widens', widenFeed.widened, true);
  // The EMPTY branch: immediate_count is exactly 0 here (isolated desert probe), which is what keys
  // the client to "nothing in your area" rather than the sparse-branch "only a few nearby". This is
  // the other side of the honesty split asserted in 6b.
  eq('empty-radius immediate_count is 0 (empty branch, not sparse)', widenFeed.immediate_count, 0);
  check('widened feed surfaces the ~5 km shop', widenFeed.items.some((i) => i.id === widenListing.id),
    `${widenFeed.items?.length} items`);
  check('nearest_distance_m is an honest ~5 km (4.5–5.5 km)',
    widenFeed.nearest_distance_m > 4500 && widenFeed.nearest_distance_m < 5500,
    `got ${widenFeed.nearest_distance_m}`);

  // 7) No token → 401 (commerce fails closed; the FE relies on this to bounce to login).
  r = await ctx.get(`${COMMERCE_API}/feed?lat=${NBO.lat}&lng=${NBO.lng}`);
  eq('feed without token 401', r.status(), 401);

  // 8) Cleanup: revoke the seeded boost so the shared PostGIS DB doesn't accumulate sponsored
  //    listings across reruns (the contamination that made the old over-broad assertion flap).
  r = await ctx.delete(`${COMMERCE_API}/boosts/${farGrantId}`, { headers: authH(farTok) });
  eq('cleanup revoke far boost 204', r.status(), 204);

  await ctx.dispose();
  console.log(`\n${passed} checks passed, ${failures.length} failed`);
  if (failures.length) { console.error('\nFAILURES:\n  - ' + failures.join('\n  - ')); process.exit(1); }
}

main().catch((e) => { console.error('fe e2e crashed:', e); process.exit(1); });
