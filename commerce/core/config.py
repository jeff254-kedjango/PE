"""Runtime configuration for the commerce service.

Commerce is the social, proximity-native marketplace — the first service of the trading
layer (see PE/weespas_trade_architecture.md). It owns its own database and verifies
weespas-minted RS256 tokens with the PUBLIC key only (asymmetric stateless auth, zero
network calls back to weespas).

Mirrors the weespas Settings idiom (pydantic-settings BaseSettings + a module-level
``settings`` singleton + the lru_cached PEM reader). One deliberate divergence from the
InSAR read app: commerce FAILS CLOSED. The InSAR data API may run public until a key is
provisioned (it is a read-only risk map); commerce carries trade identity, so an
unconfigured key must refuse rather than silently serve unauthenticated (see auth.py S1).
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


@lru_cache(maxsize=8)
def _read_pem_cached(path: str) -> str:
    """Read a PEM file once and cache by path. Empty/blank path or a missing file ⇒ ''
    (treated as 'not configured' by callers, never an exception at import/startup).
    Copied verbatim from weespas core.config — same disk-hot-path memoization."""
    if not path:
        return ""
    p = Path(path)
    if not p.is_file():
        return ""
    return p.read_text()


class Settings(BaseSettings):
    # ---- Required (fail-fast at startup if missing) ----
    # Full SQLAlchemy URL for the commerce database, e.g.
    # postgresql+psycopg2://commerce:commerce@localhost:5432/commerce
    database_url: str

    # ---- Deployment environment ----
    # "development" (default) or "production". In production the app refuses to start
    # unless a JWT public key is configured (see main.py boot guard) — a forgotten key
    # can never silently run commerce unauthenticated.
    commerce_env: str = "development"
    debug: bool = True

    # ---- Auth: RS256 token verification (public key only) ----
    # Provide EITHER the PEM inline (commerce_jwt_public_key) or a file path
    # (commerce_jwt_public_key_path). This increment shares the weespas signing keypair
    # (PE/dev/keys/insar_jwt_*.pem) — commerce holds the PUBLIC half only and can verify
    # but never mint (see S4). The scope string is the AUDIENCE guard against cross-service
    # token replay (a telemetry or mobility token can never satisfy it — S2).
    commerce_jwt_public_key_path: str = ""
    commerce_jwt_public_key_inline: str = ""
    commerce_jwt_algorithm: str = "RS256"
    commerce_jwt_scope: str = "commerce_trade"

    # ---- CORS ----
    # Comma-separated allowed origins. Default covers the local weespas + commerce Vite
    # servers on BOTH host aliases — `localhost` and `127.0.0.1` are distinct browser origins,
    # so each Vite port needs both or a dev hitting the other alias gets a preflight rejection.
    # In prod set CORS_ORIGINS to the real frontend origin(s) only.
    cors_origins: str = (
        "http://localhost:5173,http://localhost:5174,http://localhost:5175,"
        "http://127.0.0.1:5173,http://127.0.0.1:5174,http://127.0.0.1:5175"
    )

    # ---- Redis (revocation denylist on the settlement path — §2/S5) ----
    redis_url: str = "redis://localhost:6379/3"

    # ---- Settlement (§6/§7 — the LEDGER, not the live M-Pesa rail) ----
    # Which payment rail to use. ONLY "stub" exists today (inert, moves no money). The real
    # "daraja" rail is blocked on the §6 party-direct/B2C research; requesting it raises in
    # services.payment_rail.get_rail so a misconfig can never silently move real money.
    payment_rail: str = "stub"
    # Commission in basis points: 300 bps = 3%. commission = locked_price_cents * bps // 10_000
    # (integer floor — deterministic, never a float; S9). Single source of the rate.
    commission_bps: int = 300
    # Bargain bounds (§7 guardrails): cap counter rounds so haggling can't DoS the seller, and
    # bound offers to a sane multiple of the listing reference so an offer can't be a fraud /
    # fat-finger vector. An offer must be >= 1 cent and <= bargain_max_multiple × reference.
    bargain_max_rounds: int = 3
    bargain_max_multiple: float = 10.0
    # A pending negotiation holds a resource (the seller's attention) — it auto-expires after
    # this TTL. settlement.expire_stale does the sweep; services.expiry_sweeper runs it
    # periodically as a standalone process (see expiry_sweep_* below).
    pending_ttl_seconds: int = 3600

    # ---- Expiry sweeper (standalone process — not Celery; commerce keeps the lean sync stack) ----
    # How often the sweeper runs expire_stale. Default 5 min: with a 1h TTL, worst-case a pending
    # order lingers TTL + interval before expiring — acceptable, and avoids a hot poll loop.
    expiry_sweep_interval_seconds: int = 300
    # A guard the process honours at startup: set false to run the sweeper as a no-op (e.g. to
    # disable expiry in an environment without changing the deployment).
    expiry_sweep_enabled: bool = True

    # ---- Proximity feed tuning (the "algorithm" — all explainable, tunable constants) ----
    feed_default_radius_m: float = 2000.0   # 2 km default "people next door" radius
    feed_max_radius_m: float = 20000.0      # hard server cap (anti-O(n) — S8)
    feed_page_size: int = 20
    feed_max_page_size: int = 50
    # Sparse-feed reach threshold: when the immediate radius yields FEWER than this many candidates,
    # build_feed widens ONCE to feed_max_radius_m to top the page up with the nearest content (a
    # thin locality would otherwise show a near-empty page even with sellers a few km out). A FIXED
    # server constant, deliberately NOT the per-request page size — else "sparse" would depend on the
    # client's requested limit and two buyers at one spot could see different feeds. Defaults to one
    # page so a healthy local market (≥ a page nearby) never widens; only genuinely thin areas do.
    feed_sparse_threshold: int = 20
    feed_max_candidates: int = 500          # bounds k so score+sort stays O(k)
    feed_w_distance: float = 0.5            # weight: proximity
    feed_w_freshness: float = 0.3           # weight: recency
    feed_w_intent: float = 0.2              # weight: seller intent ("selling now")
    feed_freshness_halflife_h: float = 24.0 # exponential decay half-life (hours)
    # §8 ephemerality: additive boost for a listing with a live "selling now" window. Kept modest
    # (0.25) so promotion is a freshness nudge, NOT a pay-to-win override of proximity — it cannot
    # let a far promoted listing bury a close un-promoted one (w_distance dominates). Decays to 0
    # over the window (ranking.promo_boost).
    feed_w_promo: float = 0.25
    # Soft media-presence nudge: an image/video-bearing listing gets this additive bonus over an
    # otherwise-equal imageless one. A QUALITY signal, never a filter (a plain social text post has
    # no media and is simply not nudged). Kept well below w_distance (0.5) so proximity is never
    # overridden — a closer imageless item still outranks a far image-rich one. Decays nothing; it's
    # a static property of the listing's stored media_urls (see ranking.has_media).
    feed_w_media: float = 0.15

    # ---- §8 "Quick Buys" grid (the Trade right-rail 3×3 discovery grid) ----
    # A deliberate near/outer MIX per page: near_per_page items from the immediate radius, the rest
    # from beyond it matched to the buyer's own historical interest (backfilled by trending, then
    # recency). All bounded → O(k), never a scan (S8). The near radius is the "immediate 5 km"; a
    # caller-supplied radius filter overrides it but is still clamped to feed_max_radius_m.
    quick_buys_near_radius_m: float = 5000.0   # the "immediate 5 km" near bucket
    quick_buys_page_size: int = 9              # 3 columns × 3 rows
    quick_buys_near_per_page: int = 4          # 4/9 near, 5/9 outer (interest/trending)
    quick_buys_max: int = 36                   # total composed items (client pages over these)
    # Bound on the recent-engagement rows scanned per signal (saves/orders/inquiries/comments) when
    # deriving the buyer's category affinity — keeps affinity O(bounded), never a full history scan.
    quick_buys_affinity_lookback: int = 100

    # ---- "Selling now" promotion bounds (§8) — anti-abuse window limits ----
    # A promotion window must be at least this long (reject a 0/blip window) and at most this long
    # (a "selling now" post that never decays is just an ad — cap it; the seller can re-promote).
    promo_min_duration_seconds: int = 300        # 5 min
    promo_max_duration_seconds: int = 86_400     # 24 h

    # ---- §8 Flash Sales (the nationwide "crazy offer" grid under Quick Buys) ----
    # A flash sale is a NATIONWIDE, one-hour-max "crazy offer" ("Bread for 10 KES"), ranked by a
    # precomputed MARGIN "craziness" score (offer vs comparable-shop average). All the heavy work —
    # the comparable pull — happens ONCE at launch and is bounded (LIMIT k); the read is a pure
    # indexed ORDER BY flash_score, never a scan (S8).
    flash_sales_max_duration_seconds: int = 3600   # 1 h hard cap (then it vanishes; seller re-launches)
    flash_sales_min_duration_seconds: int = 60     # reject a 0/blip window
    flash_sales_page_size: int = 6                 # 3 columns × 2 rows
    flash_sales_max: int = 24                      # total nationwide items surfaced (client pages over these)
    # Comparable-pull bounds (launch-time margin computation). The comparable set is same-category
    # listings NEAR the seller's own shop; if too few, fall back to nationwide same-category. Every
    # branch is LIMIT-bounded so the launch stays O(k), never a table scan.
    flash_sales_comparable_radius_m: float = 5000.0  # "around that area" — the seller's own locality
    flash_sales_comparable_limit: int = 12           # k: candidates pulled per branch (bounded)
    flash_sales_min_comparables: int = 3             # below this near, widen to the nationwide fallback
    flash_sales_reference_sample: int = 3            # average the ~3 closest comparables for the reference

    # ---- §8.3 Boost tiers & reach economy ----
    # Free daily allowances per tier (the "chances"). A new business day resets them (a new
    # BoostAllowance row at used=0). Paid adverts (later) bypass these via source='paid'.
    boost_mtaa_daily_free: int = 10
    boost_hustle_daily_free: int = 8
    boost_sovereign_daily_free: int = 3
    # Tier reach radii (metres). Sovereign is nationwide (no radius). These are the scope snapshot
    # written onto the grant at open time.
    boost_mtaa_radius_m: float = 10_000.0        # 10 km — the neighbourhood ("mtaa")
    boost_hustle_radius_m: float = 50_000.0      # 50 km — the wider hustle area
    # A boost window length. Bounded like the ephemerality window (anti-abuse): a chance buys a
    # bounded burst of reach, not a permanent ad.
    boost_default_duration_seconds: int = 86_400     # 24 h
    boost_min_duration_seconds: int = 300            # 5 min
    boost_max_duration_seconds: int = 604_800        # 7 days (paid campaigns can run longer later)
    # The IANA timezone whose civil midnight bounds the daily quota + business_date bucket. Kenya
    # has no DST, but using a named zone keeps the reset honest if the deploy region differs.
    boost_business_tz: str = "Africa/Nairobi"

    # ---- Sponsored lane (the bounded, labelled feed injection — §8.3) ----
    # One sponsored slot every N organic items (the relevance dial). 0 disables the sponsored lane
    # entirely (organic-only feed). Tuned against buyer conversion once live.
    feed_sponsored_every_n: int = 5
    # Hard cap on the sponsored-candidate pull so a nationwide grant set can never make the feed
    # O(everyone): top-K scope-containing live grants, GiST-indexed. K bounds the lane's cost.
    feed_sponsored_max_candidates: int = 200
    # Cap on how many sponsored slots stand ALONE when the buyer's local organic feed is empty (a
    # far/sparse locality — exactly who nationwide Sovereign reach serves). Bounds the lane so an
    # empty area is never a full ad wall, while still surfacing the promotions that paid to reach it.
    feed_sponsored_max_on_empty: int = 10
    # Fairness cap: the most sponsored slots ONE shop may occupy on a single feed page, so a shop
    # boosting many listings can't flood the lane and crowd out other boosted shops (§8.3 Boost =
    # paid REACH, not a takeover). The 1-vs-2 behaviour falls out automatically: a shop boosting one
    # distinct listing naturally fills 1 slot (it has nothing else to show); a shop boosting ≥2
    # distinct listings may fill up to this cap. 0 disables the cap. NOTE: a future increment lets
    # a shop APPLY for a per-shop override that staff approve over the existing Notification / flag-
    # review channels (admin-tunable, no code change) — that override would multiply this default.
    feed_sponsored_max_per_shop: int = 2
    # A shop may APPLY for a per-shop OVERRIDE of the cap above (staff approve an ABSOLUTE cap that
    # replaces the default for that shop only). This is the hard ceiling on a requestable/approvable
    # cap — anti-abuse, so even an approving staffer can't hand out an unbounded ad-wall. The apply
    # + admin-decide flow is commerce-side (commerce owns no weespas notification queue); see
    # services/boost_cap.py. Only an APPROVED override with a positive cap ever affects the feed.
    boost_cap_override_max: int = 10

    # ---- Sponsored-slot LOTTERY (fill-rate; §8.3 documented tuning layer) ----
    # OFF by default: the sponsored lane is deterministic (widest tier → nearest → id), cursor-safe.
    # When enabled, a tier-weighted weighted-shuffle reorders the BOUNDED sponsored set BEFORE the
    # per-shop cap (wider tiers weighted higher, matching models.boost.TIER_WEIGHT). The randomness
    # is confined to this first-page-only sponsored lane — the ORGANIC lane is NEVER touched (the
    # cardinal rule). The seed is derived per (buyer-bucket, business-date) so a given buyer sees a
    # STABLE order within a day (reproducible, cursor-safe) — never wall-clock/PRNG-global entropy.
    # feed_sponsored_lottery_seed, when set, pins the seed globally (tests + deterministic demos).
    feed_sponsored_lottery_enabled: bool = False
    feed_sponsored_lottery_seed: int | None = None

    # ---- Admin (staff-gated commerce endpoints) ----
    # Roles (from the token's `role` claim) permitted to act on staff-only endpoints — approving a
    # per-shop cap override, moderating, etc. CSV env → tuple via admin_roles_tuple. Fail-closed:
    # a caller whose role is not in this set gets 403 (see core.auth._require_staff).
    admin_roles: str = "staff,admin"

    # ---- §8.3 Boost NOMINAL pricing (config-only display seam — NOT charged) ----
    # A per-tier price in KES, purely a forward-compatible DISPLAY/seam value surfaced by
    # GET /boosts/tiers. **No code path charges this** — real paid Boost is blocked on the §6
    # Daraja rail research (payment_rail.py stays an inert stub; the free daily-quota path in
    # services/boost.py is unchanged). 0 = the current free reality. When a charge rail lands, a
    # paid grant (source='paid') will read these — until then they are informational only. CSV of
    # tier:price pairs → dict via boost_tier_price_kes_map (avoids pydantic JSON-parsing a raw dict).
    boost_tier_price_kes: str = "mtaa:0,hustle:0,sovereign:0"

    # ---- Global text search (navbar unified search — trade half) ----
    # The navbar's magnifier searches BOTH weespas properties (their existing FTS) and trade
    # listings. Trade had no text search before this — it is proximity-native — so this endpoint
    # is a keyword match RANKED by proximity (nationwide reach, nearest-first): a buyer finds a
    # named product anywhere in the country, with the closest sellers surfaced first. All bounded
    # (LIMIT k), never an O(n) scan (S8) — a trigram GIN index backs the match in prod.
    #   * min query len: below this the endpoint returns empty (a 1-char query matches ~everything;
    #     the client also debounces + gates on length, this is the server-side backstop).
    #   * max results: hard server cap on the returned page (a caller can't request a huge page).
    #   * max candidates: bound on the rows the text filter pulls before the nearest-first sort —
    #     the anti-O(n) ceiling (mirrors feed_max_candidates); a saturation warning fires if hit.
    search_min_query_len: int = 2
    search_max_results: int = 30
    search_max_candidates: int = 200

    # ---- §8 trending rail (boosted PRODUCTS, per-slot decay board) ----
    # The rail shows boosted LISTINGS (products) near the buyer as category-colored cards: title +
    # price + a category icon (e.g. lunchtime "Nyama Choma / KES 350 / 🥩"). The server returns the
    # full bounded QUEUE of eligible product cards per locality bucket (a PURE function of the bucket
    # — all viewers in a cell share one Redis-cached queue, TTL = poll_seconds). The CLIENT owns the
    # animation: a fixed set of `visible_cap` slots, each decaying on its own staggered timer, the
    # next queued product taking the freed slot — so airtime is fair without a server-side rotation.
    #   * base slot: per-card lifetime in a QUIET locality (boosted products ≤ cap). A lone product
    #     persists; nothing churns when there's room for everyone.
    #   * min slot:  the floor the lifetime shrinks to under contention (more products than cap).
    #     Kept > 5 s so a card is always readable.
    #   * visible cap: how many slots show at once (bounds what the client animates).
    #   * poll seconds: the client's re-poll cadence AND the server cache TTL (queue membership
    #     changes slowly; the decay is client-local so the cache need not track sub-slot windows).
    #   * bucket m:   the locality cell size; (lat,lng) is snapped to this grid's centre so nearby
    #     buyers share one cache entry (and identical, bucket-centre distances).
    trending_base_slot_s: int = 12
    trending_min_slot_s: int = 6
    trending_visible_cap: int = 12
    trending_poll_seconds: int = 20
    trending_bucket_m: float = 1500.0

    # ---- Trending demo seeder (LOCAL/DEMO ONLY — never production) ----
    # A standalone background process (services/trending_demo.py, run via
    # PE/dev/commerce-trending-demo.sh) that keeps a FIXED pool of boosted PRODUCTS near a demo
    # centre alive so the trending rail is populated in a dev stack — without an agent hand-driving a
    # seed script. It fabricates synthetic sellers/listings/boosts, so it is DOUBLE-gated off in
    # production: this flag defaults False AND the process hard-refuses to start when
    # is_production() (see trending_demo.run_forever). It mirrors the expiry_sweeper lifecycle
    # (Event + signals, own session per tick) and auto-revokes its boosts on shutdown.
    #
    # FIXED POOL (not a growing generation loop): the seeder creates ``pool_size`` demo products
    # ONCE (idempotent — stable per-slot user_uuids, reused on every run) and then merely KEEPS THEIR
    # BOOSTS ALIVE. It never creates a new seller/shop/listing per cycle (that old design leaked rows
    # unboundedly — boosts were capped but the underlying rows were not). The rail's visible cycling
    # is owned CLIENT-side (useTrendingRotation decays slots + pulls the next queued product), so the
    # backend only needs the pool's boosts to exist, not to churn rows.
    #   * pool_size: how many demo products exist (keep ABOVE trending_visible_cap so the client rail
    #     is always in the looping/contention regime). Created once, reused forever.
    #   * refresh seconds: each tick re-grants any pool boost that is missing/near expiry. Re-granting
    #     the SAME (listing, tier, business-day) replays the existing grant (boost.grant_boost is
    #     idempotent) — no new allowance spent, no new row — so a steady pool costs ~nothing.
    #   * center/jitter: products are scattered within jitter_deg of the centre (still inside one
    #     bucket and the 10km mtaa radius) so distances vary on the cards.
    trending_demo_enabled: bool = False
    trending_demo_pool_size: int = 50
    trending_demo_refresh_seconds: int = 300
    # Kilimani (the InSAR Kilimani AOI centre — aois.py) — deliberately ~4.5 km from the CBD FE
    # default (-1.2921, 36.8219) so the synthetic demo pool sits OUTSIDE the 2 km default feed
    # radius. This keeps the ~50 imageless demo products from flooding a fresh buyer's immediate
    # feed at the CBD default while still populating the trending rail for a Kilimani-centred demo.
    # trending_demo.py relocates any existing pool rows here IN PLACE on startup (no delete — the
    # settlement ledger forbids hard-deleting listings; see its _relocate_pool heal).
    trending_demo_center_lat: float = -1.2900   # Kilimani AOI centre
    trending_demo_center_lng: float = 36.7870
    trending_demo_jitter_deg: float = 0.01

    # ── Live-viewer DEMO seeder (services/viewer_demo.py, run via PE/dev/commerce-viewer-demo.sh)
    # Keeps a rotating population of LIVE viewers on every real shop so the seller console's
    # Viewing Card has something to show in a dev stack. Same double-gate as the trending seeder:
    # this flag defaults False AND run_forever() hard-refuses under is_production().
    #
    # It MUST be a looping process, not a one-shot script: shop_views.LIVE_WINDOW_SECONDS is 60, so
    # a seeded viewer disappears 60s after its last heartbeat. The loop re-heartbeats instead.
    #
    # Viewer IDENTITIES are real weespas user uuids (read-only, from the weespas DB) rather than
    # fabricated ones — commerce stores only an opaque viewer_uuid and the Viewing Card resolves
    # names/avatars over the S2S bridge. A made-up uuid resolves to nothing and every row would
    # render as 'Guest', defeating the point of seeding.
    #   * refresh seconds: must stay comfortably UNDER LIVE_WINDOW_SECONDS or the population will
    #     visibly flicker between ticks as rows age out before being refreshed.
    #   * viewers_per_shop: how many concurrent live viewers each shop shows.
    #   * churn: how many of those slots are replaced with a different viewer each tick, so the
    #     card demonstrates arrivals/departures instead of a frozen list. 0 = a static population.
    viewer_demo_enabled: bool = False
    viewer_demo_refresh_seconds: int = 20
    viewer_demo_viewers_per_shop: int = 4
    viewer_demo_churn_per_tick: int = 1

    # §8 Chunk C+ — commerce → weespas S2S bridge for humanized live viewers. Both settings
    # must be present for the /shops/{id}/live-viewers endpoint to hydrate viewer identity;
    # missing config means graceful degradation (every viewer shown as 'Guest', no crash).
    #
    # weespas_url:                the base origin (no /api prefix) of the weespas service.
    #                              e.g. http://localhost:8000 in dev.
    # weespas_users_lookup_secret: the shared secret. Set the SAME value on both services:
    #                              here as env WEESPAS_USERS_LOOKUP_SECRET, and on weespas as
    #                              env COMMERCE_USERS_LOOKUP_SECRET (each service's var is
    #                              named for the PEER it authenticates). PE/dev/dev.env sets
    #                              both from one place in local dev.
    #                              Blank ⇒ the bridge is DISABLED and every viewer shown as
    #                              'Guest' (fail-open on identity, fail-closed on the bridge
    #                              itself: an unset secret NEVER makes a call).
    # weespas_lookup_timeout_s:    hard httpx timeout. Deliberately short — a hung weespas
    #                              must never make the seller's card unresponsive; a
    #                              timeout is treated the same as a bridge outage
    #                              (viewer summaries drop to 'Guest', endpoint returns 200).
    weespas_url: str = "http://localhost:8000"
    weespas_users_lookup_secret: str = ""
    weespas_lookup_timeout_s: float = 2.0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # ---- Derived ----
    @property
    def cors_origins_list(self) -> list[str]:
        """CSV → list (used by CORSMiddleware)."""
        return [o.strip() for o in (self.cors_origins or "").split(",") if o.strip()]

    @property
    def admin_roles_tuple(self) -> tuple[str, ...]:
        """CSV → tuple of lower-cased role names (the staff-endpoint allow-set). Lower-cased so the
        role-claim comparison is case-insensitive; empty entries dropped (a blank env ⇒ no staff)."""
        return tuple(r.strip().lower() for r in (self.admin_roles or "").split(",") if r.strip())

    @property
    def boost_tier_price_kes_map(self) -> dict[str, int]:
        """CSV of ``tier:price`` pairs → {tier: price_kes}. DISPLAY-ONLY (never charged; see the
        field docstring). A malformed pair is skipped rather than crashing config load — a bad
        price must never take the service down, and a missing tier just reads as free (0) at the
        call site. Negative prices are floored to 0."""
        out: dict[str, int] = {}
        for pair in (self.boost_tier_price_kes or "").split(","):
            pair = pair.strip()
            if not pair or ":" not in pair:
                continue
            tier, _, raw = pair.partition(":")
            tier = tier.strip().lower()
            try:
                price = int(raw.strip())
            except ValueError:
                continue
            if tier:
                out[tier] = max(0, price)
        return out

    def is_production(self) -> bool:
        return self.commerce_env.strip().lower().startswith("prod")

    @property
    def commerce_jwt_public_key(self) -> str:
        """RSA public PEM used to verify commerce tokens, or '' when not configured.
        Inline value wins over the file path; a single-line env var may carry literal
        '\\n' escapes (common in CI/secret stores). Memoized via _read_pem_cached for
        the path branch — the verify hot path must not touch disk."""
        inline = (self.commerce_jwt_public_key_inline or "").strip()
        if inline:
            return inline.replace("\\n", "\n")
        return _read_pem_cached(self.commerce_jwt_public_key_path)

    @property
    def auth_enabled(self) -> bool:
        """True when a public key is configured. Commerce fails CLOSED at request time
        when this is False (auth.py raises 503) and refuses to BOOT in production
        (main.py) — unlike the InSAR read app which stays inert-public."""
        return bool(self.commerce_jwt_public_key)


settings = Settings()
