// Commerce bridge API client.
//
// Commerce (the social-marketplace service, :8003) is a SEPARATE backend with its own DB and
// its own RS256 auth. The weespas frontend never logs into it directly: it asks weespas for a
// short-lived, commerce-scoped token (GET /commerce/session-token — see weespas
// routers/commerce.py), then talks to the commerce service DIRECTLY with that bearer. This is
// the same mint-here/verify-there pattern as the InSAR bridge (api/insar.ts), and keeps identity
// single-sourced on weespas (work_flow.md §9).
//
// The commerce base URL is returned by the bridge (`commerce_url`) rather than hard-coded, so a
// deploy can move the service without a frontend change. We read the feed/storefront against
// that base; the weespas API base (API_BASE_URL) is used only to fetch the token.
import { fetchJson, API_BASE_URL } from './config';

const authHeaders = (token: string) => ({ Authorization: `Bearer ${token}` });

// Commerce calls are CROSS-ORIGIN to a token-authenticated service; we do NOT want the weespas
// session cookie sent along (it is meaningless to commerce and avoids needless preflight noise).
// `credentials: 'omit'` overrides fetchJson's default 'include' for these calls only.
const crossOrigin: RequestInit = { credentials: 'omit' };

// ----------------------------- session bridge -----------------------------

export interface CommerceSession {
  token: string;        // commerce-scoped RS256 JWT (read:feed + create:trades)
  commerce_url: string; // base origin of the commerce service (no trailing /api)
}

/** Mint a commerce-scoped token for the signed-in user. Requires the weespas token (the bridge
 *  is authed on weespas). The returned commerce token is short-lived and is the bearer for every
 *  subsequent commerce call. */
export async function getCommerceSession(weespasToken: string): Promise<CommerceSession> {
  return fetchJson<CommerceSession>(`${API_BASE_URL}/commerce/session-token`, {
    headers: authHeaders(weespasToken),
  });
}

/** The commerce API prefix on a given base origin. */
function apiBase(commerceUrl: string): string {
  return `${commerceUrl.replace(/\/$/, '')}/api/v1`;
}

// ----------------------------- feed DTOs -----------------------------

/** One product post in the proximity feed. Mirrors commerce schemas/feed.py FeedItem — only the
 *  fields the buyer UI needs. The honesty-critical flags:
 *    is_promoted  — a live §8 "selling now" window (display badge; the boost is already in score)
 *    is_sponsored — occupies a §8.3 SPONSORED slot (paid reach, NOT a higher organic rank). The
 *                   UI MUST label this "Boosted" — that label is the two-lane feed's honesty
 *                   contract (a buyer always knows what they paid to see).
 *    boost_tier   — mtaa | hustle | sovereign when sponsored; null otherwise.
 *    property_uuid— the stitch key to the InSAR Confirmed-shield badge (fetched concurrently). */
export interface FeedItem {
  id: string;
  shop_id: string;
  seller_id: string;
  /** Owning shop's display name + profile picture for the social header. shop_avatar_url is a media
   *  URL (absolute or /uploads/... relative) — resolve via resolveMediaUrl; null ⇒ initials fallback. */
  shop_name: string | null;
  shop_avatar_url: string | null;
  /** Owning shop's trade category slug (§8) — the client maps it to a color (utils/categories.ts).
   *  null ⇒ un-categorised. Display-only, never a ranking signal. */
  shop_category: string | null;
  property_uuid: string | null;
  title: string;
  /** Free-text product description (paragraphs preserved via newlines). The card shows a short
   *  preview with a "read more" expander. null ⇒ no description. */
  description: string | null;
  price_cents: number;
  currency: string;
  media_urls: string[];
  distance_m: number;
  score: number;
  save_count: number;
  /** Whether the calling buyer has already saved this listing — seeds the card's heart so it
   *  reflects prior saves on a fresh mount. Display-only, never a ranking signal. */
  saved_by_me: boolean;
  /** Public-comment count on this post (§8 social thread). Display-only — not a ranking signal. */
  comment_count: number;
  /** Seller's declared post kind: true ⇒ a dedicated short-video post (the Videos toggle). An
   *  ordinary listing can still carry video media — this is the kind the seller chose. */
  is_short_video: boolean;
  /** §8 timeline kind: 'product' (sellable — price/Ask chrome) or 'post' (plain social content;
   *  the card suppresses price/Ask and renders the description as the post body). */
  post_kind: PostKind;
  seller_rating: number | null;
  seller_review_count: number;
  is_promoted: boolean;
  is_sponsored: boolean;
  boost_tier: string | null;
  created_at: string;
}

/** §8 timeline kind. 'product' = sellable listing; 'post' = plain social content (no price/stock). */
export type PostKind = 'product' | 'post';

export interface FeedResponse {
  items: FeedItem[];
  next_cursor: string | null;
  /** Auto-widen honesty signals (commerce services/feed.py). `widened` is true when the buyer's
   *  immediate radius held fewer than one page of local content and the feed fell back once to the
   *  server max radius to surface MORE nearest content; `nearest_distance_m` is the closest returned
   *  listing's distance in metres (null when there are no listings at all). `immediate_count` is how
   *  many listings the IMMEDIATE (un-widened) radius held — the client phrases the note honestly on
   *  it: 0 ⇒ "nothing in your area", >0 ⇒ "only a few nearby, also showing farther" (it must NOT
   *  claim the area is empty when it isn't). The client shows an honest "closest shops are within
   *  X km" note instead of a dead-end empty surface. Absent on older responses ⇒ treated as false/0. */
  widened: boolean;
  nearest_distance_m: number | null;
  immediate_count: number;
}

/** The §8 feed toggle. 'listings' = ordinary posts; 'videos' = short-video posts; undefined =
 *  both (the unified feed). Maps to the commerce `?kind=` query param. */
export type FeedKind = 'listings' | 'videos';

export interface FeedQuery {
  lat: number;
  lng: number;
  radius_m?: number;
  cursor?: string | null;
  limit?: number;
  kind?: FeedKind;
}

/** Fetch one page of the proximity feed. The cursor is the opaque keyset cursor from the previous
 *  page's `next_cursor` (id-anchored — see commerce services/feed.py); pass it to continue. */
export async function getFeed(
  session: CommerceSession,
  q: FeedQuery,
): Promise<FeedResponse> {
  const params = new URLSearchParams({ lat: String(q.lat), lng: String(q.lng) });
  if (q.radius_m != null) params.set('radius_m', String(q.radius_m));
  if (q.cursor) params.set('cursor', q.cursor);
  if (q.limit != null) params.set('limit', String(q.limit));
  if (q.kind) params.set('kind', q.kind);
  return fetchJson<FeedResponse>(`${apiBase(session.commerce_url)}/feed?${params.toString()}`, {
    ...crossOrigin,
    headers: authHeaders(session.token),
  });
}

// ----------------------------- trending rail (§8) -----------------------------

/** One boosted PRODUCT in the trending rail's queue. Mirrors commerce schemas/trending.py
 *  TrendingProductCard — opaque ids + seller-published fields only (no PII). The client maps
 *  `category` to a color + icon (utils/categories.ts), formats `price_cents`, and ALWAYS labels
 *  these "Boosted". Tapping opens the seller storefront via `seller_id`. */
export interface TrendingProductCard {
  listing_id: string;
  seller_id: string;
  title: string;
  price_cents: number;
  currency: string;
  category: string | null;
  property_uuid: string | null;
  distance_m: number;
  boost_tier: string;
  /** The product's own lead image URL — the promoted card shows the item for sale when present;
   *  null ⇒ fall back to the category tint/icon. */
  image_url: string | null;
}

/** The trending product queue for a locality. The client renders `visible_slots` cards, decays each
 *  over `slot_seconds` (per-slot timer) pulling the next queued product into a freed slot, and
 *  re-polls every `poll_seconds`. `active_count` is the total boosted products in the locality
 *  (≥ visible_slots under contention). */
export interface TrendingSlate {
  cards: TrendingProductCard[];
  visible_slots: number;
  slot_seconds: number;
  poll_seconds: number;
  bucket: string;
  active_count: number;
}

/** Fetch the queue of boosted products near the buyer. Requires the commerce session token
 *  (same audience gate as the feed). The membership is server-deterministic per locality bucket,
 *  so all nearby buyers get the same (cacheable) queue. */
export async function getTrending(
  session: CommerceSession,
  lat: number,
  lng: number,
): Promise<TrendingSlate> {
  const params = new URLSearchParams({ lat: String(lat), lng: String(lng) });
  return fetchJson<TrendingSlate>(`${apiBase(session.commerce_url)}/trending?${params.toString()}`, {
    ...crossOrigin,
    headers: authHeaders(session.token),
  });
}

// ----------------------------- Quick Buys grid (§8) -----------------------------

