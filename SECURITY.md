# Security posture — Weespas + InSAR

This document records the security model for the two-product system: **Weespas** (the
identity + revenue plane) and **InSAR** (the subsidence risk-map plane). It exists because
InSAR makes life-safety claims about *specific buildings*, so the data it serves is both a
commercial asset and a liability surface — it has to be access-controlled, not just the UI.

Scope: this covers the cross-service trust boundary, the data API, and the controls shipped
to date. It is a living document — update it when a control changes.

---

## 1. Trust model & identity

- **One identity provider.** Weespas owns identity: phone-OTP / email-password / social
  login → HS256 JWT access token (`auth_service.create_access_token`) + a `weespas_session`
  cookie. InSAR has **no login of its own**; it rides Weespas identity.
- **The bridge token is scoped and asymmetric.** When a signed-in user opens InSAR, Weespas
  mints a short-lived token with claim `scope:"insar_telemetry"`
  (`create_insar_telemetry_token`, TTL `insar_telemetry_token_ttl_min` = 120 min). It can do
  exactly two things: append metering rows for its own `sub`, and read the InSAR data API.
  It is **RS256-signed**: Weespas holds the **private** key (sign + verify), InSAR holds the
  **public** key only.
  - **Why RS256 not a shared HS256 secret:** if InSAR held the HS256 `secret_key` to verify
    tokens, a compromise of the (more exposed, read-only) InSAR service would also let an
    attacker **mint** tokens against Weespas's money (`/reveal`, M-Pesa) and PII
    (`/policy/me`) endpoints. With RS256, a breached InSAR can *verify* but never *forge*.
  - Access tokens stay HS256; only the telemetry/bridge token is RS256.
- **Scope guard (alg-agnostic).** `get_current_user` / `get_current_user_optional` reject any
  token whose `scope == "insar_telemetry"`, so a telemetry token leaked in an InSAR URL can
  never authenticate a money/PII request. This guard is independent of the signing algorithm.
- **Algorithm-confusion defense.** Weespas's dual-algorithm verifier (`_decode_token`)
  branches on the token's own header `alg` and verifies with exactly one (key, alg) pair —
  HS256 only ever against the HMAC secret, RS256 only against the RSA public key. The public
  key is therefore never usable as an HMAC secret (the classic RS→HS downgrade attack). InSAR
  verifies **RS256 only**, so it is immune by construction. Both are covered by tests
  (`test_insar_bridge.py::test_alg_confusion_rejected_on_weespas_side`,
  `test_auth.py::test_alg_confusion_attack_is_rejected`).

## 2. The data API (InSAR `:8002`)

The `:8002` read app serves the precomputed risk bundle — every building's footprint,
`classification`, `danger_level`, `failure_mode`, velocity and confidence σ. Login-gating only
the map UI left this `curl`-able; that is now closed.

- **Auth required.** `/aois`, `/aoi/{code}/bundle`, `/buildings`, `/buildings/at-date`,
  `/buildings/{id}/timeseries`, `/risk-summary` all require a valid RS256 telemetry token
  (`app/auth.py::require_telemetry_token`). `/health` stays open for liveness probes.
- **Fail-closed.** Missing / malformed / expired / bad-signature / wrong-scope → 401, opaque
  (the caller is not told which check failed). Verification is O(1): a signature check and a
  scope comparison, no DB, no network.
- **Inert when unconfigured — but fail-closed at boot in prod.** With no public key set, the
  data endpoints stay public (dev default, lets the dep deploy before keys exist). To stop a
  forgotten key env from *silently* re-exposing the dataset in prod, the app **refuses to
  start** when `INSAR_ENV=production` and no key is configured (boot guard in `app/main.py`
  lifespan). So prod is either authenticated or down — never silently open.
- **Bulk pulls are visible to §8.** A valid-token holder can still pull within the rate limit
  — but a full bundle pull now emits a server-side `insar_bundle_fetch` to Weespas's metering
  sink (`app/usage_meter.py`, best-effort background task, only on a fresh 200, not a 304
  revalidation, and only for the bundle endpoint — not the animation-driven `/buildings/at-date`).
  This feeds the §8 company-detection scorer (volume + breadth + automation), so a scraper that
  curls bundles directly — emitting zero frontend clicks — still crosses the metered threshold
  and hits the upsell. A pure-bundle scraper scores 0.70 vs a normal individual's ~0.14.
  Inert unless `WEESPAS_TELEMETRY_URL` is wired (read app keeps its no-live-network default).
- **Still future (§7):** per-AOI / per-tier *authorization* (today any signed-in user may read
  any AOI, which matches "free for individuals").

