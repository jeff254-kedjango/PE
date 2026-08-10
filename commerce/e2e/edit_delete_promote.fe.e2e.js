/**
 * Live e2e for this session's four features, over the real two-token bridge (weespas → commerce):
 *
 *   #2  EDIT + soft-DELETE a listing   — PATCH /listings/{id}, DELETE /listings/{id}
 *   #4  GLOBAL promotion               — a Sovereign boost reaches a FAR buyer with an EMPTY local
 *                                        feed (Mombasa), the bug this session fixed in _interleave_sponsored
 *   #3  "Boosted" label                — the far buyer's feed item is flagged is_sponsored + tier
 *   #5  Shop LOGO + banner             — create a shop with avatar_url/banner_url, assert they
 *                                        round-trip on the profile AND surface on the trending card
 *
 * Pure HTTP (no browser) — asserts the backend contract the seller/buyer UIs depend on. The two-token
 * split is exercised exactly as the app does it (media/identity on weespas, trade on commerce).
 *
 * Run (weespas :8000, commerce :8003):
 *   NODE_PATH=/home/jeff/PE/InSAR-Final-main/frontend/node_modules node edit_delete_promote.fe.e2e.js
 */
const { request } = require('playwright');
const { seller, registerCleanup } = require('./jwt');

const WEESPAS = process.env.WEESPAS_BASE_URL || 'http://127.0.0.1:8000';
const COMMERCE = process.env.COMMERCE_BASE_URL || 'http://127.0.0.1:8003';
const WEESPAS_API = `${WEESPAS}/api/v1`;
const COMMERCE_API = `${COMMERCE}/api/v1`;
const EMAIL = process.env.WEESPAS_EMAIL || 'admin@weespas.com';
const PASSWORD = process.env.WEESPAS_PASSWORD || 'admin123';

const RUN = `edp-${Date.now()}`;
registerCleanup(RUN);   // remove this run's shops/listings from the live DB on exit (any path)
const NBO = { lat: -1.292, lng: 36.8219 };      // seller + near buyer (Nairobi)
// A REMOTE, EMPTY locality (Lodwar, NW Kenya) — far from all test data, so the buyer's ORGANIC feed
// is empty. That is the deterministic case for the global-promotion fix: with no organic content to
// contend for the bounded sponsored slot, the empty-organic floor surfaces the nationwide (sovereign)
// boosts. (Mombasa has accumulated e2e listings, so its single contended slot is non-deterministic.)
const REMOTE = { lat: 3.1191, lng: 35.5970 };