/** One item in the Quick Buys grid. Mirrors commerce schemas/quick_buys.py QuickBuyItem — the LEAN
 *  buyer view (opaque ids + seller-published fields + one thumbnail + distance + pricing_mode +
 *  provenance bucket). No POS internals, no PII. `pricing_mode` decides the card's action:
 *  'fixed' ⇒ a one-tap buy-now (openOrder locks immediately); 'bargain' ⇒ open the storefront to
 *  negotiate (a bargain order needs an opening offer, so it can't be one tap). `bucket` is
 *  display/telemetry only: 'near' (within the radius), 'interest' (affinity-matched), 'trending'
 *  (backfill). */
export interface QuickBuyItem {
  id: string;
  shop_id: string;
  seller_id: string;
  shop_name: string | null;
  shop_category: string | null;
  title: string;
  price_cents: number;
  currency: string;
  thumbnail_url: string | null;
  distance_m: number;
  pricing_mode: 'fixed' | 'bargain';
  bucket: 'near' | 'interest' | 'trending';
}

export interface QuickBuysResponse {
  items: QuickBuyItem[];
  near_radius_m: number;
  page_size: number;
}

/** Optional buyer filters for the Quick Buys grid. All optional; the server validates + clamps
 *  (price ≥ 0, unknown categories dropped, radius clamped to the server cap). */
export interface QuickBuysFilters {
  minPriceCents?: number | null;
  maxPriceCents?: number | null;
  categories?: string[];
  radiusM?: number | null;
}

/** Fetch the buyer's Quick Buys grid — a composed near/interest mix (see commerce
 *  services/quick_buys.py). Requires the commerce session token. The result is PERSONAL (it reads
 *  the caller's own engagement history for affinity), so it is not shared/cached across buyers. */
export async function getQuickBuys(
  session: CommerceSession,
  lat: number,
  lng: number,
  filters?: QuickBuysFilters,
): Promise<QuickBuysResponse> {
  const params = new URLSearchParams({ lat: String(lat), lng: String(lng) });
  if (filters?.minPriceCents != null) params.set('min_price_cents', String(filters.minPriceCents));
  if (filters?.maxPriceCents != null) params.set('max_price_cents', String(filters.maxPriceCents));
  if (filters?.categories && filters.categories.length) {
    params.set('categories', filters.categories.join(','));
  }
  if (filters?.radiusM != null) params.set('radius_m', String(filters.radiusM));
  return fetchJson<QuickBuysResponse>(
    `${apiBase(session.commerce_url)}/quick-buys?${params.toString()}`,
    { ...crossOrigin, headers: authHeaders(session.token) },
  );
}

// ----------------------------- global trade search (navbar unified search) -----------------------------

/** One trade search result. Mirrors commerce schemas/search.py TradeSearchResult — the LEAN result
 *  view (opaque ids + seller-published fields + lead image + distance + property_uuid stitch key).
 *  No PII. `image_url` is the listing's lead non-video image (null ⇒ initials/category tint);
 *  `distance_m` is the nearest-first ordering key (results are ranked closest-first, nationwide). */
export interface TradeSearchResult {
  listing_id: string;
  seller_id: string;
  shop_id: string;
  shop_name: string | null;
  shop_category: string | null;
  title: string;
  price_cents: number;
  currency: string;
  image_url: string | null;
  media_urls: string[];
  property_uuid: string | null;
  distance_m: number;
}

export interface TradeSearchResponse {
  results: TradeSearchResult[];
  /** The normalised (trimmed) query the server actually searched on — the client labels its panel
   *  on this so it reflects exactly what was matched. */
  query: string;
}

/** Search trade listings by keyword (title / description / shop name), ranked NEAREST-FIRST
 *  nationwide (commerce GET /api/v1/search). Requires the commerce session token. The buyer's
 *  (lat,lng) only ORDERS the results (closest first) — it never gates them, so a far match still
 *  appears. A query shorter than the server minimum returns an empty list (the client should also
 *  gate + debounce). Fired CONCURRENTLY with the weespas property search for the unified navbar
 *  panel — the two backends are merged client-side, never cross-DB-joined. */
export async function searchTrade(
  session: CommerceSession,
  query: string,
  lat: number,
  lng: number,
  limit?: number,
): Promise<TradeSearchResponse> {
  const params = new URLSearchParams({ q: query, lat: String(lat), lng: String(lng) });
  if (limit != null) params.set('limit', String(limit));
  return fetchJson<TradeSearchResponse>(
    `${apiBase(session.commerce_url)}/search?${params.toString()}`,
    { ...crossOrigin, headers: authHeaders(session.token) },
  );
}

// ----------------------------- Flash Sales (§8 nationwide "crazy offer" grid) -----------------------------

/** One item in the Flash Sales grid — a lean buyer view of a listing on a live flash-sale window.
 *  Mirrors commerce schemas/flash_sales.py FlashSaleItem. No POS internals / PII. Flash sales are
 *  always fixed-price (a bargain listing can't run one), so the card reuses the one-tap buy path. */
export interface FlashSaleItem {
  id: string;
  shop_id: string;
  seller_id: string;
  shop_name: string | null;
  shop_category: string | null;
  title: string;
  flash_price_cents: number;   // the crazy price the buyer pays while the window is open
  reference_cents: number;     // the comparable-shop average (shown struck through)
  discount_percent: number;    // whole-percent discount vs the reference (display)
  currency: string;
  thumbnail_url: string | null;
  expires_at: string;          // ISO — the window close ("expires in less than an hour")
  distance_m: number | null;   // display-only (flash sales are nationwide); may be null
  pricing_mode: 'fixed' | 'bargain';
}

export interface FlashSalesResponse {
  items: FlashSaleItem[];
  page_size: number;
}

/** Fetch the nationwide Flash Sales slate — every active-window crazy offer on the platform, ranked
 *  by craziness (a precomputed margin). lat/lng are OPTIONAL and only add a display-only distance;
 *  they never filter or re-rank (the nationwide contract). Requires the commerce session token. */
export async function getFlashSales(
  session: CommerceSession,
  lat?: number | null,
  lng?: number | null,
): Promise<FlashSalesResponse> {
  const params = new URLSearchParams();
  if (lat != null && lng != null) {
    params.set('lat', String(lat));
    params.set('lng', String(lng));
  }
  const qs = params.toString();
  return fetchJson<FlashSalesResponse>(
    `${apiBase(session.commerce_url)}/flash-sales${qs ? `?${qs}` : ''}`,
    { ...crossOrigin, headers: authHeaders(session.token) },
  );
}

// ----------------------------- buy now (open a fixed-price order) -----------------------------

/** The subset of a commerce Order the buy-now flow needs back. Mirrors commerce schemas/order.py
 *  OrderOut. A fixed-price open returns status 'PRICE_LOCKED' with the locked price. */
export interface OrderOut {
  id: string;
  listing_id: string;
  status: string;
  pricing_mode: 'fixed' | 'bargain';
  reference_price_cents: number;
  locked_price_cents: number | null;
  current_offer_cents: number | null;
  created_at: string;
}

/** Open an order on a listing = the Quick Buys "buy now". For a FIXED-price listing this locks
 *  immediately at the list price (no offer needed); a BARGAIN listing requires `offerCents` (the FE
 *  routes those to the storefront instead, so this is normally called only for fixed listings). The
 *  `Idempotency-Key` header is MANDATORY on the money path — a double-tap with the same key returns
 *  the same order, never a second one. Generate a fresh key per user intent (e.g. crypto.randomUUID). */
export async function openOrder(
  session: CommerceSession,
  listingId: string,
  idempotencyKey: string,
  offerCents?: number,
): Promise<OrderOut> {
  return fetchJson<OrderOut>(`${apiBase(session.commerce_url)}/orders`, {
    ...crossOrigin,
    method: 'POST',
    headers: {
      ...authHeaders(session.token),
      'Content-Type': 'application/json',
      'Idempotency-Key': idempotencyKey,
    },
    body: JSON.stringify(offerCents != null ? { listing_id: listingId, offer_cents: offerCents } : { listing_id: listingId }),
  });
}

// ----------------------------- public storefront DTOs -----------------------------

/** A buyer-visible listing on a public storefront — the LEAN view (no POS internals: no stock
 *  counts, thresholds, intent, or inactive items). Mirrors commerce PublicListingOut. */
export interface PublicListing {
  id: string;
  shop_id: string;
  seller_id: string;
  property_uuid: string | null;
  title: string;
  price_cents: number;
  currency: string;
  media_urls: string[];
  pricing_mode: 'fixed' | 'bargain';
  created_at: string;
}

