
/**
 * FE-2a live e2e — the SELLER CONSOLE write path the seller frontend actually uses.
 *
 * This exercises the TWO-TOKEN handoff end-to-end over HTTP, exactly as SellerConsolePage does:
 *
 *   weespas /auth/login → weespas /media/trade (WEESPAS token, multipart) → /uploads/trade URLs
 *        → weespas /commerce/session-token (mints RS256)
 *        → commerce /shops, /shops/{id}/listings (COMMERCE token) using those URLs
 *        → commerce PATCH /listings/{id}/stock  (POS)  → /feed (the buyer sees it)
 *
 * The two-token split is the feature's main integration risk (media lives in the weespas pipeline;
 * trade lives in commerce). This test is the only place the real handoff runs over the wire, so it
 * is the one that catches a mis-wired token / base-url / credentials combination.
 *
 * Fixtures are generated in-process (a 1×1 PNG + a tiny ftyp-boxed mp4) — no committed binaries.
 *
 * Run:
 *   cd PE/commerce/e2e
 *   NODE_PATH=/home/jeff/PE/InSAR-Final-main/frontend/node_modules \
 *     WEESPAS_BASE_URL=http://127.0.0.1:8000 COMMERCE_BASE_URL=http://127.0.0.1:8003 \
 *     WEESPAS_EMAIL=admin@weespas.com WEESPAS_PASSWORD=admin123 \
 *     node seller.fe.e2e.js
 */
const { request } = require('playwright');
const { registerCleanup } = require('./jwt');

const WEESPAS = process.env.WEESPAS_BASE_URL || 'http://127.0.0.1:8000';
const COMMERCE = process.env.COMMERCE_BASE_URL || 'http://127.0.0.1:8003';
const WEESPAS_API = `${WEESPAS}/api/v1`;
const COMMERCE_API = `${COMMERCE}/api/v1`;
const EMAIL = process.env.WEESPAS_EMAIL || 'admin@weespas.com';
const PASSWORD = process.env.WEESPAS_PASSWORD || 'admin123';

const RUN = `fe2a-${Date.now()}`;
registerCleanup(RUN);   // remove this run's shops/listings from the live DB on exit (any path)
const NBO = { lat: -1.292, lng: 36.8219 };          // seller + near buyer (Nairobi)
const COAST = { lat: -4.0435, lng: 39.6682 };       // far buyer (Mombasa, out of every organic radius)
// A REMOTE, empty locality (Lodwar, NW Kenya) — far from all test data, so the far buyer's ORGANIC
// feed is empty. That is the deterministic case for the sponsored lane: with no organic content to
// contend for the bounded slot, the empty-organic floor surfaces the nationwide (sovereign) boosts
// (up to feed_sponsored_max_on_empty). Mombasa (COAST) has accumulated e2e listings, so its single
// contended slot is non-deterministic; the sponsored-lane reach check uses REMOTE instead.
const REMOTE = { lat: 3.1191, lng: 35.5970 };

