from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


@lru_cache(maxsize=8)
def _read_pem_cached(path: str) -> str:
    """Read a PEM file once and cache by path. Empty/blank path or a missing file ⇒ ''
    (treated as 'not configured' by callers, never an exception at import/startup)."""
    if not path:
        return ""
    p = Path(path)
    if not p.is_file():
        return ""
    return p.read_text()


class Settings(BaseSettings):
    # Required — no default on purpose. These used to ship as hard-coded
    # literals (a real DB password + a guessable JWT secret), which meant the
    # app ran with insecure credentials whenever `.env` was absent. They are
    # now mandatory env vars: the app fails fast at startup if either is
    # missing. Set both in `.env` (see `.env.example`). DATABASE_URL is the
    # full SQLAlchemy URL; SECRET_KEY signs JWTs — rotating it logs everyone out.
    database_url: str
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    debug: bool = True

    # Africa's Talking SMS
    at_username: str = "sandbox"
    at_api_key: str | None = None
    # Empty in sandbox; set to your approved alphanumeric sender ID in prod
    # (e.g. "WEESPAS"). Passed through to africastalking.SMS.send when set.
    at_sender_id: str = ""

    # Analytics / GeoIP
    geoip_db_path: str = "data/GeoLite2-City.mmdb"
    analytics_cache_ttl: int = 300  # seconds

    # Session cookie hardening. `cookie_secure=True` forces the browser to
    # only send `weespas_session` over HTTPS — required in prod and for the
    # `SameSite=None` fallback that some embedded contexts need. Stays
    # False in dev so the cookie works on `http://localhost`. Set via env
    # var `COOKIE_SECURE=true` in production.
    #
    # Why this matters: without it, the per-visitor session cookie was
    # being silently dropped on HTTPS by some browser/edge configs. The
    # middleware then created a fresh `user_sessions` row on every request
    # (`last_seen_at == created_at`), so /analytics/engagement aggregated
    # to all-zero series. See `compute_engagement()` in analytics_service.py.
    cookie_secure: bool = False

    # CORS allow-list (browser origins permitted to call the API with credentials).
    # Comma-separated in .env (CORS_ORIGINS). Default = the local Vite dev ports so
    # dev is unchanged; in prod set CORS_ORIGINS to the real frontend origin(s) only.
    # See cors_origins_list for the parsed form used by main.py.
    cors_origins: str = (
        "http://localhost:5173,http://localhost:5174,http://localhost:5175,"
        "http://localhost:5176,http://localhost:3000,"
        "http://127.0.0.1:5173,http://127.0.0.1:5174,http://127.0.0.1:5175,"
        "http://127.0.0.1:5176,http://127.0.0.1:3000"
    )

    # Redis (shared cache)
    redis_url: str = "redis://localhost:6379/0"
    feed_cache_ttl: int = 300  # seconds — personalized feed cache TTL

    # Celery broker + backend. Split from app cache so a flood of analytics
    # results never starves the personalization-feed Redis. In dev they
    # default to the same instance on a different DB index.
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/1"

    # ---- Per-feature Celery flags ----
    # Flip these on one at a time in prod with a 15-min watch window so we
    # can roll back a single offload without redeploying. Each new task
    # path keeps the old synchronous fallback (see services/celery_helpers.py).
    celery_send_otp_enabled: bool = False         # Phase 1.1
    celery_log_search_enabled: bool = False       # Phase 1.2
    celery_record_view_enabled: bool = False      # Phase 1.3
    celery_session_geo_enabled: bool = False      # Phase 2.1
    celery_last_seen_enabled: bool = False        # Phase 2.3
    celery_beat_enabled: bool = False             # Phase 3 — toggles SWR read path
    celery_feed_warm_enabled: bool = False        # Phase 4

    # SWR (stale-while-revalidate) tuning — analytics blobs have an internal
    # `computed_at` stamp; if older than ttl × ratio, the read path triggers
    # a background refresh while still serving the stale value.
    analytics_swr_refresh_ratio: float = 0.5

    # ---- P4a: InSAR integration ----
    # Path to the InSAR read-only DuckDB (the building footprints + danger tiers the
    # resolver matches listings against). Empty ⇒ integration disabled: the resolver
    # returns "not monitored" for every listing rather than guessing. Read-only.
    insar_duckdb_path: str = ""
    # Max metres a listing point may sit outside a footprint and still resolve to it
    # (nearest-fallback radius). Beyond this ⇒ "not monitored", never a wrong match.
    insar_match_radius_m: float = 30.0
    # --- Disambiguating resolver (the "bad pin" problem) ---
    # Radius (m) in which ALL candidate footprints are gathered for attribute-aware
    # ranking. A dropped pin in dense areas sits near several buildings; we score every
    # footprint in this buffer rather than blindly taking the nearest.
    insar_buffer_radius_m: float = 15.0
    # Radius (m) over which a `land` listing's ground movement is estimated from
    # neighbouring monitored buildings (land has no footprint of its own to read).
    insar_land_aggregate_radius_m: float = 40.0
    # If the top-2 candidate scores are within this gap, the match is AMBIGUOUS →
    # needs_confirmation (the owner taps the right building) rather than an auto-snap.
    insar_ambiguity_score_gap: float = 0.15
    # Max candidates persisted/offered for confirmation per listing (UI shows 2-4).
    insar_max_candidates: int = 6
    # Directory the structural-flag exporter writes <aoi>.json into — the same dir
    # the InSAR build's fetch_structural_flags() reads. Empty ⇒ export disabled.
    insar_flags_export_dir: str = ""
    # InSAR pipeline control-API base URL (the Phase 3 control plane) + its admin
    # token. When both are set, recording a flag also fires a DEBOUNCED rebuild of
    # that AOI so the new flag reaches the score without a manual re-seed. Empty URL
    # ⇒ no auto-rebuild (export still happens; operator rebuilds when ready).
    insar_control_api_url: str = ""
    insar_admin_token: str = ""
    # Best-effort trigger timeout (seconds) — kept short; the rebuild is async on
    # the InSAR side, we only need the enqueue to be accepted.
    insar_control_timeout_s: float = 3.0

    # ---- Billing: M-Pesa STK Push (Daraja) ----
    # The listing-location access model (commercial_model.md). Billing is DISABLED
    # until consumer key + secret are set — then checkout/STK go live. In dev point
    # mpesa_base_url at the Daraja SANDBOX and use the sandbox shortcode/passkey.
    # NOTHING here is a real secret in the repo; set in .env (see .env.example).
    # "sandbox" or "production" — convenience label; when set it also picks the
    # right Daraja host so you don't have to set mpesa_base_url by hand.
    mpesa_env: str = "sandbox"
    mpesa_base_url: str = "https://sandbox.safaricom.co.ke"
    mpesa_consumer_key: str = ""
    mpesa_consumer_secret: str = ""
    mpesa_shortcode: str = ""            # Business ShortCode (sandbox test: 174379)
    mpesa_passkey: str = ""              # Lipa Na M-Pesa Online passkey
    # Public URL Safaricom POSTs the STK result to. In dev this is an ngrok/tunnel
    # URL ending in /api/v1/billing/mpesa/callback.
    mpesa_callback_url: str = ""
    mpesa_timeout_s: float = 30.0        # STK round-trip can be slow

    # Checkout rate-limit (billing_architecture.md §10) — a user must not be able
    # to machine-gun STK prompts (cost + abuse). Fixed window, O(1) in Redis, keyed
    # on user id. Fail-OPEN: a Redis outage degrades to no-throttle, never blocks a
    # paying user (auth is the real control). Generous defaults: a real buyer taps a
    # few times; only automation hits the cap.
    checkout_rate_max: int = 5           # max checkout attempts per window per user
    checkout_rate_window_s: int = 300    # window length in seconds (5 min)

    # M-Pesa callback IP allow-list (defense-in-depth, billing_architecture.md §10).
    # Comma-separated Safaricom callback IPs. DEFAULT EMPTY = no IP check (so the
    # Daraja sandbox and local dev keep working unchanged). When set in prod, a
    # callback from a non-listed IP is ignored but STILL answered 200 (a non-200
    # makes Safaricom retry-storm). The body is already trusted only as a lookup key.
    mpesa_callback_allowed_ips: str = ""

    # ---- §8: metering & company-detection policy engine ----
    # The commercial-likelihood SCORE crossing this triggers the SOFT gate (an upsell
    # to a business plan), never a block. Tunable lever; see commercial_model.md §7.2.
    company_score_threshold: float = 0.6
    # Rolling window (days) the score is computed over.
    company_score_window_days: int = 30
    # Commercial-action counts that map to a full "volume" / "breadth" signal (1.0).
    # A normal house-hunter is far below these; a portfolio sweep saturates them.
    company_volume_saturation: int = 50      # commercial actions in window → volume=1.0
    company_breadth_saturation: int = 4      # distinct AOIs swept → breadth=1.0
    company_export_saturation: int = 5       # CSV/report exports → export signal=1.0
    # Email domains that strongly indicate a corporate account (one signal among
    # several — never decisive alone). Comma-separated in .env; lower-cased here.
    company_email_domains: str = (
        "kcbgroup.com,equitybank.co.ke,equitygroupholdings.com,britam.com,"
        "jubileeinsurance.com,co-opbank.co.ke,absa.co.ke,ncbagroup.com"
    )

    # ---- InSAR integration bridge (telemetry identity + deep-link) ----
    # Where the public InSAR risk-map SPA is served. Used to build the deep-link
    # URL ("Risk Map" / "View on risk map") the Weespas frontend opens. Local dev
    # topology: the InSAR Vite dev server runs on 5173 and the Weespas FE on 5174
    # (both pinned in their vite.config.ts) — so this MUST point at 5173, not the
    # Weespas FE. Set INSAR_PUBLIC_URL in the environment if your port differs.
    insar_public_url: str = "http://localhost:5173"
    # Where InSAR sends an UNAUTHENTICATED visitor. InSAR is now free-but-login-required
    # (no anonymous access): on load it verifies its telemetry token against
    # GET /insar/verify, and on a miss redirects here so the user signs in on Weespas
    # and re-enters the map via the "Risk Map" link. This is the WEESPAS FE origin
    # (:5174 in local dev), NOT the InSAR FE — the sign-in page lives on Weespas. The
    # InSAR SPA reads this from its own VITE_WEESPAS_LOGIN_URL too; this server value
    # keeps the deep-link/redirect contract in one authoritative place. Carries
    # ?next=insar so login can bounce back.
    weespas_login_url: str = "http://localhost:5174/login"
    # TTL of the telemetry-scoped token handed to the InSAR frontend. Long enough
    # for a map session, short enough to bound a leaked-in-URL token (see
    # auth_service.create_insar_telemetry_token).
    insar_telemetry_token_ttl_min: int = 120
    # TTL of the commerce-scoped token handed to the frontend for the :8003 trading
    # service (see auth_service.create_commerce_token). Same posture as the telemetry TTL.
    commerce_token_ttl_min: int = 120
    # Public base URL of the commerce service — returned alongside the session token so the
    # frontend knows where to talk. Override in prod.
    commerce_public_url: str = "http://localhost:8003"
    # Shared secret used by the commerce → weespas /commerce/users/lookup S2S bridge (§8
    # Chunk C+). Commerce sends the secret in an X-Service-Secret header; weespas checks it
    # constant-time. NOT used for any user-facing endpoint — only the one S2S surface.
    #
    # Blank ⇒ the bridge is DISABLED (weespas returns 503 on the endpoint). This is fail-closed:
    # a dev env without the secret configured must not silently accept a wrong-secret call. Set
    # a matching value on both services (WEESPAS_COMMERCE_USERS_LOOKUP_SECRET and
    # COMMERCE_WEESPAS_USERS_LOOKUP_SECRET) to enable.
    commerce_users_lookup_secret: str = ""
    # S2S read timeout (seconds) for the shops-on-map aggregator's call to commerce
    # (services.commerce_read_client). Kept short: the map must never hang or go dark on a
    # slow commerce — the aggregator degrades to no-shop-pins past this bound. Same posture
    # as insar_control_timeout_s.
    commerce_read_timeout_s: float = 3.0
    # Max building links pulled for one AOI's shops-on-map lookup (anti-O(n), S8). An AOI has
    # far fewer linked buildings than this; the cap is a hard backstop so a pathological AOI
    # can never build an unbounded S2S batch. Mirrors the commerce-side batch cap (200).
    commerce_shops_aoi_link_cap: int = 200
    # Fixed-window rate limit for the shops-on-map aggregator (GET /insar/shops/near), keyed by
    # the telemetry token's verified sub. Each call is a bounded 1:1 S2S amplifier into commerce;
    # this stops a single signed-in account looping it to pressure commerce. O(1) Redis, fail-open
    # on a Redis error (auth is the real control — a Redis blip must not blind the risk map).
    # max=0 disables the check entirely. Same discipline as checkout_rate_* (billing).
    shops_on_map_rate_max: int = 60          # max aggregator calls per window per sub
    shops_on_map_rate_window_s: int = 60     # window length in seconds (1 min)

    # ---- §8.1b pair-radiate: SSE contact bus (generic realtime rail) ----
    # The realtime downlink (GET /insar/contact/stream) and its uplink (POST /insar/contact) form
    # the §5 SSE + Redis Pub/Sub rail. Built generic on weespas (services/event_bus.py) so mobility
    # reuses it verbatim later. Pub/Sub is instance-global (works across the shared Redis), so the
    # async client reuses redis_url (db0) as the plain cache client does.
    #
    # SSE stream heartbeat cadence (seconds): a ``:keep-alive`` comment frame keeps intermediaries
    # (and the client's fetch reader) from timing out an idle connection. Must be well under any
    # proxy read timeout. 0 disables the heartbeat (tests).
    contact_sse_heartbeat_s: float = 20.0
    # Hard ceiling (seconds) on a single SSE connection before the server closes it and the client
    # reconnects. Bounds server-side fan-out state so a forgotten open tab can't hold a subscriber
    # slot forever (anti-resource-leak). Client transparently re-opens. 0 disables the cap.
    contact_sse_max_connection_s: float = 3600.0
    # Glow TTL (seconds) advertised to the frontend in the contact payload — the breath-then-fade
    # window after which a pin returns to the data ramp. No "contact ended" bookkeeping: a closed
    # tab simply never refreshes and the glow decays. Frontend clamps this defensively.
    contact_glow_ttl_s: int = 10
    # Per-sub fixed-window rate limit on POST /insar/contact. Each call resolves footprints + does
    # one Pub/Sub publish + one S2S shop_id→seller lookup, so it is a bounded amplifier — throttle
    # it per verified sub exactly like the shops-on-map aggregator (O(1) Redis, fail-open; auth is
    # the real control). max=0 disables. A pin-open is a human action, so the budget is generous.
    contact_rate_max: int = 120              # max contact POSTs per window per sub
    contact_rate_window_s: int = 60          # window length in seconds (1 min)
    # Hard cap on how many of the viewer's OWN footprints one contact resolves + returns (anti-O(n),
    # S8). A person owns a handful of buildings in any single AOI, so this is a backstop against a
    # pathological account, never a real truncation. Ordered by building_id so a bite is deterministic.
    contact_footprint_cap: int = 50

    # ---- InSAR telemetry token: RS256 signing (asymmetric trust) ----
    # The bridge/telemetry token is moving from HS256 (shared secret_key) to RS256 so the
    # InSAR read app can VERIFY tokens with a public key WITHOUT ever holding a secret that
    # could MINT one. Weespas keeps the private key (sign + verify); InSAR gets public only.
    # A breached InSAR can then verify access tokens but never forge a money (/reveal) or
    # PII (/policy/me) token. Access tokens stay HS256 (secret_key/algorithm above) — only
    # this one token type goes RS256.
    #
    # Empty private-key path ⇒ fall back to HS256 minting (lets the dual-verify rollout land
    # before keys are provisioned). Public-key path lets Weespas verify its OWN RS256 tokens.
    insar_jwt_private_key_path: str = ""
    insar_jwt_public_key_path: str = ""
    insar_jwt_algorithm: str = "RS256"
    # Post-cutover cleanup switch (rollout step 5). While the dual-verify window is open,
    # legacy HS256 telemetry tokens are still accepted so in-flight tokens aren't rejected.
    # Once a full token TTL has elapsed past the RS256 minter flip in prod, set this True to
    # REJECT any telemetry-scoped token that arrived via HS256 — pinning the telemetry path
    # to RS256 without touching access tokens (which legitimately stay HS256 and share the
    # decoder). Default False so nothing changes until you flip it (one env var, no deploy).
    insar_telemetry_require_rs256: bool = False

    @property
    def insar_jwt_private_key(self) -> str:
        """RSA private PEM for signing the InSAR telemetry token, or '' if not configured
        (⇒ HS256 fallback). Read once and memoized — the hot mint path must not touch disk."""
        return _read_pem_cached(self.insar_jwt_private_key_path)

    @property
    def insar_jwt_public_key(self) -> str:
        """RSA public PEM for verifying Weespas's own RS256 telemetry tokens, or '' if not
        configured. Memoized like the private key."""
        return _read_pem_cached(self.insar_jwt_public_key_path)

    @property
    def insar_jwt_rs256_enabled(self) -> bool:
        """True once a private key is provisioned — the minter then signs RS256 instead of
        HS256. Lets the dual-verify changes deploy inert until keys land (rollout step 1→3)."""
        return bool(self.insar_jwt_private_key)

    @property
    def company_domain_set(self) -> set[str]:
        return {d.strip().lower() for d in (self.company_email_domains or "").split(",") if d.strip()}

    @property
    def cors_origins_list(self) -> list[str]:
        """Parsed CORS allow-list (CSV → list) for the CORSMiddleware in main.py."""
        return [o.strip() for o in (self.cors_origins or "").split(",") if o.strip()]

    @property
    def mpesa_callback_allowed_ip_set(self) -> set[str]:
        """Parsed Safaricom callback IP allow-list (CSV → set). Empty ⇒ no IP check."""
        return {ip.strip() for ip in (self.mpesa_callback_allowed_ips or "").split(",") if ip.strip()}

    @property
    def is_billing_enabled(self) -> bool:
        return bool(self.mpesa_consumer_key and self.mpesa_consumer_secret
                    and self.mpesa_shortcode and self.mpesa_passkey)

    @property
    def mpesa_host(self) -> str:
        """The Daraja base URL to actually use. mpesa_env wins for production so a
        stale sandbox mpesa_base_url can't accidentally point live traffic at the
        sandbox; otherwise the explicit mpesa_base_url is honoured."""
        if (self.mpesa_env or "").lower().startswith("prod"):
            return "https://api.safaricom.co.ke"
        return self.mpesa_base_url or "https://sandbox.safaricom.co.ke"

    @property
    def is_sms_enabled(self) -> bool:
        return bool(self.at_api_key)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()
