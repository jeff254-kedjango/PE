"""Runtime configuration for the mobility service.

Mobility is the realtime dispatch layer — the second service of the trading layer (see
PE/weespas_trade_architecture.md §5). It verifies weespas-minted RS256 tokens with the
PUBLIC key only (asymmetric stateless auth, zero network calls back to weespas), exactly
like commerce and the InSAR read app.

Mirrors the commerce Settings idiom (pydantic-settings BaseSettings + a module-level
``settings`` singleton + the lru_cached PEM reader), with two deliberate divergences:

  1. **Fail closed, like commerce.** No public key configured ⇒ every protected endpoint
     returns 503 and the app refuses to boot in production. Mobility carries dispatch +
     (later) money identity — it must never silently run unauthenticated.
  2. **This slice is Redis-only** (doc §4): the §5 dispatch spine holds live driver
     positions in Redis GEO and rides events on Redis Pub/Sub. No ``database_url`` yet —
     the durable graph (asyncpg + GeoAlchemy2 KYC store, ride history) is a later slice, so
     there is deliberately no Postgres dependency to misconfigure here.
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


@lru_cache(maxsize=8)
def _read_pem_cached(path: str) -> str:
    """Read a PEM file once and cache by path. Empty/blank path or a missing file ⇒ ''
    (treated as 'not configured' by callers, never an exception at import/startup).
    Copied verbatim from commerce/weespas core.config — same disk-hot-path memoization."""
    if not path:
        return ""
    p = Path(path)
    if not p.is_file():
        return ""
    return p.read_text()


class Settings(BaseSettings):
    # ---- Deployment environment ----
    # "development" (default) or "production". In production the app refuses to start unless a
    # JWT public key is configured (see main.py boot guard) — a forgotten key can never silently
    # run mobility unauthenticated.
    mobility_env: str = "development"
    debug: bool = True

    # ---- Auth: RS256 token verification (public key only) ----
    # Provide EITHER the PEM inline (mobility_jwt_public_key_inline) or a file path
    # (mobility_jwt_public_key_path). This slice shares the weespas signing keypair
    # (PE/dev/keys/insar_jwt_*.pem) — mobility holds the PUBLIC half only and can verify but
    # never mint. The scope string is the AUDIENCE guard against cross-service token replay: a
    # commerce_trade or insar_telemetry token can never satisfy it, and vice-versa.
    mobility_jwt_public_key_path: str = ""
    mobility_jwt_public_key_inline: str = ""
    mobility_jwt_algorithm: str = "RS256"
    mobility_jwt_scope: str = "mobility_dispatch"

    # ---- CORS ----
    # Comma-separated allowed origins. Default covers the local weespas + commerce Vite servers on
    # BOTH host aliases — `localhost` and `127.0.0.1` are distinct browser origins. In prod set
    # CORS_ORIGINS to the real frontend origin(s) only.
    cors_origins: str = (
        "http://localhost:5173,http://localhost:5174,http://localhost:5175,"
        "http://127.0.0.1:5173,http://127.0.0.1:5174,http://127.0.0.1:5175"
    )

    # ---- Redis (the whole dispatch spine: GEO positions + Pub/Sub bus + denylist) ----
    # Own DB index (db 4): weespas cache=0, Celery=1, commerce=3 are taken. Pub/Sub is
    # instance-global (NOT db-scoped), so the SSE bus interoperates across services on one Redis;
    # GEO keys ARE db-scoped and stay self-contained to mobility.
    redis_url: str = "redis://localhost:6379/4"

    # ---- Dispatch tuning (§5 — all explainable, bounded constants; anti-O(n)) ----
    # A driver whose most recent ping is older than this is considered OFF-SHIFT and never
    # dispatched, even though the GEO entry lingers (Redis GEO has no per-member TTL). Bounds
    # "who is live right now" without a sweep.
    driver_stale_seconds: int = 30
    # Ride matcher: search radius + hard cap on drivers returned. GEOSEARCH is O(log n + k); the
    # COUNT cap bounds k so the match + publish fan-out is bounded regardless of driver density.
    ride_default_radius_m: float = 3000.0
    ride_max_radius_m: float = 10000.0      # hard server cap (anti-O(n))
    ride_max_matches: int = 10              # k: most drivers pinged per ride request

    # ---- SSE downlink (mirrors the commerce/weespas event_bus contract) ----
    # Idle keep-alive cadence and a hard per-connection lifetime (bounds server-side subscriber
    # state; the EventSource client transparently reconnects). Matches the §8.1b bus tuning.
    sse_heartbeat_seconds: float = 15.0
    sse_max_connection_seconds: float = 300.0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # ---- Derived ----
    @property
    def cors_origins_list(self) -> list[str]:
        """CSV → list (used by CORSMiddleware)."""
        return [o.strip() for o in (self.cors_origins or "").split(",") if o.strip()]

    def is_production(self) -> bool:
        return self.mobility_env.strip().lower().startswith("prod")

    @property
    def mobility_jwt_public_key(self) -> str:
        """RSA public PEM used to verify mobility tokens, or '' when not configured. Inline value
        wins over the file path; a single-line env var may carry literal '\\n' escapes (common in
        CI/secret stores). Memoized via _read_pem_cached for the path branch — the verify hot path
        must not touch disk."""
        inline = (self.mobility_jwt_public_key_inline or "").strip()
        if inline:
            return inline.replace("\\n", "\n")
        return _read_pem_cached(self.mobility_jwt_public_key_path)

    @property
    def auth_enabled(self) -> bool:
        """True when a public key is configured. Mobility fails CLOSED at request time when this is
        False (auth.py raises 503) and refuses to BOOT in production (main.py)."""
        return bool(self.mobility_jwt_public_key)


settings = Settings()