## 3. Rate limiting

- Per-account fixed window on the data endpoints (`app/ratelimit.py`), keyed on the verified
  `sub` (runs after auth). Blunts bulk exfiltration by a *signed-in* account.
- **Fail-OPEN, deliberately.** Auth (the RSA signature) is the real security control and is
  redis-independent; a redis outage must not 503 the whole read API, so a redis error
  degrades to "no throttling" (logged), not denial. Contrast the auth dep, which fails
  CLOSED. Inert when `REDIS_URL` is unset.

## 4. CORS & transport

- **Origin-locked CORS.** InSAR allows only `INSAR_ALLOWED_ORIGINS` (default: local Vite
  servers). The cross-origin data GET carries an `Authorization` header → a non-simple
  request → browser preflights `OPTIONS`, answered only for allow-listed origins. No cookies
  on the data path (Bearer header), so `allow_credentials` stays false.
- **Transport (prod requirement, not yet enforced in dev):** terminate TLS at the edge;
  enable HSTS. Tokens travel in the `Authorization` header (never the query string after the
  initial deep-link, which `telemetry.ts` strips from the URL on load) so they don't land in
  access logs, referrers, or browser history.

## 5. Token hygiene

- Telemetry token is short-lived (120 min) and held **in memory** on the InSAR SPA (never
  localStorage) — it dies with the tab, bounding a leak.
- The `?wt=` deep-link param is stripped from the URL via `history.replaceState` on first
  load (`telemetry.ts::initTelemetryFromUrl`).
- Rotating the RSA keypair invalidates outstanding telemetry tokens (≤120 min blast radius);
  rotating Weespas's HS256 `secret_key` logs everyone out (access tokens).

## 6. Secrets & key management

- **Private key lives only on Weespas.** Public key is distributed to InSAR. Neither PEM is
  committed (`dev/keys/.gitignore` excludes `*.pem`; dev keys are generated locally with
  `openssl`). Prod keys are provisioned out-of-band (secret manager / mounted file) and
  referenced by `*_PATH` env vars.
- Config: Weespas `insar_jwt_private_key_path` / `insar_jwt_public_key_path` /
  `insar_jwt_algorithm`; InSAR `INSAR_JWT_PUBLIC_KEY[_PATH]` / `INSAR_JWT_ALGORITHM` /
  `INSAR_JWT_SCOPE`. PEMs are read once and cached (no per-request disk I/O).
- M-Pesa / Africa's Talking / DB credentials are mandatory env vars (no in-repo defaults);
  the app fails fast at startup if `DATABASE_URL` / `SECRET_KEY` are missing.

## 7. Edge isolation & future hardening (tracked, not yet done)

- **Edge isolation (prod):** `:8002` should not be publicly routable — bind both app processes
  to `127.0.0.1` and front them with one TLS gateway. Reference config:
  [`deploy/insar-gateway.conf`](deploy/insar-gateway.conf) (nginx); prod env templates:
  [`deploy/insar.env.prod.example`](deploy/insar.env.prod.example) +
  [`deploy/weespas.env.prod.example`](deploy/weespas.env.prod.example). In dev the Vite proxy
  (`/api → :8002`) already keeps it same-origin.
- **Per-AOI / per-tier authorization:** today any signed-in user may read any AOI (free for
  individuals). Enterprise/portfolio tiers will need the token to carry AOI scopes and the
  data dep to enforce them.
- **Audit trail:** the Weespas integration audit table is append-only + hash-chained; extend
  coverage to data-API access if/when contractual audit is required.
- **HS256 cleanup:** once one full token TTL (120 min) has elapsed past the RS256 minter
  cutover in prod, pin telemetry tokens to RS256 by setting `insar_telemetry_require_rs256=True`.
  This does NOT touch `_decode_token` (the shared decoder must keep HS256 — **access tokens are
  HS256 too**); it adds a telemetry-path-only check in `require_insar_telemetry_token` that
  rejects a telemetry-scoped token presented via HS256. One env flip, no code deploy.

## 8. Rollout ordering (no-lockout invariant)

The RS256 migration must not reject an in-flight token. Safe order:
1. Provision keys; deploy dual-alg verifiers (accept HS256 **and** RS256) — inert, no RS256
   tokens exist yet.