export interface PublicStorefrontShop {
  shop: {
    id: string;
    seller_id: string;
    name: string;
    /** Shareable URL slug (§8 storefront: /shop/<handle>) — null when unclaimed. Present here on
     *  the buyer-visible DTO so the frontend router can canonicalize /shop/<sellerId> →
     *  /shop/<handle> once the storefront resolves. */
    handle: string | null;
    property_uuid: string | null;
    lat: number;
    lng: number;
    created_at: string;
  };
  listings: PublicListing[];
}

export interface PublicStorefront {
  seller_id: string;
  display_name: string;
  rating: number | null;
  review_count: number;
  shops: PublicStorefrontShop[];
}

/** Any seller's public storefront (in-stock listings + embedded rating, no POS leak). 404 maps to
 *  a thrown error the caller surfaces as "storefront not found". */
export async function getPublicStorefront(
  session: CommerceSession,
  sellerId: string,
): Promise<PublicStorefront> {
  return fetchJson<PublicStorefront>(
    `${apiBase(session.commerce_url)}/sellers/${encodeURIComponent(sellerId)}/storefront`,
    { ...crossOrigin, headers: authHeaders(session.token) },
  );
}

/** The public storefront for a shop's HANDLE (the shareable /shop/<handle> URL, §8). Same shape as
 *  {@link getPublicStorefront} — one component renders both mounts. An unknown or bad handle is a
 *  404 (uniform with unknown-sellerId), so the caller surfaces the SAME "storefront not found"
 *  path either way. The server normalizes case, so `/shop/Mama-Mboga` resolves the same row. */
export async function getPublicStorefrontByHandle(
  session: CommerceSession,
  handle: string,
): Promise<PublicStorefront> {
  return fetchJson<PublicStorefront>(
    `${apiBase(session.commerce_url)}/shops/@${encodeURIComponent(handle)}/storefront`,
    { ...crossOrigin, headers: authHeaders(session.token) },
  );
}

// ----------------------------- engagement: saves + inquiries -----------------------------

export interface SaveToggleResult {
  listing_id: string;
  saved: boolean;
  save_count: number;
}

/** Toggle the signed-in buyer's save on a listing. Idempotent on the server (a double-save stays
 *  saved); returns the new state + count so the UI flips without a refetch. */
export async function toggleSave(
  session: CommerceSession,
  listingId: string,
): Promise<SaveToggleResult> {
  return fetchJson<SaveToggleResult>(
    `${apiBase(session.commerce_url)}/listings/${encodeURIComponent(listingId)}/save`,
    { ...crossOrigin, method: 'POST', headers: authHeaders(session.token) },
  );
}

export interface InquiryResult {
  id: string;
  listing_id: string;
  listing_title: string;
  message: string;
  created_at: string;
}

/** Ask the seller "is this still available?" (or a short custom message). The inquiry lands in the
 *  seller's PRIVATE inbox — distinct from a public comment. Defaults to the canonical question. */
export async function createInquiry(
  session: CommerceSession,
  listingId: string,
  message?: string,
): Promise<InquiryResult> {
  return fetchJson<InquiryResult>(
    `${apiBase(session.commerce_url)}/listings/${encodeURIComponent(listingId)}/inquiries`,
    {
      ...crossOrigin,
      method: 'POST',
      headers: { ...authHeaders(session.token), 'Content-Type': 'application/json' },
      body: JSON.stringify(message != null ? { message } : {}),
    },
  );
}

// ----------------------------- public comments (§8 social thread) -----------------------------

export interface Comment {
  id: string;
  listing_id: string;
  author_uuid: string;
  /** Display-name snapshot from the commenter's token (null on older comments — the UI falls back
   *  to a neutral label, never the raw uuid). */
  author_name: string | null;
  body: string;
  /** §8 like ("love") social proof: total likes + whether THIS viewer liked it (drives the
   *  filled/empty heart). Display-only — never a ranking signal. */
  like_count: number;
  liked_by_me: boolean;
  created_at: string;
}

export interface CommentPage {
  items: Comment[];
  next_cursor: string | null;
}

/** The new like state after a toggle — lets the client flip the heart + count without a refetch. */
export interface CommentLikeToggle {
  comment_id: string;
  liked: boolean;
  like_count: number;
}

/** A listing's PUBLIC comment thread, newest-first, keyset-paginated (same id-anchored cursor as
 *  the feed). Shown inline under a post — distinct from the private seller inquiry inbox. */
export async function listComments(
  session: CommerceSession,
  listingId: string,
  opts: { cursor?: string | null; limit?: number } = {},
): Promise<CommentPage> {
  const params = new URLSearchParams();
  if (opts.cursor) params.set('cursor', opts.cursor);
  if (opts.limit != null) params.set('limit', String(opts.limit));
  const qs = params.toString();
  return fetchJson<CommentPage>(
    `${apiBase(session.commerce_url)}/listings/${encodeURIComponent(listingId)}/comments${qs ? `?${qs}` : ''}`,
    { ...crossOrigin, headers: authHeaders(session.token) },
  );
}

/** Post a public comment on a listing. The body is trimmed + length-capped server-side (422 on
 *  empty/oversized); we mirror a soft cap in the UI. */
export async function postComment(
  session: CommerceSession,
  listingId: string,
  body: string,
): Promise<Comment> {
  return fetchJson<Comment>(
    `${apiBase(session.commerce_url)}/listings/${encodeURIComponent(listingId)}/comments`,
    {
      ...crossOrigin,
      method: 'POST',
      headers: { ...authHeaders(session.token), 'Content-Type': 'application/json' },
      body: JSON.stringify({ body }),
    },
  );
}

/** Toggle the caller's like ("love") on a public comment. Idempotent server-side (double-like
 *  stays liked); returns the new state so the client flips the heart + count without a refetch. */
export async function toggleCommentLike(
  session: CommerceSession,
  commentId: string,
): Promise<CommentLikeToggle> {
  return fetchJson<CommentLikeToggle>(
    `${apiBase(session.commerce_url)}/comments/${encodeURIComponent(commentId)}/like`,
    { ...crossOrigin, method: 'POST', headers: authHeaders(session.token) },
  );
}

/** The public-comment body cap mirrored from commerce services.engagement.COMMENT_MAX_LEN — the
 *  server is the authority (422), this is just for a friendly client-side guard. */
export const COMMENT_MAX_LEN = 2000;

// ----------------------------- shop profile hovercard + follow (§8) -----------------------------

/** A shop's public profile card (the hovercard over a post's seller avatar). Mirrors commerce
 *  schemas.catalog.ShopProfileOut — seller-published business fields + social proof, no PII. */
export interface ShopProfile {
  shop_id: string;
  seller_id: string;
  name: string;
  /** Shop profile picture / logo — a media URL (resolve via resolveMediaUrl); null ⇒ initials. */
  avatar_url: string | null;
  /** Wide banner / cover image for the shop profile — a media URL; null ⇒ plain header. */
  banner_url: string | null;
  /** Seller-published "about" blurb (≤200 words), or null when unset. */
  description: string | null;
  /** Seller-published public contact line (e.g. a WhatsApp/phone they elect to show), or null. */
  contact: string | null;
  /** Trade category slug (§8) — the client maps it to a color; null ⇒ un-categorised. */
  category: string | null;
  property_uuid: string | null;
  /** §8 "Notify": followers + whether THIS viewer follows (drives Follow/Following). */
  follower_count: number;
  following: boolean;
  /** The owning seller's proof-of-purchase rating (null ⇒ unrated). */
  rating: number | null;
  review_count: number;
}

/** The new follow state after a toggle — flips Follow/Following + the count without a refetch. */
export interface FollowToggle {
  shop_id: string;
  following: boolean;
  follower_count: number;
}

/** A shop's public profile for the hovercard. Any authenticated buyer may view any shop. 404 maps
 *  to a thrown error the caller surfaces as "couldn't load". */
export async function getShopProfile(session: CommerceSession, shopId: string): Promise<ShopProfile> {
  return fetchJson<ShopProfile>(
    `${apiBase(session.commerce_url)}/shops/${encodeURIComponent(shopId)}/profile`,
    { ...crossOrigin, headers: authHeaders(session.token) },
  );
}

/** Toggle the caller's follow ("Notify") on a shop — subscribe to its updates. Idempotent
 *  server-side (a double-follow stays following); returns the new state so the UI flips at once. */
export async function toggleShopFollow(session: CommerceSession, shopId: string): Promise<FollowToggle> {
  return fetchJson<FollowToggle>(
    `${apiBase(session.commerce_url)}/shops/${encodeURIComponent(shopId)}/follow`,
    { ...crossOrigin, method: 'POST', headers: authHeaders(session.token) },
  );
}