let passed = 0;
const failures = [];
const check = (name, cond, detail = '') => {
  if (cond) { passed += 1; console.log(`  ✓ ${name}`); }
  else { failures.push(`${name}${detail ? ` — ${detail}` : ''}`); console.log(`  ✗ ${name}${detail ? ` — ${detail}` : ''}`); }
};
const eq = (name, a, b) => check(name, a === b, `expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
const authH = (t) => ({ Authorization: `Bearer ${t}` });

// A tiny valid PNG for the logo/banner upload (no committed binary).
const PNG_1x1 = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
  'base64',
);

async function uploadImage(ctx, weespasToken, name) {
  const form = new FormData();
  form.append('images', new Blob([PNG_1x1], { type: 'image/png' }), name);
  const r = await ctx.post(`${WEESPAS_API}/media/trade`, { headers: authH(weespasToken), multipart: form });
  eq(`upload ${name} 201`, r.status(), 201);
  return (await r.json()).images[0].url;
}

async function feedTitles(ctx, cTok, at, extra = '') {
  const r = await ctx.get(`${COMMERCE_API}/feed?lat=${at.lat}&lng=${at.lng}${extra}`, { headers: authH(cTok) });
  check(`feed 200 @${at.lat}`, r.ok(), `status ${r.status()}`);
  return (await r.json()).items;
}

async function main() {
  const ctx = await request.newContext();

  // --- auth + bridge ---
  let r = await ctx.post(`${WEESPAS_API}/auth/login`, {
    headers: { 'Content-Type': 'application/json' }, data: { email: EMAIL, password: PASSWORD },
  });
  check('weespas login 200', r.ok(), `status ${r.status()}`);
  const weespasToken = (await r.json()).token;

  r = await ctx.get(`${WEESPAS_API}/commerce/session-token`, { headers: authH(weespasToken) });
  check('commerce session-token 200', r.ok(), `status ${r.status()}`);
  const cTok = (await r.json()).token;
  const jsonH = { ...authH(cTok), 'Content-Type': 'application/json' };

  // ============================ #5 shop logo + banner ============================
  const logoUrl = await uploadImage(ctx, weespasToken, 'logo.png');
  const bannerUrl = await uploadImage(ctx, weespasToken, 'banner.png');

  r = await ctx.post(`${COMMERCE_API}/shops`, {
    headers: jsonH,
    data: {
      name: `EDP Shop ${RUN}`, lat: NBO.lat, lng: NBO.lng, display_name: 'EDP Seller',
      avatar_url: logoUrl, banner_url: bannerUrl, category: 'bakery',
    },
  });
  eq('create shop with logo+banner 201', r.status(), 201);
  const shop = await r.json();
  eq('shop carries avatar_url (logo)', shop.avatar_url, logoUrl);
  eq('shop carries banner_url', shop.banner_url, bannerUrl);

  // The public profile hovercard exposes both (seller-published, not PII).
  r = await ctx.get(`${COMMERCE_API}/shops/${shop.id}/profile`, { headers: authH(cTok) });
  eq('shop profile 200', r.status(), 200);
  const prof = await r.json();
  eq('profile avatar_url (logo) round-trips', prof.avatar_url, logoUrl);
  eq('profile banner_url round-trips', prof.banner_url, bannerUrl);

  // ============================ #2 create → edit → delete ============================
  r = await ctx.post(`${COMMERCE_API}/shops/${shop.id}/listings`, {
    headers: jsonH,
    data: { title: `Bread ${RUN}`, price_cents: 12000, stock_qty: 5, pricing_mode: 'fixed' },
  });
  eq('create listing 201', r.status(), 201);
  const listing = await r.json();

  // EDIT: change title + price via PATCH (partial).
  r = await ctx.patch(`${COMMERCE_API}/listings/${listing.id}`, {
    headers: jsonH, data: { title: `Sourdough ${RUN}`, price_cents: 15000 },
  });
  eq('PATCH edit listing 200', r.status(), 200);
  const edited = await r.json();
  eq('edited title applied', edited.title, `Sourdough ${RUN}`);
  eq('edited price applied', edited.price_cents, 15000);

  // The edit is visible on the near buyer's feed.
  let items = await feedTitles(ctx, cTok, NBO);
  check('edited listing visible on feed with new title',
    items.some((i) => i.id === listing.id && i.title === `Sourdough ${RUN}`));

  // A cross-owner PATCH is impossible to test with one identity here; the pytest suite covers 404.
  // Empty patch → 422 (nothing to change).
  r = await ctx.patch(`${COMMERCE_API}/listings/${listing.id}`, { headers: jsonH, data: {} });
  eq('empty PATCH rejected 422', r.status(), 422);

  // DELETE (soft): removes it from the feed; row retained inactive.
  r = await ctx.delete(`${COMMERCE_API}/listings/${listing.id}`, { headers: authH(cTok) });
  eq('DELETE listing 204', r.status(), 204);
  items = await feedTitles(ctx, cTok, NBO);
  check('deleted listing gone from feed', !items.some((i) => i.id === listing.id));
  // Idempotent: a second delete is still 204.
  r = await ctx.delete(`${COMMERCE_API}/listings/${listing.id}`, { headers: authH(cTok) });
  eq('second DELETE idempotent 204', r.status(), 204);

  // ============================ #4 + #3 global promotion + Boosted label ============================
  // A fresh in-stock listing, boosted SOVEREIGN (nationwide). It must reach a FAR buyer whose local
  // feed is empty — the exact bug fixed this session.
  //
  // Boost as a FRESH SYNTHETIC SELLER, not the shared admin. The sovereign tier is capped at 3
  // grants/seller/day with NO midnight refund (the boost economy). The admin identity is reused by
  // several e2e in a run, so a few same-day passes exhaust ITS 3 chances → 429 → this headline
  // "empty-feed buyer still sees a sovereign item" assertion silently can't run. Previously that was
  // masked by leftover residue in the feed; now that the run-scoped teardown actually completes
  // (this session's cleanup_run FK fix) each run cleans up after itself, so a cap-spent run genuinely
  // saw 0 sponsored → red. A run-tagged synthetic seller (`${RUN}-promoter`, new seller_id every
  // run) always has its full sovereign allowance, so the grant is a deterministic 201 and the fix
  // stays under test on EVERY pass. Its shop/listing/allowance are all removed by cleanup_run (which
  // deletes synthetic sellers keyed on the run id). Same seeding pattern trending.perf.js uses.
  const promoterTok = seller(`${RUN}-promoter`);
  const promoterH = { ...authH(promoterTok), 'Content-Type': 'application/json' };
  // The promoter's own shop carries the SAME logo (reusing the already-uploaded media URL — a plain
  // string, no per-seller ownership on media) so #5 (logo surfaces on the promoted card) still holds.
  r = await ctx.post(`${COMMERCE_API}/shops`, {
    headers: promoterH,
    data: {
      name: `EDP Promoter ${RUN}`, lat: NBO.lat, lng: NBO.lng, display_name: 'EDP Promoter',
      avatar_url: logoUrl, category: 'bakery',
    },
  });
  eq('create promoter shop 201', r.status(), 201);
  const promoterShop = await r.json();

  r = await ctx.post(`${COMMERCE_API}/shops/${promoterShop.id}/listings`, {
    headers: promoterH,
    data: { title: `Nationwide Loaf ${RUN}`, price_cents: 9000, stock_qty: 9, pricing_mode: 'fixed' },
  });
  eq('create boostable listing 201', r.status(), 201);
  const promoted = await r.json();

  r = await ctx.post(`${COMMERCE_API}/boosts`, {
    headers: promoterH, data: { target_type: 'listing', target_id: promoted.id, tier: 'sovereign' },
  });
  // Deterministic 201 — a brand-new seller always has its full 3/day sovereign allowance.
  eq('grant sovereign boost 201', r.status(), 201);

  // REMOTE buyer (Lodwar) with an EMPTY organic feed: the nationwide (sovereign) boost must reach them
  // regardless of radius — the bug this session fixed. With no organic content, the empty-organic floor
  // surfaces the sovereign boosts deterministically, so our just-boosted listing appears.
  const far = await feedTitles(ctx, cTok, REMOTE);
  const sponsored = far.filter((i) => i.is_sponsored);
  check('#4 a REMOTE buyer with an empty local feed still sees sponsored (boosted) items',
    sponsored.length > 0, `remote feed had ${far.length} items, ${sponsored.length} sponsored`);
  check('#3 every far sponsored item is labelled Boosted with a reach tier',
    sponsored.every((i) => i.is_sponsored === true && !!i.boost_tier));
  check('#4 nationwide reach: a sovereign boost is in the remote buyer\'s sponsored lane',
    sponsored.some((i) => i.boost_tier === 'sovereign'),
    `tiers: ${sponsored.map((i) => i.boost_tier).join(',')}`);
  // #5 is asserted against OUR listing (the one we gave a logo). The grant is deterministic now, so
  // our item must be present AND carry the promoter shop's avatar.
  const mine = far.find((i) => i.id === promoted.id);
  check('#4 our just-boosted listing specifically reaches the empty-feed remote buyer', !!mine,
    'expected the freshly-boosted listing in the remote sponsored lane');
  check('#5 our promoted feed item carries the shop logo (avatar)',
    !!(mine && mine.shop_avatar_url), `avatar: ${mine ? mine.shop_avatar_url : '(listing absent)'}`);

  await ctx.dispose();

  console.log(`\n${passed} checks passed, ${failures.length} failed`);
  if (failures.length) { console.error('\nFAILURES:\n  - ' + failures.join('\n  - ')); process.exit(1); }
}

main().catch((e) => { console.error('e2e crashed:', e); process.exit(1); });
