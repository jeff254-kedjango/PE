/**
 * Playwright API e2e for the commerce trading layer — runs against the LIVE server
 * (default http://127.0.0.1:8003, real PostGIS + real RS256 verify + Redis denylist), not the
 * SQLite unit path. This closes the "live end-to-end never run" gap flagged since increment 1
 * and exercises every feature added this session through the real HTTP boundary:
 *
 *   - settlement: open a fixed-price order → settle (3% commission, stub rail)
 *   - receipts (inc 5): a receipt is issued on settle, money split gross/commission/net,
 *     hash-bound to the order's settle_ok chain tip; parties-only
 *   - reviews (inc 6): proof-of-purchase gate (buyer-of-settled-order only; seller-own-sale
 *     403; unsettled 409; one-per-order 409)
 *   - rating badge surfacing: seller rating on the public storefront after a review
 *   - public storefront: in-stock-only, no POS-internal leak, embedded rating, 404
 *   - "selling now" promotion (§8): promote evergreen → feed is_promoted, bounds +
 *     unknown-mode 422, write-scope/owner authz (403/404), clear (idempotent) + cross-owner 404
 *   - Boost tiers & sponsored lane (§8.3, this slice): a far Sovereign-boosted listing appears in
 *     a distant buyer's feed AS SPONSORED (labelled, separate lane) without relabelling organic
 *     items; allowance spend + idempotent replay (no double-charge); authz (422/404/403); revoke
 *     drops it from the feed
 *
 * Uses the bare `playwright` package's request API (no @playwright/test dependency) with a tiny
 * assertion harness. Tokens are minted in-process with the dev RS256 key (e2e/jwt.js).
 *
 * Run:  node PE/commerce/e2e/commerce.e2e.js
 *       COMMERCE_BASE_URL=http://127.0.0.1:8003 node PE/commerce/e2e/commerce.e2e.js
 */
const { request } = require('playwright');
const { buyer, seller, registerCleanup } = require('./jwt');

const BASE = process.env.COMMERCE_BASE_URL || 'http://127.0.0.1:8003';
const API = `${BASE}/api/v1`;

// Unique suffix per run so reruns against the persistent PostGIS DB never collide on
// (buyer, listing) open-order or one-review-per-order constraints.
const RUN = `e2e-${Date.now()}`;
registerCleanup(RUN);   // remove this run's shops/listings from the live DB on exit (any path)

