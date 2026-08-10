/**
 * Trending rail + categories + comment moderation — live e2e over the REAL weespas→commerce bridge.
 *
 * Covers the §8.5 trending rail and §8.4 moderation end-to-end on the live PostGIS/RS256 stack
 * (the layer the unit/SQLite tests structurally can't reach), via the same bridge the Trade
 * frontend uses:
 *
 *   weespas /auth/login → weespas /commerce/session-token (RS256) → commerce /trending, /shops,
 *   /listings, /comments, PATCH /comments/{id}/hidden
 *
 * Asserts:
 *   - a LISTING boosted (sovereign) by a far seller surfaces in the buyer's trending slate as a
 *     PRODUCT card (listing_id + title + price + category + boost_tier), and NO PII (the seller's
 *     user id never appears); a SHOP-level boost in the same locality is EXCLUDED (feed-only);
 *   - the slate's visible_slots + slot_seconds (>5) + poll_seconds are present (the per-slot decay
 *     + polling contract the client renders);
 *   - comment moderation: the listing OWNER can hide a comment on their thread (vanishes from the
 *     thread + comment_count); a non-owner/non-staff buyer gets 404 (no existence leak); a STAFF
 *     principal can hide too; un-hide restores.
 *
 * Run:
 *   cd PE/commerce/e2e
 *   NODE_PATH=/home/jeff/PE/InSAR-Final-main/frontend/node_modules \
 *     WEESPAS_BASE_URL=http://127.0.0.1:8000 COMMERCE_BASE_URL=http://127.0.0.1:8003 \
 *     WEESPAS_EMAIL=admin@weespas.com WEESPAS_PASSWORD=admin123 \
 *     node trending.fe.e2e.js
 *
 * Same prerequisites as trade.fe.e2e.js (weespas :8000 with RS256 keys + commerce :8003).
 */
const { request } = require('playwright');
const { seller, buyer, staff, registerCleanup } = require('./jwt');

const WEESPAS = process.env.WEESPAS_BASE_URL || 'http://127.0.0.1:8000';
const COMMERCE = process.env.COMMERCE_BASE_URL || 'http://127.0.0.1:8003';
const WEESPAS_API = `${WEESPAS}/api/v1`;
const COMMERCE_API = `${COMMERCE}/api/v1`;
const EMAIL = process.env.WEESPAS_EMAIL || 'admin@weespas.com';
const PASSWORD = process.env.WEESPAS_PASSWORD || 'admin123';