// ----------------------------- shop handle (§8 shareable storefront URL) -----------------------------
//
// A handle is the shop's public URL slug (/shop/<handle>) — a shareable link that is nicer than
// the raw seller_id UUID. ONE-SHOT policy: once set, permanent (a rename would break every
// previously-shared link). Every failure mode names a specific `reason` slug the frontend can
// map to inline copy; the same slugs are returned by both the availability probe and the PATCH
// error detail, so the frontend has ONE error-message map for both.

/** The reason slug an availability check or claim can fail on. Mirrors HandleError.detail in
 *  services.shops. Kept as a string union so an exhaustive switch statement is a compile check. */
export type HandleReason =
  | 'handle-required'    // blank/null (only from the probe if it is called with an empty box)
  | 'handle-length'      // outside [3, 30] chars after trim
  | 'handle-syntax'      // bad grammar (leading/trailing/double hyphen, uppercase, punctuation)
  | 'handle-reserved'    // reserved word (admin, api, me, ...) — server-side deny-list
  | 'handle-taken'       // another shop already claims this handle (case-insensitive)
  | 'handle-locked';     // this shop already claimed a DIFFERENT handle (one-shot policy)

/** GET /shops/handle-available response — the live probe used by CreateShopForm as the seller types. */
export interface HandleAvailability {
  /** The normalized (lowercased, trimmed) handle. Blank if the input was blank. */
  handle: string;
  /** True iff the handle passes syntax AND is not currently held. */
  available: boolean;
  /** The specific failure slug when available=false; null when available. */
  reason: HandleReason | null;
}

/** Probe the availability of a handle. Never throws for validation failures — a syntax error is
 *  a normal `available:false + reason` answer (the whole point is that the frontend can call this
 *  on every keystroke). Only throws on transport failure. */
export async function checkHandleAvailable(
  session: CommerceSession,
  handle: string,
): Promise<HandleAvailability> {
  const url = `${apiBase(session.commerce_url)}/shops/handle-available?handle=${encodeURIComponent(handle)}`;
  return fetchJson<HandleAvailability>(url, {
    ...crossOrigin,
    headers: authHeaders(session.token),
  });
}

/** Claim a handle for the caller's shop (one-shot). Idempotent on the same value; a claim against
 *  a shop that already carries a DIFFERENT handle throws with `handle-locked`. A collision throws
 *  `handle-taken`. Invalid syntax throws `handle-syntax`/`handle-reserved`/`handle-length`. */
export async function claimShopHandle(
  session: CommerceSession,
  shopId: string,
  handle: string,
): Promise<ShopOut> {
  return fetchJson<ShopOut>(
    `${apiBase(session.commerce_url)}/shops/${encodeURIComponent(shopId)}/handle`,
    {
      ...crossOrigin,
      method: 'PATCH',
      headers: { ...authHeaders(session.token), 'Content-Type': 'application/json' },
      body: JSON.stringify({ handle }),
    },
  );
}

// ----------------------------- reviews (§8 proof-of-purchase social proof) -----------------------------
//
// A review can only be WRITTEN by the buyer of a SETTLED order (proof-of-purchase gate — the create
// path lives on the order, not surfaced here yet since a buyer post-purchase flow ships later). This
// section covers the READ shape only: the storefront's Reviews tab renders `SellerReviewsPage`, whose
// `summary` doubles as the header rating aggregate. No PII beyond the reviewer's own `sub` (S6).

/** One review row — mirrors commerce schemas/review.py ReviewOut. */
export interface ReviewOut {
  id: string;
  order_id: string;
  seller_id: string;
  listing_id: string;
  /** Reviewer's synchronized weespas identity (the token `sub`) — an opaque id, never a name (S6);
   *  the UI resolves it to a display label through the same neutral fallback comments use. */
  reviewer_uuid: string;
  rating: number; // bounded 1..5 at the API + DB
  /** Optional short free-text note (≤1000 chars per the write-path schema); null when the reviewer
   *  left only a star. */
  body: string | null;
  created_at: string; // ISO 8601 UTC (SQLAlchemy datetime; JSON-encoded as string over the wire)
}

/** Aggregate score for a seller. `average` is null when `count` is 0 (unrated, distinct from a
 *  zero-star average) so the header can show "unrated" instead of a misleading 0. */
export interface RatingSummary {
  average: number | null;
  count: number;
}

/** One page of a seller's reviews plus the aggregate. `next_cursor` is null when the page is the
 *  last one; pass a non-null cursor back to the next call to continue newest-first (keyset). */
export interface SellerReviewsPage {
  summary: RatingSummary;
  items: ReviewOut[];
  next_cursor: string | null;
}

/** Fetch one page of a seller's reviews (newest-first) + the aggregate summary. An unknown or
 *  brand-new (unrated) seller returns a valid EMPTY page (summary.count = 0, average = null) —
 *  never a 404 — so the caller needn't special-case it. `cursor` continues a previous page;
 *  `limit` is bounded by the server (feed_max_page_size), passing it too large is clamped. */
export async function getSellerReviews(
  session: CommerceSession,
  sellerId: string,
  opts?: { cursor?: string | null; limit?: number },
): Promise<SellerReviewsPage> {
  const params = new URLSearchParams();
  if (opts?.cursor) params.set('cursor', opts.cursor);
  if (opts?.limit != null) params.set('limit', String(opts.limit));
  const qs = params.toString();
  const url =
    `${apiBase(session.commerce_url)}/sellers/${encodeURIComponent(sellerId)}/reviews` +
    (qs ? `?${qs}` : '');
  return fetchJson<SellerReviewsPage>(url, {
    ...crossOrigin,
    headers: authHeaders(session.token),
  });
}

// ----------------------------- ranking DTOs (§8, Chunk B) -----------------------------

/** Per-weight decomposition of the caller's rank — the "why this rank?" tooltip. Server-side
 *  weights: sales 0.60, composite (saves 0.35 · rating 0.30 · followers 0.20 · recency 0.15) 0.40.
 *  Both fields are already normalized to [0,1] by peer-set (divide-by-max in radius). */
export interface RankingWeightBreakdown {
  sales_score: number;      // 0..1 — the sales-window term of the caller's total score
  composite_score: number;  // 0..1 — the composite (saves/rating/followers/recency) term
}

/** Raw underlying signals — displayed alongside the breakdown so the caller sees the actual
 *  numbers, not just normalized ratios. `rating_count == 0` ⇒ shop is unrated (render "unrated",
 *  NOT "0.0 ★" — the server sends 0.0 for the average but the count carries the truth). */
export interface RankingSignals {
  revenue_cents: number;      // gross settled revenue in the window
  revenue_window_days: number; // 30 today; comes from the server so a change never desyncs
  rating: number;             // 0..5 (only meaningful when rating_count > 0)
  rating_count: number;
  follower_count: number;
  saves_total: number;
}

/** The Ranking Card's happy-path payload. `kind: 'ranking'` is the discriminant — see
 *  RankingResponse. `next_refresh_at` is when the server-side cache entry expires; the frontend
 *  aligns its own refetchInterval to this so we never poll faster than the cache TTL. */
export interface RankingOut {
  kind: 'ranking';
  rank: number;             // 1-indexed
  peer_count: number;
  radius_km: number;
  refreshed_at: string;     // ISO — server computed timestamp
  next_refresh_at: string;  // ISO — refreshed_at + 5 min
  own_score: number;        // 0..1
  weight_breakdown: RankingWeightBreakdown;
  signals: RankingSignals;
}

/** The paywall response for a radius > 200 km without an active entitlement. NOT an HTTP error
 *  (200 with kind='paywall_required'); the FE renders a CTA offering the `cta_kinds`. */
export interface RankingPaywallOut {
  kind: 'paywall_required';
  reason: 'radius_over_free_cap';
  free_max_radius_km: number;      // 200.0 today
  requested_radius_km: number;
  cta_kinds: Array<'one_time_2h' | 'annual'>;
}

/** The seller has no shop yet — the card renders a "create a shop to see your ranking" hint.
 *  Also a 200; the endpoint never returns 404 for the seller-console surface. */
export interface RankingUnavailableOut {
  kind: 'no_shop';
}

/** Discriminated union — the FE branches on `kind`. */
export type RankingResponse = RankingOut | RankingPaywallOut | RankingUnavailableOut;

/** Fetch the caller's ranking within `radiusKm` of their shop. `radiusKm` MUST be > 0 (the server
 *  enforces gt:0 le:20000 — a client-side bound-check would just duplicate that). The response
 *  is one of three shapes above; the caller branches on `kind`. */
export async function getMyRanking(
  session: CommerceSession,
  radiusKm: number,
): Promise<RankingResponse> {
  const url = `${apiBase(session.commerce_url)}/sellers/me/ranking?radius_km=${encodeURIComponent(String(radiusKm))}`;
  return fetchJson<RankingResponse>(url, {
    ...crossOrigin,
    headers: authHeaders(session.token),
  });
}