2. Deploy the InSAR data-API auth dep gated on the public key being set (don't set it yet).
3. Flip the Weespas minter to RS256 (private key now signs).
4. Enable InSAR enforcement (set its public key) + FE Bearer + CORS lock together. Pre-cutover
   HS256 holders simply re-enter via the deep-link for a fresh RS256 token.
5. After ≥ one token TTL: remove HS256 acceptance (§7 cleanup).

Hazards if mis-ordered: minting RS256 before verifiers are dual → Weespas 500s its own
tokens; enabling InSAR enforcement before the minter flips → 401s every live token.

## 9. Weespas application hardening (added 2026-06-25)

Four additive, env-gated, inert-by-default controls on the Weespas plane. With no new env
vars set, behaviour is byte-identical to before (dev/sandbox unchanged); operators flip them
on in prod.

- **Celery task time-limits.** Both Celery apps now set `task_soft_time_limit` /
  `task_time_limit` (`core/celery_app.py` defaults 120 s / 180 s for the short Weespas tasks;
  `scripts/pipeline/celery_app.py` defaults 3000 s / 3600 s for the heavy InSAR pipeline,
  env-overridable). A wedged stage can no longer pin a worker forever; the soft limit raises
  `SoftTimeLimitExceeded` (catchable cleanup) before the hard SIGKILL. Combined with the
  existing `acks_late` + idempotent scripts, a killed task re-queues and re-runs safely.
- **Checkout rate-limit.** `POST /billing/checkout` is now throttled per user (Redis fixed
  window, `services/entitlement_service.check_rate_limit`, defaults 5 / 5 min via
  `checkout_rate_max` / `checkout_rate_window_s`). O(1). **Fail-OPEN** on Redis error — auth is
  the real control and a Redis outage must never block a paying user (same posture as §3). Stops
  a user machine-gunning STK PIN prompts (cost + abuse).
- **Env-driven CORS.** Weespas's previously-hardcoded localhost origin list moved to
  `settings.cors_origins` (`CORS_ORIGINS` CSV; default = the local Vite ports, so dev is
  unchanged). In prod set `CORS_ORIGINS` to the real frontend origin(s) only. `allow_credentials`
  stays true (session cookie + JWT); origin-locking is the meaningful control.
- **M-Pesa callback IP allow-list (opt-in).** `POST /billing/mpesa/callback` honours
  `MPESA_CALLBACK_ALLOWED_IPS` (CSV; **default empty = no check**, keeping Daraja sandbox/dev
  working). When set, a callback from a non-listed IP is ignored — but **still answered 200**, so
  Safaricom does not retry-storm. The body was already trusted only as a lookup key (verified
  `ResultCode` + `Amount`, deduped on receipt via Redis NX + a UNIQUE ledger column); this is
  defense-in-depth on top of that. Client IP resolved via `middleware/session._client_ip`
  (honours `X-Forwarded-For` behind a proxy).

## 10. Shops-on-the-InSAR-map aggregator (added 2026-07-19)

`GET /insar/shops/near` (`routers/insar.py`) is a new authenticated cross-service read on the
Weespas plane (`:8000`), added for §8.1a (shop pins on the risk map). The stateless InSAR SPA
holds only its telemetry token; Weespas owns the `BuildingLink` spine + `StructuralFlag` and
mints a short-lived, least-privilege (`role:"user"`, `scope:["read:feed"]`) S2S commerce token
to fetch shop display-meta.

- **Same gate as `/insar/verify`.** Requires a `scope:"insar_telemetry"` token
  (`require_insar_telemetry_token`); a plain access token is 401. Reachable only by the InSAR
  SPA's own token, never a money/PII token.
- **Coordinates never cross (S6).** The response carries `insar_building_id` + non-PII display
  meta (`name`, `category`, `confirmed`) only — **no lat/lng, no avatar**. The footprint the
  client already renders *is* the location; commerce projects exactly these fields at
  `/shops/by-property` and the batch is bounded (`SHOPS_BY_PROPERTY_BATCH_MAX` = 200, anti-O(n)).
- **Least-privilege S2S mint.** The aggregator hardcodes `role="user"` and passes only the
  verified `sub` — it does not trust the telemetry token's role claim for the downstream mint.
- **Degrade, never fail-open into error.** A commerce read failure returns empty pins +
  `partial=true`, HTTP 200 — the life-safety map never goes dark on a commerce hiccup.
- **Per-sub rate-limit.** Each call is a bounded 1:1 S2S amplifier into commerce, so it is
  throttled per verified `sub` (Redis fixed window, `entitlement_service.check_rate_limit`,
  defaults 60 / min via `shops_on_map_rate_max` / `shops_on_map_rate_window_s`). O(1), throttles
  *before* any DB/commerce work. **Fail-OPEN** on Redis error — auth is the real control and a
  Redis blip must not blind the risk map (same posture as §3 and the §9 checkout limit).