const RUN = `trend-${Date.now()}`;
registerCleanup(RUN);   // remove this run's shops/listings from the live DB on exit (any path)
const NBO = { lat: -1.292, lng: 36.8219 };       // buyer (Nairobi)
const COAST = { lat: -4.0435, lng: 39.6682 };    // far seller (Mombasa, out of every radius tier)

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

  // 1) Bridge: weespas login → commerce session token (RS256), exactly as the FE does.
  let r = await ctx.post(`${WEESPAS_API}/auth/login`, {
    headers: { 'Content-Type': 'application/json' },
    data: { email: EMAIL, password: PASSWORD },
  });
  check('weespas login 200', r.ok(), `status ${r.status()}`);
  const weespasToken = (await r.json()).token;
  r = await ctx.get(`${WEESPAS_API}/commerce/session-token`, { headers: authH(weespasToken) });
  check('commerce session-token 200', r.ok(), `status ${r.status()}`);
  const session = (await r.json()).token;

  // 2) Seed a FAR shop with a category + a LISTING, then sovereign-boost the LISTING so it reaches
  //    the Nairobi buyer's trending slate (sovereign is nationwide). Far + sovereign is deliberate:
  //    trending shows boosted PRODUCTS reaching the locality, and a far product is unambiguously
  //    there only via the boost. ALSO sovereign-boost the SHOP itself to prove shop boosts are
  //    EXCLUDED from trending (they live only in the in-feed sponsored lane).
  const farTok = seller(`${RUN}-farseller`);
  r = await ctx.post(`${COMMERCE_API}/shops`, {
    headers: authH(farTok),
    data: { name: `Trend Coast Butchery ${RUN}`, lat: COAST.lat, lng: COAST.lng,
            display_name: 'Trend Coast', category: 'butchery' },
  });
  eq('seed far shop 201', r.status(), 201);
  const farShop = await r.json();
  eq('shop carries category', farShop.category, 'butchery');
  r = await ctx.post(`${COMMERCE_API}/shops/${farShop.id}/listings`, {
    headers: authH(farTok),
    // A video FIRST then an image — trending's image_url must SKIP the video and pick the image
    // (the #6 product-image behavior, exercised through the live API/cache path).
    data: {
      title: `Nyama Choma ${RUN}`, price_cents: 3500, stock_qty: 8,
      media_urls: [`/uploads/trade/videos/clip-${RUN}.mp4`, `/uploads/trade/images/nyama-${RUN}.webp`],
    },
  });
  eq('seed far listing 201', r.status(), 201);
  const farListing = await r.json();
  // The LISTING boost — this is what trending surfaces.
  r = await ctx.post(`${COMMERCE_API}/boosts`, {
    headers: authH(farTok),
    data: { target_type: 'listing', target_id: farListing.id, tier: 'sovereign' },
  });
  eq('far sovereign LISTING boost 201', r.status(), 201);
  const farGrantId = (await r.json()).id;
  // A SHOP boost in the same locality — must NOT appear in trending (feed-only).
  r = await ctx.post(`${COMMERCE_API}/boosts`, {
    headers: authH(farTok),
    data: { target_type: 'shop', target_id: farShop.id, tier: 'sovereign' },
  });
  eq('far sovereign SHOP boost 201', r.status(), 201);
  const farShopGrantId = (await r.json()).id;

  // 3) Trending slate via the bridge token — the boosted far LISTING must be in it as a product
  //    card (title + price + category + tier), and the per-slot decay contract present.
  //    The /trending endpoint caches one slate per locality bucket in Redis for `poll_seconds`
  //    (20s) — a shared discovery surface, eventually consistent by design. A grant created
  //    milliseconds ago is not guaranteed to be in a slate that was cached moments earlier, so a
  //    single immediate read races the cache TTL (flaky ~3-in-4). Poll until the just-boosted card
  //    appears or one full TTL elapses; this asserts eventual visibility without weakening any of
  //    the downstream card.* checks (they run identically once the card is found).
  const sleep = (ms) => new Promise((res) => setTimeout(res, ms));
  let slate;
  let card;
  const deadline = Date.now() + 25000; // > poll_seconds (20s) so at least one cache window turns over
  for (;;) {
    r = await ctx.get(`${COMMERCE_API}/trending?lat=${NBO.lat}&lng=${NBO.lng}`, { headers: authH(session) });
    check('trending via bridge 200', r.ok(), `status ${r.status()}`);
    slate = await r.json();
    card = Array.isArray(slate.cards) ? slate.cards.find((c) => c.listing_id === farListing.id) : undefined;
    if (card || Date.now() >= deadline) break;
    await sleep(2500);
  }
  check('slate has cards', Array.isArray(slate.cards));
  check('boosted far PRODUCT present in slate', !!card);
  if (card) {
    eq('card title', card.title, `Nyama Choma ${RUN}`);
    eq('card price_cents', card.price_cents, 3500);
    eq('card category', card.category, 'butchery');
    eq('card boost_tier', card.boost_tier, 'sovereign');
    // #6 — the card leads with the PRODUCT's own image, and the video is skipped for the still.
    eq('card image_url is the product image (video skipped)',
      card.image_url, `/uploads/trade/images/nyama-${RUN}.webp`);
  }
  // The SHOP boost must NOT have produced a card (trending is listing-only).
  check('shop-level boost excluded from trending',
    !slate.cards.some((c) => c.seller_id === farShop.seller_id && c.listing_id !== farListing.id));
  check('slate carries visible_slots', typeof slate.visible_slots === 'number' && slate.visible_slots >= 1);
  check('slate carries slot_seconds > 5 (readable per-card lifetime)',
    typeof slate.slot_seconds === 'number' && slate.slot_seconds > 5);
  check('slate carries poll_seconds (re-poll cadence)',
    typeof slate.poll_seconds === 'number' && slate.poll_seconds >= 1);

  // 4) NO PII: the far seller's user id must never appear anywhere in the slate payload, and each
  //    card must carry only the allow-listed fields.
  const slateText = JSON.stringify(slate);
  check('slate leaks no seller user-id', !slateText.includes(`${RUN}-farseller`));
  const allowed = ['listing_id', 'seller_id', 'title', 'price_cents', 'currency', 'category', 'property_uuid', 'distance_m', 'boost_tier', 'image_url'];
  check('cards expose only allow-listed fields',
    slate.cards.every((c) => Object.keys(c).every((k) => allowed.includes(k))),
    `keys=${JSON.stringify(slate.cards[0] ? Object.keys(slate.cards[0]) : [])}`);

  // 5) lat/lng bounds (S-input): out-of-range coords → 422.
  r = await ctx.get(`${COMMERCE_API}/trending?lat=200&lng=0`, { headers: authH(session) });
  eq('trending rejects out-of-range lat 422', r.status(), 422);
  // No token → 401 (fails closed).
  r = await ctx.get(`${COMMERCE_API}/trending?lat=${NBO.lat}&lng=${NBO.lng}`);
  eq('trending without token 401', r.status(), 401);

  // 6) COMMENT MODERATION. A near seller seeds a listing; a buyer comments; the listing OWNER hides
  //    it (own-your-thread); it vanishes from the thread + count; a non-owner buyer can't moderate
  //    (404, no leak); a STAFF principal can; un-hide restores.
  const nearTok = seller(`${RUN}-nearseller`);
  r = await ctx.post(`${COMMERCE_API}/shops`, {
    headers: authH(nearTok),
    data: { name: `Trend Near Shop ${RUN}`, lat: NBO.lat, lng: NBO.lng, display_name: 'Trend Near' },
  });
  eq('seed near shop 201', r.status(), 201);
  const nearShop = await r.json();
  r = await ctx.post(`${COMMERCE_API}/shops/${nearShop.id}/listings`, {
    headers: authH(nearTok),
    data: { title: `Trend Near Maize ${RUN}`, price_cents: 2000, stock_qty: 5 },
  });
  eq('seed near listing 201', r.status(), 201);
  const nearListing = await r.json();

  // A buyer (the bridge identity) posts a public comment.
  r = await ctx.post(`${COMMERCE_API}/listings/${nearListing.id}/comments`, {
    headers: { ...authH(session), 'Content-Type': 'application/json' },
    data: { body: `Moderate me ${RUN}` },
  });
  eq('post comment 201', r.status(), 201);
  const comment = await r.json();

  const threadHas = async () => {
    const tr = await ctx.get(`${COMMERCE_API}/listings/${nearListing.id}/comments`, { headers: authH(session) });
    return (await tr.json()).items.some((c) => c.id === comment.id);
  };
  check('comment visible before moderation', await threadHas());

  // A random buyer (not staff, doesn't own the listing) cannot moderate → 404 (no existence leak).
  r = await ctx.patch(`${COMMERCE_API}/comments/${comment.id}/hidden`, {
    headers: { ...authH(buyer(`${RUN}-randombuyer`)), 'Content-Type': 'application/json' },
    data: { hidden: true },
  });
  eq('non-owner non-staff moderation 404', r.status(), 404);
  check('comment still visible after blocked attempt', await threadHas());

  // The listing OWNER (near seller) hides it → 204; gone from the thread.
  r = await ctx.patch(`${COMMERCE_API}/comments/${comment.id}/hidden`, {
    headers: { ...authH(nearTok), 'Content-Type': 'application/json' },
    data: { hidden: true },
  });
  eq('owner hides comment 204', r.status(), 204);
  check('comment hidden from thread', !(await threadHas()));
  // And dropped from the feed comment_count.
  r = await ctx.get(`${COMMERCE_API}/feed?lat=${NBO.lat}&lng=${NBO.lng}&radius_m=2000`, { headers: authH(session) });
  const fitem = (await r.json()).items.find((i) => i.id === nearListing.id);
  check('hidden comment excluded from comment_count', fitem && fitem.comment_count === 0, `count=${fitem && fitem.comment_count}`);

  // A STAFF principal can un-hide it → restored.
  r = await ctx.patch(`${COMMERCE_API}/comments/${comment.id}/hidden`, {
    headers: { ...authH(staff(`${RUN}-mod`)), 'Content-Type': 'application/json' },
    data: { hidden: false },
  });
  eq('staff un-hides comment 204', r.status(), 204);
  check('comment restored to thread', await threadHas());
  // Moderating a nonexistent comment → 404.
  r = await ctx.patch(`${COMMERCE_API}/comments/does-not-exist/hidden`, {
    headers: { ...authH(staff(`${RUN}-mod`)), 'Content-Type': 'application/json' },
    data: { hidden: true },
  });
  eq('moderate nonexistent comment 404', r.status(), 404);

  // 7) Cleanup: revoke both seeded boosts so the shared PostGIS DB doesn't accumulate boosts.
  r = await ctx.delete(`${COMMERCE_API}/boosts/${farGrantId}`, { headers: authH(farTok) });
  eq('cleanup revoke far listing boost 204', r.status(), 204);
  r = await ctx.delete(`${COMMERCE_API}/boosts/${farShopGrantId}`, { headers: authH(farTok) });
  eq('cleanup revoke far shop boost 204', r.status(), 204);

  await ctx.dispose();
  console.log(`\n${passed} checks passed, ${failures.length} failed`);
  if (failures.length) { console.error('\nFAILURES:\n  - ' + failures.join('\n  - ')); process.exit(1); }
}

main().catch((e) => { console.error('trending e2e crashed:', e); process.exit(1); });