// ----------------------------- bulk stock CSV (§8, Chunk E3) -----------------------------

/** Response of POST /sellers/me/stock/bulk-csv. `skipped_count` counts submitted listing_ids
 *  the caller did not own — never their ids (privacy). `updated_ids` is echoed so the FE can
 *  invalidate its per-listing caches surgically. */
export interface BulkStockOut {
  updated_count: number;
  skipped_count: number;
  updated_ids: string[];
}

/** Apply a `listing_id,stock_qty` CSV to the caller's listings in one transaction. Server
 *  returns 422 on any parse/validation error with a human-readable `detail`. */
export async function postBulkStockCsv(
  session: CommerceSession,
  csv: string,
): Promise<BulkStockOut> {
  const url = `${apiBase(session.commerce_url)}/sellers/me/stock/bulk-csv`;
  return fetchJson<BulkStockOut>(url, {
    ...crossOrigin,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders(session.token) },
    body: JSON.stringify({ csv }),
  });
}


// ----------------------------- low-stock (§8, Chunk E2) -----------------------------

/** Response of GET /sellers/me/low-stock. `floor` echoes the applied shop-wide backstop so the
 *  UI can say "listings at or below 5"; per-listing thresholds still trigger regardless. */
export interface LowStockOut {
  floor: number;
  items: ListingOut[];
}

/** Fetch the caller's low-stock product listings, sorted most-urgent-first. */
export async function getMyLowStock(
  session: CommerceSession,
  opts?: { floor?: number; limit?: number },
): Promise<LowStockOut> {
  const params = new URLSearchParams();
  if (typeof opts?.floor === 'number') params.set('floor', String(opts.floor));
  if (typeof opts?.limit === 'number') params.set('limit', String(opts.limit));
  const qs = params.toString();
  const url = `${apiBase(session.commerce_url)}/sellers/me/low-stock${qs ? `?${qs}` : ''}`;
  return fetchJson<LowStockOut>(url, {
    ...crossOrigin,
    headers: authHeaders(session.token),
  });
}


// ----------------------------- shop views (§8, Chunk C) -----------------------------

/** Server response to a POST /shops/{id}/heartbeat. The client mostly ignores this — the
 *  Viewing Card doesn't render was_new_visit today. */
export interface HeartbeatOut {
  ok: true;
  was_new_visit: boolean;
  last_heartbeat_at: string;   // ISO
}

/** Live viewer count for a shop. `window_seconds` is echoed from the server so the UI can
 *  say "3 viewers in the last minute" without hard-coding what "live" means. */
export interface LiveCountOut {
  shop_id: string;
  live_count: number;
  window_seconds: number;
}

/** One hydrated live viewer row — §8 Chunk C+. The server does the identity + area +
 *  viewing-listing hydration; the FE just renders. Every field except session_id and
 *  last_heartbeat_at may be missing (anonymous viewer, no geolocation grant, not on a PDP,
 *  or not following → phone withheld). display_name falls back to "Guest" server-side, so
 *  the FE never has to synthesize a label. */
export interface LiveViewerOut {
  session_id: string;
  viewer_uuid: string | null;
  display_name: string;
  avatar_url: string | null;
  phone: string | null;                    // followers-only; server enforces
  area_label: string | null;               // e.g. 'Kilimani'; null outside Nairobi metro
  viewing_listing_id: string | null;
  viewing_listing_title: string | null;
  last_heartbeat_at: string;               // ISO
}

/** Hydrated live-viewers response — the Viewing Card's live tab feed. `count` is echoed
 *  alongside `items.length` so the small (N) counter next to the "Viewing" header stays
 *  in lock-step with the row list. */
export interface LiveViewersOut {
  shop_id: string;
  count: number;
  window_seconds: number;
  items: LiveViewerOut[];
}

/** One row of the History tab. `viewer_uuid` is null for anonymous visitors — the FE labels
 *  those as "Guest" (never surfaces the raw uuid). */
export interface ViewHistoryItem {
  id: string;
  viewer_uuid: string | null;
  session_id: string;
  viewed_at: string;              // ISO
  last_heartbeat_at: string;      // ISO
}

export interface ViewHistoryOut {
  items: ViewHistoryItem[];
  next_cursor: string | null;
}

/** Response from POST /shops/{id}/promote-all. `expires_at` tells the UI when the boost lapses. */
export interface PromoteAllOut {
  shop_id: string;
  promoted_count: number;
  skipped_ids: string[];
  duration_seconds: number;
  expires_at: string;              // ISO
}

/** Ping the shop's heartbeat endpoint. Called every 30s while a storefront is mounted. The
 *  session_id is a stable-per-browser opaque token from useShopHeartbeat.
 *
 *  Signed-in callers use their commerce session; anonymous callers pass a null session and the
 *  fetch goes without an Authorization header. The server accepts both.
 *
 *  Failures are SWALLOWED at the hook level (a lost heartbeat is not worth surfacing to the
 *  visitor); this function still throws so the hook can decide. */
/** §8 Chunk C+ extension to the heartbeat body: optional viewing_listing_id (the PDP the
 *  visitor is currently on — latest wins, null clears) and optional last_lat/last_lng
 *  (browser Geolocation grant — server drops half-coords). Every field is optional to
 *  preserve the original anonymous-storefront ping contract. */
export interface HeartbeatBodyExtras {
  viewing_listing_id?: string | null;
  last_lat?: number | null;
  last_lng?: number | null;
}

export async function postShopHeartbeat(
  session: CommerceSession | null,
  shopId: string,
  sessionId: string,
  commerceUrl: string,
  extras?: HeartbeatBodyExtras,
): Promise<HeartbeatOut> {
  const url = `${apiBase(commerceUrl)}/shops/${encodeURIComponent(shopId)}/heartbeat`;
  const body: Record<string, unknown> = { session_id: sessionId };
  if (extras) {
    // Only forward defined fields — an omitted extra must NOT arrive as a stray null, so
    // the server's "latest wins" contract doesn't clobber prior state on partial updates.
    if (extras.viewing_listing_id !== undefined) body.viewing_listing_id = extras.viewing_listing_id;
    if (extras.last_lat !== undefined) body.last_lat = extras.last_lat;
    if (extras.last_lng !== undefined) body.last_lng = extras.last_lng;
  }
  return fetchJson<HeartbeatOut>(url, {
    ...crossOrigin,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(session ? authHeaders(session.token) : {}),
    },
    body: JSON.stringify(body),
  });
}

/** Owner-only. Live count of viewers currently on the storefront. */
export async function getShopLiveCount(
  session: CommerceSession,
  shopId: string,
): Promise<LiveCountOut> {
  const url = `${apiBase(session.commerce_url)}/shops/${encodeURIComponent(shopId)}/live-count`;
  return fetchJson<LiveCountOut>(url, {
    ...crossOrigin,
    headers: authHeaders(session.token),
  });
}

/** Owner-only. Hydrated live viewers for the Viewing Card. §8 Chunk C+. */
export async function getShopLiveViewers(
  session: CommerceSession,
  shopId: string,
): Promise<LiveViewersOut> {
  const url = `${apiBase(session.commerce_url)}/shops/${encodeURIComponent(shopId)}/live-viewers`;
  return fetchJson<LiveViewersOut>(url, {
    ...crossOrigin,
    headers: authHeaders(session.token),
  });
}

/** Owner-only. Keyset-paginated view history. `since` / `until` are ISO timestamps for the
 *  calendar filter. */
export async function getShopViewHistory(
  session: CommerceSession,
  shopId: string,
  opts?: { since?: string | null; until?: string | null; cursor?: string | null; limit?: number },
): Promise<ViewHistoryOut> {
  const params = new URLSearchParams();
  if (opts?.since) params.set('since', opts.since);
  if (opts?.until) params.set('until', opts.until);
  if (opts?.cursor) params.set('cursor', opts.cursor);
  if (opts?.limit != null) params.set('limit', String(opts.limit));
  const qs = params.toString();
  const url =
    `${apiBase(session.commerce_url)}/shops/${encodeURIComponent(shopId)}/view-history` +
    (qs ? `?${qs}` : '');
  return fetchJson<ViewHistoryOut>(url, {
    ...crossOrigin,
    headers: authHeaders(session.token),
  });
}

/** Owner-only. Boosts every active, in-stock listing in the shop with an evergreen promotion
 *  for the requested duration (server-bounded 5min..24h). Out-of-stock and inactive listings
 *  are skipped by the server. */
export async function promoteAllShopListings(
  session: CommerceSession,
  shopId: string,
  durationSeconds: number,
): Promise<PromoteAllOut> {
  const url =
    `${apiBase(session.commerce_url)}/shops/${encodeURIComponent(shopId)}/promote-all` +
    `?duration_seconds=${encodeURIComponent(String(durationSeconds))}`;
  return fetchJson<PromoteAllOut>(url, {
    ...crossOrigin,
    method: 'POST',
    headers: authHeaders(session.token),
  });
}