let passed = 0;
const failures = [];
const check = (name, cond, detail = '') => {
  if (cond) { passed += 1; console.log(`  ✓ ${name}`); }
  else { failures.push(`${name}${detail ? ` — ${detail}` : ''}`); console.log(`  ✗ ${name}${detail ? ` — ${detail}` : ''}`); }
};
const eq = (name, a, b) => check(name, a === b, `expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
const authH = (t) => ({ Authorization: `Bearer ${t}` });

// --- tiny in-process fixtures (no committed binaries) ---
// A valid 1×1 transparent PNG.
const PNG_1x1 = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
  'base64',
);
// A REAL, decodable H.264 clip (1s, 64×64, ~1.9 KB) — NOT a stub. The upload endpoint only
// content-type-checks (doesn't decode), so a 24-byte ftyp box passed the upload fine; but this test
// PUBLISHES a live `is_short_video` listing at the demo centre, which then feeds the user-facing
// Trade video strip. A non-playable stub there is real pollution: every run left another broken clip
// that the video-strip e2e (first tile must actually play) then failed on. A genuine playable clip
// keeps the upload assertion valid AND ensures nothing undecodable ever reaches the live feed.
const MP4_TINY = Buffer.from(
  'AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAAPibW9vdgAAAGxtdmhkAAAAAAAAAAAAAAAAAAAD6AAABI8AAQAAAQAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAgAAAwx0cmFrAAAAXHRraGQAAAADAAAAAAAAAAAAAAABAAAAAAAABI8AAAAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAAEAAAABAAAAAAAAkZWR0cwAAABxlbHN0AAAAAAAAAAEAAASPAAAIAAABAAAAAAKEbWRpYQAAACBtZGhkAAAAAAAAAAAAAAAAAAAwAAAAOABVxAAAAAAALWhkbHIAAAAAAAAAAHZpZGUAAAAAAAAAAAAAAABWaWRlb0hhbmRsZXIAAAACL21pbmYAAAAUdm1oZAAAAAEAAAAAAAAAAAAAACRkaW5mAAAAHGRyZWYAAAAAAAAAAQAAAAx1cmwgAAAAAQAAAe9zdGJsAAAAv3N0c2QAAAAAAAAAAQAAAK9hdmMxAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAAEAAQABIAAAASAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAGP//AAAANWF2Y0MBZAAK/+EAGGdkAAqs2UQmwEQAAAMABAAAAwBgPEiWWAEABmjr48siwP34+AAAAAAQcGFzcAAAAAEAAAABAAAAFGJ0cnQAAAAAAAAYnQAAGJ0AAAAYc3R0cwAAAAAAAAABAAAADgAABAAAAAAUc3RzcwAAAAAAAAABAAAAAQAAAIBjdHRzAAAAAAAAAA4AAAABAAAIAAAAAAEAABQAAAAAAQAACAAAAAABAAAAAAAAAAEAAAQAAAAAAQAAFAAAAAABAAAIAAAAAAEAAAAAAAAAAQAABAAAAAABAAAUAAAAAAEAAAgAAAAAAQAAAAAAAAABAAAEAAAAAAEAAAgAAAAAHHN0c2MAAAAAAAAAAQAAAAEAAAAOAAAAAQAAAExzdHN6AAAAAAAAAAAAAAAOAAAC3QAAAA4AAAAMAAAADAAAAAwAAAAUAAAADgAAAAwAAAAMAAAAFAAAAA4AAAAMAAAADAAAABQAAAAUc3RjbwAAAAAAAAABAAAEEgAAAGJ1ZHRhAAAAWm1ldGEAAAAAAAAAIWhkbHIAAAAAAAAAAG1kaXJhcHBsAAAAAAAAAAAAAAAALWlsc3QAAAAlqXRvbwAAAB1kYXRhAAAAAQAAAABMYXZmNTguNzYuMTAwAAAACGZyZWUAAAOfbWRhdAAAAq4GBf//qtxF6b3m2Ui3lizYINkj7u94MjY0IC0gY29yZSAxNjMgcjMwNjAgNWRiNmFhNiAtIEguMjY0L01QRUctNCBBVkMgY29kZWMgLSBDb3B5bGVmdCAyMDAzLTIwMjEgLSBodHRwOi8vd3d3LnZpZGVvbGFuLm9yZy94MjY0Lmh0bWwgLSBvcHRpb25zOiBjYWJhYz0xIHJlZj0zIGRlYmxvY2s9MTowOjAgYW5hbHlzZT0weDM6MHgxMTMgbWU9aGV4IHN1Ym1lPTcgcHN5PTEgcHN5X3JkPTEuMDA6MC4wMCBtaXhlZF9yZWY9MSBtZV9yYW5nZT0xNiBjaHJvbWFfbWU9MSB0cmVsbGlzPTEgOHg4ZGN0PTEgY3FtPTAgZGVhZHpvbmU9MjEsMTEgZmFzdF9wc2tpcD0xIGNocm9tYV9xcF9vZmZzZXQ9LTIgdGhyZWFkcz0yIGxvb2thaGVhZF90aHJlYWRzPTEgc2xpY2VkX3RocmVhZHM9MCBucj0wIGRlY2ltYXRlPTEgaW50ZXJsYWNlZD0wIGJsdXJheV9jb21wYXQ9MCBjb25zdHJhaW5lZF9pbnRyYT0wIGJmcmFtZXM9MyBiX3B5cmFtaWQ9MiBiX2FkYXB0PTEgYl9iaWFzPTAgZGlyZWN0PTEgd2VpZ2h0Yj0xIG9wZW5fZ29wPTAgd2VpZ2h0cD0yIGtleWludD0yNTAga2V5aW50X21pbj0xMiBzY2VuZWN1dD00MCBpbnRyYV9yZWZyZXNoPTAgcmNfbG9va2FoZWFkPTQwIHJjPWNyZiBtYnRyZWU9MSBjcmY9MjMuMCBxY29tcD0wLjYwIHFwbWluPTAgcXBtYXg9NjkgcXBzdGVwPTQgaXBfcmF0aW89MS40MCBhcT0xOjEuMDAAgAAAACdliIQAEP/+5sD5llUNV3/2YjwHgLSLlJeKSOrCiJeV3ezaG+F4beEAAAAKQZokbEEP/qpX3gAAAAhBnkJ4hv8HfQAAAAgBnmF0Qz8ICAAAAAgBnmNqQz8ICQAAABBBmmhJqEFomUwIf//+qZ01AAAACkGehkURLDf/B30AAAAIAZ6ldEM/CAkAAAAIAZ6nakM/CAgAAAAQQZqsSahBbJlMCG///qePiAAAAApBnspFFSw3/wd9AAAACAGe6XRDPwgIAAAACAGe62pDPwgIAAAAEEGa7UmoQWyZTAhn//6eLfE=',
  'base64',
);

async function main() {
  const ctx = await request.newContext();

  // 1) Weespas login.
  let r = await ctx.post(`${WEESPAS_API}/auth/login`, {
    headers: { 'Content-Type': 'application/json' },
    data: { email: EMAIL, password: PASSWORD },
  });
  check('weespas login 200', r.ok(), `status ${r.status()}`);
  const weespasToken = (await r.json()).token;
  check('weespas token present', typeof weespasToken === 'string' && weespasToken.length > 20);

  // 2) Upload listing media with the WEESPAS token (multipart). This is the two-token exception —
  //    media goes to weespas, NOT commerce. Assert the returned URLs are weespas-relative /uploads.
  // TWO images + a video — exercises the multi-media carousel on the feed card. Playwright's
  // `multipart` object can't repeat a field name, so build a FormData and append `images` twice.
  const mediaForm = new FormData();
  mediaForm.append('images', new Blob([PNG_1x1], { type: 'image/png' }), 'photo1.png');
  mediaForm.append('images', new Blob([PNG_1x1], { type: 'image/png' }), 'photo2.png');
  mediaForm.append('video', new Blob([MP4_TINY], { type: 'video/mp4' }), 'clip.mp4');
  r = await ctx.post(`${WEESPAS_API}/media/trade`, {
    headers: authH(weespasToken),
    multipart: mediaForm,
  });
  eq('upload trade media 201', r.status(), 201);
  const media = await r.json();
  eq('uploaded count == 3 (2 images + video)', media.uploaded, 3);
  check('image url is /uploads/trade/images', media.images[0]?.url.startsWith('/uploads/trade/images/'), media.images[0]?.url);
  check('video url is /uploads/trade/videos', media.video?.url.startsWith('/uploads/trade/videos/'), media.video?.url);
  const imageUrl = media.images[0].url;
  const imageUrl2 = media.images[1].url;
  const videoUrl = media.video.url;

  // 2b) The WEESPAS token must NOT be accepted by commerce (the two services trust different algs);
  //     proving the split is real — a mis-wired client sending the weespas token to commerce 401s.
  r = await ctx.get(`${COMMERCE_API}/feed?lat=${NBO.lat}&lng=${NBO.lng}`, { headers: authH(weespasToken) });
  check('commerce rejects the weespas token (two-token split is real)', r.status() === 401, `status ${r.status()}`);

  // 3) Bridge: mint the commerce session token (RS256) — everything below uses THIS.
  r = await ctx.get(`${WEESPAS_API}/commerce/session-token`, { headers: authH(weespasToken) });
  check('commerce session-token 200', r.ok(), `status ${r.status()}`);
  const session = await r.json();
  const alg = JSON.parse(Buffer.from(session.token.split('.')[0], 'base64').toString()).alg;
  eq('bridge mints RS256', alg, 'RS256');
  const cTok = session.token;

  // 4) Create a shop (commerce token) WITH a published business card (description + contact) —
  //    the §8 hovercard fields. Assert they round-trip.
  const SHOP_DESC = `Fresh stock from ${RUN}. Restocked weekly; message to confirm availability.`;
  const SHOP_CONTACT = 'WhatsApp 0712 345 678';
  r = await ctx.post(`${COMMERCE_API}/shops`, {
    headers: { ...authH(cTok), 'Content-Type': 'application/json' },
    data: {
      name: `FE2 Shop ${RUN}`, lat: NBO.lat, lng: NBO.lng, display_name: 'FE2 Seller',
      description: SHOP_DESC, contact: SHOP_CONTACT,
    },
  });
  eq('create shop 201', r.status(), 201);
  const shop = await r.json();
  const sellerId = shop.seller_id;
  eq('shop carries the published description', shop.description, SHOP_DESC);
  eq('shop carries the published contact', shop.contact, SHOP_CONTACT);

  // 4b) A description over the 200-word cap is rejected at the API edge (422).
  r = await ctx.post(`${COMMERCE_API}/shops`, {
    headers: { ...authH(cTok), 'Content-Type': 'application/json' },
    data: {
      name: `FE2 Overlong ${RUN}`, lat: NBO.lat, lng: NBO.lng, display_name: 'FE2 Seller',
      description: Array(201).fill('word').join(' '),
    },
  });
  eq('over-200-word shop description rejected 422', r.status(), 422);

  // 5) Create a SHORT-VIDEO listing with the uploaded media URLs — the full two-token round-trip.
  //    Include a multi-paragraph description so we verify newlines survive the create→feed path
  //    (the frontend renders them as paragraphs behind the 150-char "read more" expander).
  const DESC = `Fresh stock ${RUN}.\n\nPicked this morning, delivered same day.`;
  r = await ctx.post(`${COMMERCE_API}/shops/${shop.id}/listings`, {
    headers: { ...authH(cTok), 'Content-Type': 'application/json' },
    data: {
      title: `FE2 Reel ${RUN}`, description: DESC, price_cents: 15000, stock_qty: 3,
      pricing_mode: 'fixed', is_short_video: true, media_urls: [imageUrl, imageUrl2, videoUrl],
    },
  });
  eq('create listing 201', r.status(), 201);
  const listing = await r.json();
  eq('listing flagged is_short_video', listing.is_short_video, true);
  eq('listing description round-trips with paragraphs', listing.description, DESC);
  check('listing carries the uploaded media urls', listing.media_urls.includes(videoUrl), JSON.stringify(listing.media_urls));
  eq('listing carries all THREE media (carousel input)', listing.media_urls.length, 3);
  eq('listing initial stock 3', listing.stock_qty, 3);

  // 6) POS: a sale of one via {delta:-1} (the StockControl path).
  r = await ctx.patch(`${COMMERCE_API}/listings/${listing.id}/stock`, {
    headers: { ...authH(cTok), 'Content-Type': 'application/json' },
    data: { delta: -1 },
  });
  eq('adjust stock 200', r.status(), 200);
  eq('stock decremented to 2', (await r.json()).stock_qty, 2);

  // 7) The seller's own storefront lists the new item (the dashboard read path).
  r = await ctx.get(`${COMMERCE_API}/shops/mine`, { headers: authH(cTok) });
  eq('my storefront 200', r.status(), 200);
  const mine = await r.json();
  const mineTitles = mine.shops.flatMap((s) => s.listings.map((l) => l.title));
  check('my storefront lists the seeded reel', mineTitles.includes(`FE2 Reel ${RUN}`));

  // 8) The buyer feed surfaces it under the Videos toggle (?kind=videos) — the post is live.
  r = await ctx.get(`${COMMERCE_API}/feed?lat=${NBO.lat}&lng=${NBO.lng}&radius_m=2000&kind=videos`, { headers: authH(cTok) });
  eq('feed kind=videos 200', r.status(), 200);
  const vids = (await r.json()).items;
  check('reel appears under the Videos toggle', vids.some((i) => i.id === listing.id), `${vids.length} video items`);
  const feedReel = vids.find((i) => i.id === listing.id);
  if (feedReel) eq('feed item carries the description', feedReel.description, DESC);

  // 8b) FE-2b "reach & respond": promote the listing (story window), then Boost it (mtaa) and
  //     assert the allowance decrements + the sponsored lane labels it. Finally clean up the boost.
  r = await ctx.post(`${COMMERCE_API}/listings/${listing.id}/promote`, {
    headers: { ...authH(cTok), 'Content-Type': 'application/json' },
    data: { mode: 'story', duration_seconds: 3600 },
  });
  eq('promote listing 200', r.status(), 200);
  const promoted = await r.json();
  eq('listing now is_promoted', promoted.is_promoted, true);
  eq('promo_mode is story', promoted.promo_mode, 'story');

  // 8b-i) The server-authoritative tier catalogue the FE BoostChooser now reads (instead of
  //   hard-coding reach km) — GET /boosts/tiers. Assert the three tiers, narrow→wide order, and the
  //   reach shape: local tiers carry a positive radius, sovereign is nationwide (radius_m == null).
  r = await ctx.get(`${COMMERCE_API}/boosts/tiers`, { headers: authH(cTok) });
  eq('boost tiers 200', r.status(), 200);
  const cat = (await r.json()).tiers;
  eq('catalogue lists all three tiers', cat.map((t) => t.tier).join(','), 'mtaa,hustle,sovereign');
  const catMtaa = cat.find((t) => t.tier === 'mtaa');
  const catSov = cat.find((t) => t.tier === 'sovereign');
  check('mtaa tier carries a positive reach radius', catMtaa.radius_m > 0);
  eq('sovereign tier is nationwide (no radius)', catSov.radius_m, null);
  check('each tier exposes a daily free cap', cat.every((t) => t.daily_free_cap >= 0));

  // Record the pre-boost sovereign allowance, then spend one chance. We use the SOVEREIGN
  //  (nationwide) tier deliberately: the sponsored lane drops any listing already shown organically
  //  (no double-show), so to OBSERVE the sponsored label we must query from a point where the
  //  listing is NOT organic — a far buyer (Mombasa), reachable only because Sovereign is nationwide.
  r = await ctx.get(`${COMMERCE_API}/boosts/allowances`, { headers: authH(cTok) });
  eq('allowances 200', r.status(), 200);
  const beforeSov = (await r.json()).tiers.find((t) => t.tier === 'sovereign').remaining;
  r = await ctx.post(`${COMMERCE_API}/boosts`, {
    headers: { ...authH(cTok), 'Content-Type': 'application/json' },
    data: { target_type: 'listing', target_id: listing.id, tier: 'sovereign' },
  });
  // The sovereign tier is capped at 3/day with NO midnight refund (the boost economy). The shared
  // admin seller exhausts it after a few same-day runs → 429. That's a documented valid state, not
  // a regression, so we SKIP the boost-dependent checks (loudly) rather than fail a re-run. The
  // tier mechanics are covered exhaustively by commerce pytest; this block is the live sanity pass.
  if (r.status() === 429) {
    console.log('  ⚠ sovereign daily cap reached — skipping boost-lane checks (valid 429; covered by pytest)');
  } else {
    eq('create sovereign boost 201', r.status(), 201);
    const boostGrant = await r.json();
    eq('grant tier is sovereign', boostGrant.tier, 'sovereign');
    r = await ctx.get(`${COMMERCE_API}/boosts/allowances`, { headers: authH(cTok) });
    const afterSov = (await r.json()).tiers.find((t) => t.tier === 'sovereign').remaining;
    eq('sovereign allowance decremented by 1', afterSov, beforeSov - 1);

    // From a REMOTE, EMPTY buyer point (Lodwar), the Nairobi listing is way out of organic radius but
    // must enter via the sponsored lane — labelled is_sponsored with its tier (the §8.3 honesty
    // contract). An empty organic feed is the deterministic case: the empty-organic floor surfaces the
    // nationwide boosts (this session's global-promotion fix), so our just-boosted listing appears.
    r = await ctx.get(`${COMMERCE_API}/feed?lat=${REMOTE.lat}&lng=${REMOTE.lng}&radius_m=2000`, { headers: authH(cTok) });
    const boostedItem = (await r.json()).items.find((i) => i.id === listing.id);
    check('boosted listing reaches the far buyer via the sponsored lane', !!boostedItem);
    if (boostedItem) {
      eq('feed item is_sponsored', boostedItem.is_sponsored, true);
      eq('feed item boost_tier sovereign', boostedItem.boost_tier, 'sovereign');
    }

    // Stop the boost early (owner-only); the spent chance is NOT refunded.
    r = await ctx.delete(`${COMMERCE_API}/boosts/${boostGrant.id}`, { headers: authH(cTok) });
    eq('revoke boost 204', r.status(), 204);
    r = await ctx.get(`${COMMERCE_API}/boosts/allowances`, { headers: authH(cTok) });
    const postRevokeSov = (await r.json()).tiers.find((t) => t.tier === 'sovereign').remaining;
    eq('revoke does not refund the chance', postRevokeSov, afterSov);
    // And the far buyer no longer sees it (sponsored slot gone after revoke).
    r = await ctx.get(`${COMMERCE_API}/feed?lat=${REMOTE.lat}&lng=${REMOTE.lng}&radius_m=2000`, { headers: authH(cTok) });
    check('revoked boost no longer reaches the far buyer',
      !(await r.json()).items.some((i) => i.id === listing.id));
  }

  // 10) Plain POST (§8 timeline): a price-less post must surface in the feed despite stock 0 (the
  //     critical regression vs the out-of-stock product gate). Posted from the near-buyer location.
  r = await ctx.post(`${COMMERCE_API}/posts`, {
    headers: { ...authH(cTok), 'Content-Type': 'application/json' },
    data: { body: `Hello from ${RUN}.\n\nAnyone got fresh maize today?`, lat: NBO.lat, lng: NBO.lng },
  });
  eq('create post 201', r.status(), 201);
  const post = await r.json();
  eq('post is post_kind=post', post.post_kind, 'post');
  eq('post is price-less', post.price_cents, 0);
  r = await ctx.get(`${COMMERCE_API}/feed?lat=${NBO.lat}&lng=${NBO.lng}&radius_m=2000`, { headers: authH(cTok) });
  const feedPost = (await r.json()).items.find((i) => i.id === post.id);
  check('post surfaces in the feed despite zero stock', !!feedPost);
  if (feedPost) eq('feed post carries post_kind', feedPost.post_kind, 'post');

  // 11) Comment LIKE ("love"): toggle on → like_count 1 + liked_by_me; toggle off → 0/false.
  r = await ctx.post(`${COMMERCE_API}/listings/${post.id}/comments`, {
    headers: { ...authH(cTok), 'Content-Type': 'application/json' },
    data: { body: 'Great, will check 👍' },
  });
  eq('create comment 201', r.status(), 201);
  const comment = await r.json();
  r = await ctx.post(`${COMMERCE_API}/comments/${comment.id}/like`, { headers: authH(cTok) });
  eq('like comment 200', r.status(), 200);
  const liked = await r.json();
  eq('comment liked', liked.liked, true);
  eq('comment like_count 1', liked.like_count, 1);
  // The thread reflects the like for this viewer.
  r = await ctx.get(`${COMMERCE_API}/listings/${post.id}/comments`, { headers: authH(cTok) });
  const threaded = (await r.json()).items.find((c) => c.id === comment.id);
  eq('thread shows like_count', threaded?.like_count, 1);
  eq('thread shows liked_by_me', threaded?.liked_by_me, true);
  // Toggling again removes it.
  r = await ctx.post(`${COMMERCE_API}/comments/${comment.id}/like`, { headers: authH(cTok) });
  eq('unlike comment_count 0', (await r.json()).like_count, 0);

  // 12) Shop PROFILE hovercard (§8): the published business card + follower count + this viewer's
  //     follow state, plus the seller_id for the "Profile" deep-link.
  r = await ctx.get(`${COMMERCE_API}/shops/${shop.id}/profile`, { headers: authH(cTok) });
  eq('shop profile 200', r.status(), 200);
  const prof = await r.json();
  eq('profile name matches', prof.name, `FE2 Shop ${RUN}`);
  eq('profile carries the description', prof.description, SHOP_DESC);
  eq('profile carries the contact', prof.contact, SHOP_CONTACT);
  eq('profile seller_id matches (Profile deep-link)', prof.seller_id, sellerId);
  eq('profile starts un-followed', prof.following, false);
  eq('profile starts with 0 followers', prof.follower_count, 0);

  // Profile of an unknown shop → 404 (no fabricated card).
  r = await ctx.get(`${COMMERCE_API}/shops/does-not-exist/profile`, { headers: authH(cTok) });
  eq('unknown shop profile 404', r.status(), 404);

  // 13) Follow ("Notify") toggle: on → following + count 1; idempotent profile reflects it; off → 0.
  r = await ctx.post(`${COMMERCE_API}/shops/${shop.id}/follow`, { headers: authH(cTok) });
  eq('follow shop 200', r.status(), 200);
  const followed = await r.json();
  eq('now following', followed.following, true);
  eq('follower_count 1', followed.follower_count, 1);
  r = await ctx.get(`${COMMERCE_API}/shops/${shop.id}/profile`, { headers: authH(cTok) });
  const prof2 = await r.json();
  eq('profile reflects following', prof2.following, true);
  eq('profile reflects follower_count', prof2.follower_count, 1);
  // Toggle off.
  r = await ctx.post(`${COMMERCE_API}/shops/${shop.id}/follow`, { headers: authH(cTok) });
  eq('unfollow → following false', (await r.json()).following, false);
  // Follow of an unknown shop → 404.
  r = await ctx.post(`${COMMERCE_API}/shops/does-not-exist/follow`, { headers: authH(cTok) });
  eq('follow unknown shop 404', r.status(), 404);

  // 13b) Per-shop sponsored-cap OVERRIDE (§8.3 item 1) — the seller apply / staff decide loop the
  //   new UI drives. The admin login carries create:trades (owner) AND role=admin (staff), so one
  //   token exercises both sides. Key assertion: the seller status GET is NON-DESTRUCTIVE — reading
  //   it must never reset an approved override, which is exactly why the read endpoint exists.

  // Non-destructive status read: never applied → null override + server-authoritative bounds.
  r = await ctx.get(`${COMMERCE_API}/shops/${shop.id}/sponsored-cap`, { headers: authH(cTok) });
  eq('cap status 200', r.status(), 200);
  let cap = await r.json();
  eq('no override yet', cap.override, null);
  check('status exposes a positive max_cap (anti-drift)', cap.max_cap > 0, `max_cap ${cap.max_cap}`);
  check('status exposes a default_cap', typeof cap.default_cap === 'number', `default_cap ${cap.default_cap}`);
  const maxCap = cap.max_cap;

  // A bad value is a 422 before the DB is touched.
  r = await ctx.post(`${COMMERCE_API}/shops/${shop.id}/sponsored-cap`,
    { headers: { ...authH(cTok), 'Content-Type': 'application/json' }, data: { requested_cap: 0 } });
  eq('cap apply rejects 0 → 422', r.status(), 422);

  // Owner applies for a higher cap → pending.
  r = await ctx.post(`${COMMERCE_API}/shops/${shop.id}/sponsored-cap`,
    { headers: { ...authH(cTok), 'Content-Type': 'application/json' }, data: { requested_cap: 4 } });
  eq('cap apply 200', r.status(), 200);
  const applied = await r.json();
  eq('applied is pending', applied.status, 'pending');
  eq('applied requested_cap', applied.requested_cap, 4);

  // Appears in the staff pending queue, which carries the ceiling for the decide input.
  r = await ctx.get(`${COMMERCE_API}/admin/sponsored-caps`, { headers: authH(cTok) });
  eq('pending list 200', r.status(), 200);
  const queue = await r.json();
  eq('pending list carries max_cap', queue.max_cap, maxCap);
  check('our application is in the pending queue', queue.overrides.some((o) => o.id === applied.id));

  // Staff approves with an explicit cap → approved.
  r = await ctx.post(`${COMMERCE_API}/admin/sponsored-caps/${applied.id}/decide`,
    { headers: { ...authH(cTok), 'Content-Type': 'application/json' }, data: { approve: true, approved_cap: 3 } });
  eq('cap decide approve 200', r.status(), 200);
  eq('decided approved', (await r.json()).status, 'approved');

  // The seller status GET now reflects approved — and reading it repeatedly leaves it approved
  // (NON-DESTRUCTIVE: the whole point of the GET vs the pending-resetting POST).
  for (let i = 0; i < 2; i += 1) {
    r = await ctx.get(`${COMMERCE_API}/shops/${shop.id}/sponsored-cap`, { headers: authH(cTok) });
    cap = await r.json();
    eq(`status still approved after read #${i + 1}`, cap.override.status, 'approved');
    eq('status carries approved_cap', cap.override.approved_cap, 3);
  }

  // Cross-owner status read is 404 (no existence leak) — forge a token for a different sub via the
  // bridge is out of scope here; instead assert an unknown shop id (same no-leak path) is 404.
  r = await ctx.get(`${COMMERCE_API}/shops/does-not-exist/sponsored-cap`, { headers: authH(cTok) });
  eq('cap status unknown shop 404', r.status(), 404);

  // 9) Negative controls on the media endpoint (the FE relies on these for honest errors):
  //    bad content-type → 400; missing auth → 401/403.
  r = await ctx.post(`${WEESPAS_API}/media/trade`, {
    headers: authH(weespasToken),
    multipart: { images: { name: 'evil.txt', mimeType: 'text/plain', buffer: Buffer.from('nope') } },
  });
  eq('bad content-type rejected 400', r.status(), 400);
  r = await ctx.post(`${WEESPAS_API}/media/trade`, {
    multipart: { images: { name: 'photo.png', mimeType: 'image/png', buffer: PNG_1x1 } },
  });
  check('media upload without auth rejected', r.status() === 401 || r.status() === 403, `status ${r.status()}`);

  await ctx.dispose();
  console.log(`\n${passed} checks passed, ${failures.length} failed`);
  if (failures.length) { console.error('\nFAILURES:\n  - ' + failures.join('\n  - ')); process.exit(1); }
}

main().catch((e) => { console.error('seller fe e2e crashed:', e); process.exit(1); });