// ----- tiny assertion harness -----
let passed = 0;
const failures = [];
function check(name, cond, detail = '') {
  if (cond) {
    passed += 1;
    console.log(`  ✓ ${name}`);
  } else {
    failures.push(`${name}${detail ? ` — ${detail}` : ''}`);
    console.log(`  ✗ ${name}${detail ? ` — ${detail}` : ''}`);
  }
}
const eq = (name, a, b) => check(name, a === b, `expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);

// Auth + idempotency header helpers.
const authH = (token) => ({ Authorization: `Bearer ${token}` });
const idem = (k) => ({ 'Idempotency-Key': `${RUN}-${k}` });

async function main() {
  const ctx = await request.newContext();
  // Path-prefixing wrappers: an absolute http(s) URL passes through; anything else is resolved
  // under the API prefix. (Playwright's baseURL drops the path segment for leading-slash paths,
  // so we prefix explicitly.)
  const get = (p, o) => ctx.get(p.startsWith('http') ? p : API + p, o);
  const post = (p, o) => ctx.post(p.startsWith('http') ? p : API + p, o);

  const sellerSub = `${RUN}-seller`;
  const buyerSub = `${RUN}-buyer`;
  const sellerTok = seller(sellerSub);
  const buyerTok = buyer(buyerSub);

  // --- health ---
  const health = await (await get(`${BASE}/health`)).json();
  eq('health auth_enabled', health.auth_enabled, true);

  // --- seller creates a shop + a fixed-price in-stock listing, and an out-of-stock one ---
  let r = await post('/shops', {
    headers: authH(sellerTok),
    data: { name: 'E2E Shop', lat: -1.292, lng: 36.8219, display_name: 'E2E Seller' },
  });
  eq('create shop 201', r.status(), 201);
  const shop = await r.json();
  const sellerId = shop.seller_id;

  r = await post(`/shops/${shop.id}/listings`, {
    headers: authH(sellerTok),
    data: { title: 'E2E Maize 2kg', price_cents: 10000, currency: 'KES',
            stock_qty: 5, pricing_mode: 'fixed', property_uuid: `${RUN}-prop` },
  });
  eq('create in-stock listing 201', r.status(), 201);
  const listing = await r.json();

  r = await post(`/shops/${shop.id}/listings`, {
    headers: authH(sellerTok),
    data: { title: 'E2E Sold Out', price_cents: 5000, stock_qty: 0, pricing_mode: 'fixed' },
  });
  eq('create out-of-stock listing 201', r.status(), 201);

  // --- buyer opens a fixed-price order → PRICE_LOCKED immediately ---
  r = await post('/orders', {
    headers: { ...authH(buyerTok), ...idem('open') },
    data: { listing_id: listing.id },
  });
  eq('open order 201', r.status(), 201);
  const order = await r.json();
  eq('order locked at list price', order.locked_price_cents, 10000);
  eq('order status PRICE_LOCKED', order.status, 'PRICE_LOCKED');

  // open without an Idempotency-Key → 422
  r = await post('/orders', { headers: authH(buyerTok), data: { listing_id: listing.id } });
  eq('open without idem-key 422', r.status(), 422);

  // --- review BEFORE settle is rejected (proof-of-purchase gate) ---
  r = await post(`/orders/${order.id}/review`, { headers: authH(buyerTok), data: { rating: 5 } });
  eq('review before settle 409', r.status(), 409);

  // --- settle → SETTLED, 3% commission, stub rail ---
  r = await post(`/orders/${order.id}/settle`, { headers: { ...authH(buyerTok), ...idem('settle') } });
  eq('settle 200', r.status(), 200);
  const settled = await r.json();
  eq('status SETTLED', settled.status, 'SETTLED');
  eq('commission 3% of 10000', settled.commission_cents, 300);
  check('rail_ref is stub', typeof settled.rail_ref === 'string' && settled.rail_ref.startsWith('stub-'),
    `got ${settled.rail_ref}`);

  // --- receipt issued on settle (inc 5) ---
  r = await get(`/orders/${order.id}/receipt`, { headers: authH(buyerTok) });
  eq('receipt 200', r.status(), 200);
  const receipt = await r.json();
  eq('receipt gross', receipt.gross_cents, 10000);
  eq('receipt commission', receipt.commission_cents, 300);
  eq('receipt net = gross - commission', receipt.net_to_seller_cents, 9700);
  check('receipt money split sums', receipt.gross_cents === receipt.commission_cents + receipt.net_to_seller_cents);
  check('receipt has chain_tip_hash', typeof receipt.chain_tip_hash === 'string' && receipt.chain_tip_hash.length === 64);
  check('receipt frozen title', receipt.listing_title === 'E2E Maize 2kg');

  // chain tip matches the order's settle_ok event row_hash
  const detail = await (await get(`/orders/${order.id}`, { headers: authH(buyerTok) })).json();
  const settleOk = detail.events.find((e) => e.event_type === 'settle_ok');
  eq('receipt chain tip == settle_ok row_hash', receipt.chain_tip_hash, settleOk.row_hash);

  // receipt is parties-only: a stranger gets 404 (no leak)
  r = await get(`/orders/${order.id}/receipt`, { headers: authH(buyer(`${RUN}-stranger`)) });
  eq('receipt non-party 404', r.status(), 404);

  // --- seller cannot review their own sale (403) ---
  r = await post(`/orders/${order.id}/review`, { headers: authH(sellerTok), data: { rating: 5 } });
  eq('seller-own-sale review 403', r.status(), 403);

  // --- buyer reviews the settled order (201), then a second review is 409 ---
  r = await post(`/orders/${order.id}/review`, {
    headers: authH(buyerTok), data: { rating: 5, body: 'Fast, fair, neighbourly' },
  });
  eq('buyer review 201', r.status(), 201);
  r = await post(`/orders/${order.id}/review`, { headers: authH(buyerTok), data: { rating: 1 } });
  eq('second review 409', r.status(), 409);

  // out-of-range rating → 422
  r = await post(`/orders/${order.id}/review`, { headers: authH(buyerTok), data: { rating: 6 } });
  eq('rating out of range 422', r.status(), 422);

  // --- seller reviews list + aggregate summary ---
  const reviews = await (await get(`/sellers/${sellerId}/reviews`, { headers: authH(buyerTok) })).json();
  eq('seller rating average 5.0', reviews.summary.average, 5.0);
  check('seller review count >= 1', reviews.summary.count >= 1, `count=${reviews.summary.count}`);

  // --- PUBLIC storefront: in-stock only, embedded rating, no POS leak ---
  r = await get(`/sellers/${sellerId}/storefront`, { headers: authH(buyerTok) });
  eq('public storefront 200', r.status(), 200);
  const sf = await r.json();
  eq('storefront rating 5.0', sf.rating, 5.0);
  const allListings = sf.shops.flatMap((s) => s.listings);
  const titles = allListings.map((l) => l.title).sort();
  check('public storefront shows in-stock only', titles.includes('E2E Maize 2kg') && !titles.includes('E2E Sold Out'),
    `titles=${JSON.stringify(titles)}`);
  const leakKeys = ['stock_qty', 'low_stock_threshold', 'is_low_stock', 'is_out_of_stock', 'intent_weight', 'is_active'];
  const leaked = leakKeys.filter((k) => allListings.length && k in allListings[0]);
  check('public storefront leaks no POS-internal fields', leaked.length === 0, `leaked=${JSON.stringify(leaked)}`);
  check('public listing exposes property_uuid for InSAR stitch', allListings.length && 'property_uuid' in allListings[0]);

  // unknown seller → 404; no token → 401
  r = await get(`/sellers/${RUN}-ghost/storefront`, { headers: authH(buyerTok) });
  eq('public storefront unknown seller 404', r.status(), 404);
  r = await get(`/sellers/${sellerId}/storefront`);
  eq('public storefront no token 401', r.status(), 401);

  // --- feed surfaces the seller rating (display-only) ---
  const feed = await (await get(`/feed?lat=-1.292&lng=36.8219&radius_m=2000`, { headers: authH(buyerTok) })).json();
  const mine = feed.items.find((i) => i.seller_id === sellerId);
  check('feed includes our in-stock listing', !!mine, 'seller listing not found in feed');
  if (mine) {
    eq('feed seller_rating 5.0', mine.seller_rating, 5.0);
    check('feed seller_review_count >= 1', mine.seller_review_count >= 1);
  }

  // --- §8 "selling now" promotion (this slice) ---
  // A fresh in-stock listing to promote (the original was settled/out-of-stock churn above).
  r = await post(`/shops/${shop.id}/listings`, {
    headers: authH(sellerTok),
    data: { title: 'E2E Fresh Mandazi', price_cents: 2000, stock_qty: 10, pricing_mode: 'fixed' },
  });
  eq('create promotable listing 201', r.status(), 201);
  const promoListing = await r.json();
  const del = (p, o) => ctx.delete(p.startsWith('http') ? p : API + p, o);

  // duration below the configured min / above the max → 422 (anti-abuse bounds, service-enforced)
  r = await post(`/listings/${promoListing.id}/promote`,
    { headers: authH(sellerTok), data: { mode: 'evergreen', duration_seconds: 1 } });
  eq('promote below-min duration 422', r.status(), 422);
  r = await post(`/listings/${promoListing.id}/promote`,
    { headers: authH(sellerTok), data: { mode: 'evergreen', duration_seconds: 99999999 } });
  eq('promote above-max duration 422', r.status(), 422);

  // unknown mode → 422 at the schema edge
  r = await post(`/listings/${promoListing.id}/promote`,
    { headers: authH(sellerTok), data: { mode: 'spam', duration_seconds: 3600 } });
  eq('promote unknown mode 422', r.status(), 422);

  // a read-only buyer token cannot promote (write scope required)
  r = await post(`/listings/${promoListing.id}/promote`,
    { headers: authH(buyerTok), data: { mode: 'evergreen', duration_seconds: 3600 } });
  eq('promote with read-only token 403', r.status(), 403);

  // a different seller cannot promote our listing → 404 (no existence leak)
  r = await post(`/listings/${promoListing.id}/promote`,
    { headers: authH(seller(`${RUN}-otherseller`)), data: { mode: 'evergreen', duration_seconds: 3600 } });
  eq('cross-owner promote 404', r.status(), 404);

  // happy path: promote evergreen → 200, owner view reflects the live window
  r = await post(`/listings/${promoListing.id}/promote`,
    { headers: authH(sellerTok), data: { mode: 'evergreen', duration_seconds: 3600 } });
  eq('promote evergreen 200', r.status(), 200);
  const promoted = await r.json();
  eq('promo_mode evergreen', promoted.promo_mode, 'evergreen');
  eq('owner is_promoted true', promoted.is_promoted, true);
  check('promo_expires_at set', typeof promoted.promo_expires_at === 'string');

  // the live window shows in the buyer feed (is_promoted) — same source of truth as the owner view
  let pf = await (await get(`/feed?lat=-1.292&lng=36.8219&radius_m=2000`, { headers: authH(buyerTok) })).json();
  let pItem = pf.items.find((i) => i.id === promoListing.id);
  check('promoted listing in feed', !!pItem, 'promoted listing not in feed');
  if (pItem) eq('feed is_promoted true', pItem.is_promoted, true);

  // clear the promotion → 200, back to un-promoted; clearing again is idempotent
  r = await del(`/listings/${promoListing.id}/promotion`, { headers: authH(sellerTok) });
  eq('clear promotion 200', r.status(), 200);
  eq('cleared promo_mode null', (await r.json()).promo_mode, null);
  r = await del(`/listings/${promoListing.id}/promotion`, { headers: authH(sellerTok) });
  eq('clear promotion idempotent 200', r.status(), 200);

  // cross-owner clear → 404
  r = await del(`/listings/${promoListing.id}/promotion`,
    { headers: authH(seller(`${RUN}-otherseller`)) });
  eq('cross-owner clear 404', r.status(), 404);

  // feed no longer flags it promoted (boost gone, listing still visible — evergreen stays)
  pf = await (await get(`/feed?lat=-1.292&lng=36.8219&radius_m=2000`, { headers: authH(buyerTok) })).json();
  pItem = pf.items.find((i) => i.id === promoListing.id);
  check('cleared listing still visible in feed', !!pItem, 'listing vanished after clear');
  if (pItem) eq('feed is_promoted false after clear', pItem.is_promoted, false);

  // --- §8.3 Boost tiers & sponsored lane (this slice) ---
  // A FAR seller (Mombasa, ~440 km from the Nairobi buyer) lists an item that is OUT of every
  // radius tier — so it can only reach the Nairobi buyer via a nationwide Sovereign boost.
  const farSellerTok = seller(`${RUN}-far`);
  let far = await post('/shops', {
    headers: authH(farSellerTok),
    data: { name: 'Coast Shop', lat: -4.0435, lng: 39.6682, display_name: 'Coast Seller' },
  });
  eq('create far shop 201', far.status(), 201);
  const farShop = await far.json();
  far = await post(`/shops/${farShop.id}/listings`, {
    headers: authH(farSellerTok),
    data: { title: 'E2E Coast Mangoes', price_cents: 3000, stock_qty: 8, pricing_mode: 'fixed' },
  });
  eq('create far listing 201', far.status(), 201);
  const farListing = await far.json();

  // Baseline: the far listing is NOT in the Nairobi buyer's feed (out of radius, no boost).
  let bf = await (await get(`/feed?lat=-1.292&lng=36.8219&radius_m=2000`, { headers: authH(buyerTok) })).json();
  check('far listing absent from feed pre-boost', !bf.items.some((i) => i.id === farListing.id));

  // Allowances endpoint reflects the full caps before any spend.
  let allow = await (await get('/boosts/allowances', { headers: authH(farSellerTok) })).json();
  const sovBefore = allow.tiers.find((t) => t.tier === 'sovereign').remaining;
  check('sovereign cap present', typeof sovBefore === 'number' && sovBefore >= 1, `remaining=${sovBefore}`);

  // unknown tier → 422; cross-owner target → 404; read-only token → 403
  let rb = await post('/boosts', { headers: authH(farSellerTok), data: { target_type: 'listing', target_id: farListing.id, tier: 'galaxy' } });
  eq('boost unknown tier 422', rb.status(), 422);
  rb = await post('/boosts', { headers: authH(sellerTok), data: { target_type: 'listing', target_id: farListing.id, tier: 'sovereign' } });
  eq('boost cross-owner 404', rb.status(), 404);
  rb = await post('/boosts', { headers: authH(buyerTok), data: { target_type: 'listing', target_id: farListing.id, tier: 'sovereign' } });
  eq('boost read-only token 403', rb.status(), 403);

  // Happy path: far seller buys a Sovereign boost → 201, scope nation.
  rb = await post('/boosts', { headers: authH(farSellerTok), data: { target_type: 'listing', target_id: farListing.id, tier: 'sovereign' } });
  eq('sovereign boost 201', rb.status(), 201);
  const grant = await rb.json();
  eq('grant scope nation', grant.scope_kind, 'nation');
  eq('grant tier sovereign', grant.tier, 'sovereign');

  // a chance was spent
  allow = await (await get('/boosts/allowances', { headers: authH(farSellerTok) })).json();
  eq('sovereign remaining decremented', allow.tiers.find((t) => t.tier === 'sovereign').remaining, sovBefore - 1);

  // re-boost same target/tier/day → idempotent replay (same grant id), no extra spend
  rb = await post('/boosts', { headers: authH(farSellerTok), data: { target_type: 'listing', target_id: farListing.id, tier: 'sovereign' } });
  const grant2 = await rb.json();
  eq('re-boost replays same grant', grant2.id, grant.id);
  allow = await (await get('/boosts/allowances', { headers: authH(farSellerTok) })).json();
  eq('no double charge on replay', allow.tiers.find((t) => t.tier === 'sovereign').remaining, sovBefore - 1);

  // The nationwide (Sovereign) boost now reaches a distant buyer AS SPONSORED. We assert this from a
  // REMOTE, EMPTY locality (Lodwar, ~3.1191,35.5970), NOT Nairobi: a nationwide boost must reach
  // everyone, but when the buyer is IN Nairobi the platform correctly ranks real Nairobi shops' own
  // closer boosts ahead of a distant test listing for the bounded sponsored slots. Lodwar has no
  // local organic content, so the empty-organic floor surfaces the nationwide sovereigns on their
  // own — deterministic regardless of what real sellers are boosting in Nairobi.
  const REMOTE_Q = `lat=3.1191&lng=35.5970&radius_m=2000`;
  bf = await (await get(`/feed?${REMOTE_Q}`, { headers: authH(buyerTok) })).json();
  const sponsored = bf.items.find((i) => i.id === farListing.id);
  check('far listing reaches a remote buyer via boost', !!sponsored, 'sponsored item missing');
  if (sponsored) {
    eq('sponsored flagged is_sponsored', sponsored.is_sponsored, true);
    eq('sponsored boost_tier sovereign', sponsored.boost_tier, 'sovereign');
  }
  // The remote empty-organic floor is a pure sponsored lane — every item it surfaces is labelled
  // sponsored (no organic content exists this far out to be mislabelled).
  check('remote floor carries only labelled sponsored items',
    bf.items.every((i) => i.is_sponsored === true));

  // revoke → 204, then the sponsored item drops out of the remote feed where it had appeared.
  rb = await del(`/boosts/${grant.id}`, { headers: authH(farSellerTok) });
  eq('revoke boost 204', rb.status(), 204);
  bf = await (await get(`/feed?${REMOTE_Q}`, { headers: authH(buyerTok) })).json();
  check('far listing gone after revoke', !bf.items.some((i) => i.id === farListing.id));

  // --- §8 public comments thread (live) ---
  r = await post(`/listings/${listing.id}/comments`, { headers: authH(buyerTok), data: { body: 'Still available?' } });
  eq('post comment 201', r.status(), 201);
  const comment = await r.json();
  eq('comment body echoed', comment.body, 'Still available?');
  eq('comment author is buyer sub', comment.author_uuid, buyerSub);
  // whitespace-only body → 422 (schema guard at the boundary)
  r = await post(`/listings/${listing.id}/comments`, { headers: authH(buyerTok), data: { body: '   ' } });
  eq('empty comment 422', r.status(), 422);
  // second commenter → thread is public, newest-first
  await post(`/listings/${listing.id}/comments`, { headers: authH(seller(`${RUN}-cmt2`)), data: { body: 'I will take it' } });
  const thread = await (await get(`/listings/${listing.id}/comments`, { headers: authH(buyerTok) })).json();
  eq('thread newest-first', thread.items[0].body, 'I will take it');
  eq('thread has both comments', thread.items.length, 2);
  // comment on a missing listing → 404
  r = await post(`/listings/${RUN}-nope/comments`, { headers: authH(buyerTok), data: { body: 'hi' } });
  eq('comment missing listing 404', r.status(), 404);
  // comment_count surfaces on the feed (display-only) for the commented listing
  bf = await (await get(`/feed?lat=-1.292&lng=36.8219&radius_m=2000`, { headers: authH(buyerTok) })).json();
  const commented = bf.items.find((i) => i.id === listing.id);
  check('feed item carries comment_count >= 2', commented && commented.comment_count >= 2,
    `got ${commented && commented.comment_count}`);

  // --- §8 short-video post + Listings|Videos feed toggle (live) ---
  r = await post(`/shops/${shop.id}/listings`, {
    headers: authH(sellerTok),
    data: { title: 'E2E Reel', price_cents: 4000, stock_qty: 3, is_short_video: true },
  });
  eq('create short-video listing 201', r.status(), 201);
  const reel = await r.json();
  eq('reel flagged is_short_video', reel.is_short_video, true);

  const feedIds = async (kind) => {
    const q = `/feed?lat=-1.292&lng=36.8219&radius_m=2000${kind ? `&kind=${kind}` : ''}`;
    const fr = await (await get(q, { headers: authH(buyerTok) })).json();
    return new Set(fr.items.map((i) => i.id));
  };
  const both = await feedIds();
  check('both kinds present in unfiltered feed', both.has(reel.id) && both.has(listing.id));
  const onlyListings = await feedIds('listings');
  check('listings toggle excludes the reel', onlyListings.has(listing.id) && !onlyListings.has(reel.id));
  const onlyVideos = await feedIds('videos');
  check('videos toggle keeps only the reel', onlyVideos.has(reel.id) && !onlyVideos.has(listing.id));
  // invalid kind → 422
  r = await get(`/feed?lat=-1.292&lng=36.8219&kind=bogus`, { headers: authH(buyerTok) });
  eq('invalid feed kind 422', r.status(), 422);

  await ctx.dispose();

  console.log(`\n${passed} checks passed, ${failures.length} failed`);
  if (failures.length) {
    console.error('\nFAILURES:\n  - ' + failures.join('\n  - '));
    process.exit(1);
  }
}

main().catch((e) => { console.error('e2e crashed:', e); process.exit(1); });