// ----------------------------- display helpers (pure) -----------------------------

/** A neutral label for a commenter/asker whose token carried no name (older rows, or a token
 *  minted before the name claim existed). We NEVER show the raw user id in the UI. */
export const ANON_USER_LABEL = 'Weespas user';

/** Max stored description length — mirrors the commerce schema cap (the server is the authority
 *  with a 422; this is a friendly client-side guard on the seller's textarea). */
export const DESCRIPTION_MAX_LEN = 2000;

/** The collapsed preview length for a product description on the feed card. The card shows up to
 *  this many characters then a "read more" expander reveals the rest. */
export const DESCRIPTION_PREVIEW_LEN = 150;

/** Whether a description is long enough to need a "read more" expander. */
export function needsTruncation(text: string, limit: number = DESCRIPTION_PREVIEW_LEN): boolean {
  return text.trim().length > limit;
}

/** Build the collapsed preview: the first `limit` characters, cut on a word boundary where one
 *  is reasonably close (avoids slicing mid-word) and trailing punctuation/space trimmed. Pure —
 *  the caller appends the "…read more" affordance. Returns the whole text when short enough. */
export function previewText(text: string, limit: number = DESCRIPTION_PREVIEW_LEN): string {
  const t = text.trim();
  if (t.length <= limit) return t;
  const hard = t.slice(0, limit);
  const lastSpace = hard.lastIndexOf(' ');
  // Prefer a word boundary if it isn't too far back (keep ≥ 80% of the budget), else hard cut.
  const cut = lastSpace > limit * 0.8 ? hard.slice(0, lastSpace) : hard;
  return cut.replace(/[\s.,;:!-]+$/, '');
}

/** Resolve a display name from a snapshot that may be null/blank → the neutral fallback. */
export function displayName(name: string | null | undefined): string {
  const trimmed = (name ?? '').trim();
  return trimmed || ANON_USER_LABEL;
}

/** Format integer minor units → a human price. Commerce stores integer cents (S9); KES has no
 *  sub-unit in practice, so we show whole units with a thousands separator. */
export function formatPrice(priceCents: number, currency: string): string {
  const major = Math.round(priceCents / 100);
  return `${currency} ${major.toLocaleString()}`;
}

/** A short, honest distance label. Under 1 km → metres (rounded to 10 m); else kilometres (1 dp).
 *  A national (Sovereign-boosted) item can be far — we never hide that. */
export function formatDistance(distanceM: number): string {
  if (distanceM < 1000) return `${Math.round(distanceM / 10) * 10} m away`;
  return `${(distanceM / 1000).toFixed(1)} km away`;
}

/** Parse a price entered in MAJOR units (e.g. "150" or "150.50" KES) into integer minor units
 *  (cents) for the commerce API, which stores integer money only (S9 — never a float). Returns
 *  null on a non-numeric / negative input so the caller can reject before sending. */
export function majorToCents(value: string | number): number | null {
  const n = typeof value === 'number' ? value : parseFloat(value.trim());
  if (!Number.isFinite(n) || n < 0) return null;
  return Math.round(n * 100);
}

/** Inverse of majorToCents: integer minor units → a MAJOR-unit string for prefilling a price
 *  input (e.g. 15050 → "150.5", 15000 → "150"). Drops a trailing ".00" so a whole price shows
 *  cleanly. Used by the edit form to seed the field from the stored cents. */
export function centsToMajor(priceCents: number): string {
  const major = priceCents / 100;
  return Number.isInteger(major) ? String(major) : major.toFixed(2).replace(/\.?0+$/, '');
}

// ===================================================================================
// SELLER CONSOLE (FE-2) — the seller write-path client (architecture §8 / §9).
//
// Every function below EXCEPT uploadTradeMedia talks to the COMMERCE service with the RS256
// session token (create:trades scope) — same base/credentials discipline as the feed.
// ===================================================================================

export type PricingMode = 'fixed' | 'bargain';

// ----------------------------- seller DTOs -----------------------------

export interface ShopCreate {
  name: string;
  lat: number;
  lng: number;
  display_name: string;
  /** Trade category slug (§8) — one of CATEGORY_SLUGS (utils/categories.ts); omit/undefined =
   *  un-categorised. The server allow-list-validates it (422 on an unknown value). */
  category?: string | null;
  /** Shop profile picture / LOGO — a /uploads URL from uploadTradeMedia. Doubles as the business
   *  logo shown on promoted cards. Omit ⇒ initials fallback. */
  avatar_url?: string | null;
  /** Wide banner / cover image for the shop profile — a /uploads URL. Omit ⇒ plain header. */
  banner_url?: string | null;
  property_uuid?: string | null;
}

export interface ShopOut {
  id: string;
  seller_id: string;
  name: string;
  avatar_url: string | null;
  banner_url: string | null;
  /** Shareable URL slug (§8 storefront: /shop/<handle>). Null until the seller claims one; once
   *  set it is PERMANENT (one-shot policy — a rename would break every previously-shared link).
   *  When null, the frontend falls back to /shop/<seller_id> so every shop has a shareable URL. */
  handle: string | null;
  property_uuid: string | null;
  lat: number;
  lng: number;
  created_at: string;
}

export interface ListingCreate {
  title: string;
  description?: string | null;  // free-text, paragraphs preserved; capped at DESCRIPTION_MAX_LEN
  price_cents: number;          // integer minor units (use majorToCents)
  currency?: string;            // default KES
  media_urls?: string[];        // pre-existing /uploads URLs from uploadTradeMedia
  intent_weight?: number;       // 0..1, default 1.0
  stock_qty?: number;
  low_stock_threshold?: number;
  pricing_mode?: PricingMode;
  is_short_video?: boolean;     // §8 declared post kind
  post_kind?: PostKind;         // §8 timeline kind; defaults to 'product' server-side
  property_uuid?: string | null;
}

/** Partial edit of a listing (PATCH /listings/{id}). Every field is optional — only the keys
 *  actually present are changed server-side (an omitted key is left untouched; an explicit
 *  description:null clears it). Stock is NOT edited here (it has the dedicated POS adjustStock).
 *  Commerce-only fields on a plain POST are ignored server-side (a post stays price-less). */
export interface ListingUpdate {
  title?: string;
  description?: string | null;
  price_cents?: number;
  media_urls?: string[];
  intent_weight?: number;
  low_stock_threshold?: number;
  pricing_mode?: PricingMode;
  is_short_video?: boolean;
}

/** A plain social POST (§8 timeline) — text + optional media, no price/stock. Published to the
 *  caller's auto-provisioned personal shop; surfaces in the feed like a product but with no
 *  commerce chrome. */
export interface PostCreate {
  body: string;                 // the post text (required); paragraphs preserved
  media_urls?: string[];        // pre-existing /uploads URLs from uploadTradeMedia
  is_short_video?: boolean;     // mark a video post (the Videos toggle)
  lat: number;                  // anchors the post in the proximity feed (the buyer's location)
  lng: number;
  author_name?: string | null;  // display-name snapshot (server falls back to the token name claim)
}

/** The OWNER view of a listing (richer than the public FeedItem) — mirrors commerce ListingOut.
 *  Carries the derived POS + promo flags the seller dashboard renders. */
export interface ListingOut {
  id: string;
  shop_id: string;
  seller_id: string;
  property_uuid: string | null;
  title: string;
  description: string | null;
  price_cents: number;
  currency: string;
  media_urls: string[];
  intent_weight: number;
  is_active: boolean;
  stock_qty: number;
  low_stock_threshold: number;
  pricing_mode: PricingMode;
  is_short_video: boolean;
  post_kind: PostKind;
  is_out_of_stock: boolean;
  is_low_stock: boolean;
  promo_mode: 'evergreen' | 'story' | null;
  promo_started_at: string | null;
  promo_expires_at: string | null;
  is_promoted: boolean;
  // §8 flash sale — the seller's own view of a live "crazy offer" window (all null when none set).
  flash_price_cents: number | null;
  flash_started_at: string | null;
  flash_expires_at: string | null;
  flash_reference_cents: number | null;
  is_flash_active: boolean;
  created_at: string;
}

/** POS stock change — EXACTLY ONE of stock_qty (absolute) or delta (relative). The server 422s if
 *  both/neither are sent; the StockControl UI guarantees one shape per action. */
export interface StockAdjust {
  stock_qty?: number;
  delta?: number;
}

export interface StorefrontShop {
  shop: ShopOut;
  listings: ListingOut[];
}

/** The seller's OWN storefront — ALL listings incl. out-of-stock + the seller's rating. */
export interface StorefrontOut {
  seller_id: string;
  display_name: string;
  rating: number | null;
  review_count: number;
  shops: StorefrontShop[];
}

// ----------------------------- seller functions (commerce token) -----------------------------

export async function createShop(session: CommerceSession, body: ShopCreate): Promise<ShopOut> {
  return fetchJson<ShopOut>(`${apiBase(session.commerce_url)}/shops`, {
    ...crossOrigin,
    method: 'POST',
    headers: { ...authHeaders(session.token), 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function createListing(
  session: CommerceSession,
  shopId: string,
  body: ListingCreate,
): Promise<ListingOut> {
  return fetchJson<ListingOut>(
    `${apiBase(session.commerce_url)}/shops/${encodeURIComponent(shopId)}/listings`,
    {
      ...crossOrigin,
      method: 'POST',
      headers: { ...authHeaders(session.token), 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  );
}

/** Publish a plain social POST to the caller's timeline (§8). No shop needed — the server
 *  auto-provisions a personal shop on first post. Returns the post as a ListingOut (post_kind:'post'). */
export async function createPost(session: CommerceSession, body: PostCreate): Promise<ListingOut> {
  return fetchJson<ListingOut>(`${apiBase(session.commerce_url)}/posts`, {
    ...crossOrigin,
    method: 'POST',
    headers: { ...authHeaders(session.token), 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function adjustStock(
  session: CommerceSession,
  listingId: string,
  body: StockAdjust,
): Promise<ListingOut> {
  return fetchJson<ListingOut>(
    `${apiBase(session.commerce_url)}/listings/${encodeURIComponent(listingId)}/stock`,
    {
      ...crossOrigin,
      method: 'PATCH',
      headers: { ...authHeaders(session.token), 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  );
}

/** Edit the caller's listing (partial). Only the fields present in `body` change server-side.
 *  Returns the updated owner-view ListingOut. 404 if not owned; 422 on an empty patch. */
export async function updateListing(
  session: CommerceSession,
  listingId: string,
  body: ListingUpdate,
): Promise<ListingOut> {
  return fetchJson<ListingOut>(
    `${apiBase(session.commerce_url)}/listings/${encodeURIComponent(listingId)}`,
    {
      ...crossOrigin,
      method: 'PATCH',
      headers: { ...authHeaders(session.token), 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  );
}

/** Soft-delete the caller's listing (removes it from every buyer-facing lane; the row is retained
 *  inactive so order/receipt history is never orphaned). 204 on success, idempotent. */
export async function deleteListing(session: CommerceSession, listingId: string): Promise<void> {
  return fetchNoContent(
    `${apiBase(session.commerce_url)}/listings/${encodeURIComponent(listingId)}`,
    { ...crossOrigin, method: 'DELETE', headers: authHeaders(session.token) },
  );
}

/** The signed-in seller's own storefront (all shops + all listings, in- and out-of-stock). */
export async function getMyStorefront(session: CommerceSession): Promise<StorefrontOut> {
  return fetchJson<StorefrontOut>(`${apiBase(session.commerce_url)}/shops/mine`, {
    ...crossOrigin,
    headers: authHeaders(session.token),
  });
}

// ----------------------------- media upload (THE TWO-TOKEN EXCEPTION) -----------------------------

/** Upload listing media (images + an optional short video ≤250 MB) and get back /uploads URLs to
 *  pass into createListing's media_urls.
 *
 *  ⚠️ UNLIKE every other function in this file, this hits the WEESPAS backend (API_BASE_URL) with
 *  the WEESPAS session token — NOT the commerce service / commerce token. Media lives in the
 *  weespas /uploads pipeline (architecture §8: reuse it, don't rebuild). It uses the default
 *  credentials:'include' (same-origin weespas), mirroring uploadPropertyImages. Do not "fix" this
 *  to use the commerce session — commerce has no uploader and would 404/401. */
export interface TradeMediaUpload {
  uploaded: number;
  images: { url: string; thumbnail_url: string; mime_type: string; file_size: number }[];
  video: { url: string; thumbnail_url: string; mime_type: string; file_size: number } | null;
}

export async function uploadTradeMedia(
  weespasToken: string,
  files: { images: File[]; video?: File | null },
): Promise<TradeMediaUpload> {
  const form = new FormData();
  files.images.forEach((f) => form.append('images', f));
  if (files.video) form.append('video', files.video);
  return fetchJson<TradeMediaUpload>(`${API_BASE_URL}/media/trade`, {
    method: 'POST',
    headers: authHeaders(weespasToken), // NB: no Content-Type — the browser sets the multipart boundary
    credentials: 'include',
    body: form,
  });
}

// ===================================================================================
// SELLER CONSOLE FE-2b — "reach & respond": promotion, Boost (reach economy), inquiry inbox.
//
// Same COMMERCE token / base / credentials discipline as the rest of the file (NOT the two-token
// exception above — these are all commerce calls). The two 204 endpoints (revoke boost, mark
// inquiry read) can't go through fetchJson (it calls .json() on an empty body), so they use the
// fetchNoContent helper below.
// ===================================================================================

/** Fire a commerce request that returns 204 No Content (no JSON body). Mirrors fetchJson's auth
 *  failure + error handling, but never parses a body. */
async function fetchNoContent(input: RequestInfo, init?: RequestInit): Promise<void> {
  const response = await fetch(input, { credentials: 'omit', ...init });
  if (response.status === 401) {
    localStorage.removeItem('weespas_token');
    localStorage.removeItem('weespas_user');
    window.location.href = '/login';
    throw new Error('Session expired. Please log in again.');
  }
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Request failed: ${response.status} ${response.statusText} - ${errorText}`);
  }
}

// ----------------------------- promotion (§8 ephemerality) -----------------------------

/** A "selling now" promotion mode. evergreen = the boost fades on expiry but the listing stays in
 *  the feed; story = the post disappears from the feed on expiry (stock untouched). Mirrors
 *  commerce schemas.catalog.PromoMode. */
export type PromoMode = 'evergreen' | 'story';

export interface PromoteRequest {
  mode: PromoMode;
  duration_seconds: number; // 1..604800 (≤7d); the service applies the real config bounds
}

/** Open / extend a "selling now" window on the caller's listing. Re-promoting overwrites the
 *  existing window. Returns the owner ListingOut with the new promo state. */
export async function promoteListing(
  session: CommerceSession,
  listingId: string,
  body: PromoteRequest,
): Promise<ListingOut> {
  return fetchJson<ListingOut>(
    `${apiBase(session.commerce_url)}/listings/${encodeURIComponent(listingId)}/promote`,
    {
      ...crossOrigin,
      method: 'POST',
      headers: { ...authHeaders(session.token), 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  );
}

/** Remove any "selling now" window (back to an ordinary always-on listing). Idempotent. */
export async function clearPromotion(session: CommerceSession, listingId: string): Promise<ListingOut> {
  return fetchJson<ListingOut>(
    `${apiBase(session.commerce_url)}/listings/${encodeURIComponent(listingId)}/promotion`,
    { ...crossOrigin, method: 'DELETE', headers: authHeaders(session.token) },
  );
}

// ----------------------------- Flash Sale (§8 seller launch/clear) -----------------------------

export interface FlashSaleRequest {
  flash_price_cents: number;  // > 0; the crazy price (a temporary override — normal price untouched)
  duration_seconds: number;   // 1..3600 (≤1h); the service applies the real config bounds
}

/** Launch / re-launch a flash sale on the caller's listing (a nationwide, ≤1-hour crazy offer). The
 *  server rejects a non-discount, a bargain listing, or an out-of-bounds duration (422). Returns the
 *  owner ListingOut with the new flash state. */
export async function launchFlashSale(
  session: CommerceSession,
  listingId: string,
  body: FlashSaleRequest,
): Promise<ListingOut> {
  return fetchJson<ListingOut>(
    `${apiBase(session.commerce_url)}/listings/${encodeURIComponent(listingId)}/flash-sale`,
    {
      ...crossOrigin,
      method: 'POST',
      headers: { ...authHeaders(session.token), 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  );
}

/** Remove any flash sale from the caller's listing (back to its ordinary price). Idempotent. */
export async function clearFlashSale(session: CommerceSession, listingId: string): Promise<ListingOut> {
  return fetchJson<ListingOut>(
    `${apiBase(session.commerce_url)}/listings/${encodeURIComponent(listingId)}/flash-sale`,
    { ...crossOrigin, method: 'DELETE', headers: authHeaders(session.token) },
  );
}

// ----------------------------- Boost (§8.3 reach economy) -----------------------------

/** Reach tier. mtaa = 10 km (neighbourhood), hustle = 50 km, sovereign = nationwide. Mirrors
 *  commerce schemas.boost.BoostTier. */
export type BoostTier = 'mtaa' | 'hustle' | 'sovereign';
export type BoostTarget = 'listing' | 'shop';

export interface BoostRequest {
  target_type: BoostTarget;
  target_id: string;
  tier: BoostTier;
  duration_seconds?: number | null; // optional; defaults to the tier's configured window
}

export interface BoostGrantOut {
  id: string;
  seller_id: string;
  target_type: BoostTarget;
  target_id: string;
  tier: BoostTier;
  scope_kind: string;
  radius_m: number | null;
  started_at: string;
  expires_at: string;
  business_date: string;
  source: string;
}

export interface TierAllowanceOut {
  tier: BoostTier;
  daily_cap: number;
  remaining: number;
}

export interface BoostAllowancesOut {
  business_date: string;
  tiers: TierAllowanceOut[];
}

/** One tier in the server-authoritative Boost catalogue (GET /boosts/tiers). The FE chooser reads
 *  reach radius / free cap / nominal price from HERE instead of hard-coding them, so the two can
 *  never drift. Mirrors commerce schemas.boost.BoostTierMetaOut. */
export interface BoostTierMetaOut {
  tier: BoostTier;
  scope_kind: string;                    // radius vs nation scope label
  radius_m: number | null;               // null ⇒ nationwide (sovereign)
  daily_free_cap: number;                // free grants/day for this tier
  duration_default_seconds: number;      // the tier's default reach window
  price_kes: number;                     // NOMINAL, display-only (0 = free today)
}

/** The server-authoritative tier catalogue for the Boost chooser (order = narrow → wide). */
export interface BoostTiersOut {
  tiers: BoostTierMetaOut[];
}

/** Open a Boost (paid-style reach in the labelled sponsored lane — NOT a higher organic rank).
 *  Spends one of the day's free chances for the tier. 429 when the tier's chances are spent. */
export async function createBoost(session: CommerceSession, body: BoostRequest): Promise<BoostGrantOut> {
  return fetchJson<BoostGrantOut>(`${apiBase(session.commerce_url)}/boosts`, {
    ...crossOrigin,
    method: 'POST',
    headers: { ...authHeaders(session.token), 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/** The caller's remaining free Boost chances per tier for the current business day. */
export async function getBoostAllowances(session: CommerceSession): Promise<BoostAllowancesOut> {
  return fetchJson<BoostAllowancesOut>(`${apiBase(session.commerce_url)}/boosts/allowances`, {
    ...crossOrigin,
    headers: authHeaders(session.token),
  });
}

/** The server-authoritative Boost tier catalogue — reach radius, free cap, default window, nominal
 *  price. The chooser reads this instead of hard-coding reach copy/caps so the two can't drift. */
export async function getBoostTiers(session: CommerceSession): Promise<BoostTiersOut> {
  return fetchJson<BoostTiersOut>(`${apiBase(session.commerce_url)}/boosts/tiers`, {
    ...crossOrigin,
    headers: authHeaders(session.token),
  });
}

/** End a Boost early (owner-only). The spent chance is NOT refunded (reach already began). 204. */
export async function revokeBoost(session: CommerceSession, grantId: string): Promise<void> {
  return fetchNoContent(
    `${apiBase(session.commerce_url)}/boosts/${encodeURIComponent(grantId)}`,
    { method: 'DELETE', headers: authHeaders(session.token) },
  );
}

// ----------------------------- per-shop sponsored-cap override (§8.3 item 1) -----------------------------
// A shop may APPLY for an absolute per-shop cap on the labelled sponsored feed lane; STAFF approve
// or reject it. Only an approved override with a positive cap ever affects the feed. The seller
// status read is NON-DESTRUCTIVE (GET) — opening the control must not reset an approved override to
// pending (that is what the POST does). Mirrors commerce schemas.boost cap types.

export type CapOverrideStatus = 'pending' | 'approved' | 'rejected';

/** One shop's cap-override request + staff decision. Mirrors commerce schemas.boost.CapOverrideOut. */
export interface CapOverrideOut {
  id: string;
  shop_id: string;
  requested_cap: number;
  status: CapOverrideStatus;
  approved_cap: number | null;
  decided_by: string | null;
  decided_at: string | null;
}

/** The seller-facing status of their OWN shop's override. `override` is null when never applied.
 *  `max_cap` / `default_cap` are server-authoritative — the FE bounds its input and shows context
 *  from THESE, never hard-coded (anti-drift). Mirrors schemas.boost.CapOverrideStatusOut. */
export interface CapOverrideStatusOut {
  override: CapOverrideOut | null;
  max_cap: number;
  default_cap: number;
}

/** The staff review queue of pending applications, plus the server ceiling for the decide input. */
export interface PendingCapListOut {
  overrides: CapOverrideOut[];
  max_cap: number;
}

/** Non-destructive read of the caller's own shop's sponsored-cap status. 404 if not owned. */
export async function getSponsoredCapStatus(
  session: CommerceSession, shopId: string,
): Promise<CapOverrideStatusOut> {
  return fetchJson<CapOverrideStatusOut>(
    `${apiBase(session.commerce_url)}/shops/${encodeURIComponent(shopId)}/sponsored-cap`,
    { ...crossOrigin, headers: authHeaders(session.token) },
  );
}

/** Apply for a per-shop sponsored cap on the caller's own shop. Re-opens as pending (a prior
 *  decision is cleared server-side), so callers should confirm before overwriting an approval. */
export async function applySponsoredCap(
  session: CommerceSession, shopId: string, requestedCap: number,
): Promise<CapOverrideOut> {
  return fetchJson<CapOverrideOut>(
    `${apiBase(session.commerce_url)}/shops/${encodeURIComponent(shopId)}/sponsored-cap`,
    {
      ...crossOrigin,
      method: 'POST',
      headers: { ...authHeaders(session.token), 'Content-Type': 'application/json' },
      body: JSON.stringify({ requested_cap: requestedCap }),
    },
  );
}

/** Staff-only: the pending cap applications awaiting a decision (+ the server ceiling). 403 if the
 *  session token's role isn't staff/admin. */
export async function listPendingSponsoredCaps(session: CommerceSession): Promise<PendingCapListOut> {
  return fetchJson<PendingCapListOut>(
    `${apiBase(session.commerce_url)}/admin/sponsored-caps`,
    { ...crossOrigin, headers: authHeaders(session.token) },
  );
}

/** Staff-only: approve (with an absolute cap, server-clamped to max_cap) or reject a pending
 *  application. `approvedCap` is ignored on reject. */
export async function decideSponsoredCap(
  session: CommerceSession,
  overrideId: string,
  decision: { approve: boolean; approvedCap?: number | null },
): Promise<CapOverrideOut> {
  return fetchJson<CapOverrideOut>(
    `${apiBase(session.commerce_url)}/admin/sponsored-caps/${encodeURIComponent(overrideId)}/decide`,
    {
      ...crossOrigin,
      method: 'POST',
      headers: { ...authHeaders(session.token), 'Content-Type': 'application/json' },
      body: JSON.stringify({ approve: decision.approve, approved_cap: decision.approvedCap ?? null }),
    },
  );
}

// ----------------------------- inquiry inbox (seller side) -----------------------------

/** One inquiry in the seller's inbox — a buyer's "is this available?" on one of the seller's
 *  listings. Mirrors commerce schemas.engagement.InquiryOut. */
export interface InquiryOut {
  id: string;
  listing_id: string;
  listing_title: string;
  seller_id: string;
  from_user_uuid: string;
  /** Display-name snapshot from the asker's token (null on older inquiries — the UI falls back to
   *  a neutral label, never the raw uuid). */
  from_user_name: string | null;
  message: string;
  is_read: boolean;
  created_at: string;
}

export interface InquiryPage {
  items: InquiryOut[];
  next_cursor: string | null;
}

/** The signed-in seller's inquiry inbox, newest-first, keyset-paginated. */
export async function getMyInquiries(
  session: CommerceSession,
  opts: { cursor?: string | null; limit?: number } = {},
): Promise<InquiryPage> {
  const params = new URLSearchParams();
  if (opts.cursor) params.set('cursor', opts.cursor);
  if (opts.limit != null) params.set('limit', String(opts.limit));
  const qs = params.toString();
  return fetchJson<InquiryPage>(
    `${apiBase(session.commerce_url)}/me/inquiries${qs ? `?${qs}` : ''}`,
    { ...crossOrigin, headers: authHeaders(session.token) },
  );
}

/** Mark one inquiry read (recipient only; idempotent). 204. */
export async function markInquiryRead(session: CommerceSession, inquiryId: string): Promise<void> {
  return fetchNoContent(
    `${apiBase(session.commerce_url)}/inquiries/${encodeURIComponent(inquiryId)}/read`,
    { method: 'PATCH', headers: authHeaders(session.token) },
  );
}
