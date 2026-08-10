"""Engine / session / Base for the commerce service.

Copied from weespas core.database (sync SQLAlchemy 2.0 + psycopg2 — commerce is
request/response). One addition: under PostgreSQL, ``create_tables`` self-provisions the
PostGIS extension before create_all so a fresh commerce DB stands up the GiST-indexable
geography columns on first boot (mirrors the InSAR read app loading its DuckDB spatial
extension at startup). Under SQLite (tests) the extension step is skipped.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from PE.commerce.core.config import settings

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    """Timezone-aware UTC now, used as the Python-side ``default`` for timestamp columns that
    back a keyset cursor (created_at / issued_at).

    WHY a Python default and not just ``server_default=func.now()``: SQLite stores
    ``CURRENT_TIMESTAMP`` at SECOND precision, while a cursor anchor is a Python datetime bound
    as MICROSECOND text — so a row-value comparison ``(ts, id) < (anchor_ts, anchor_id)`` over a
    same-second batch short-circuits on the (string-unequal) timestamp and never reaches the id
    tiebreak, leaking already-seen rows back into the next page. Supplying a microsecond,
    tz-aware value here makes the stored timestamp round-trip the cursor exactly on SQLite, and
    is identical to ``now()`` semantics on Postgres. The ``server_default`` is retained as the
    DB-side fallback for any non-ORM insert path."""
    return datetime.now(timezone.utc)

# pool_pre_ping guards against stale connections; pool sizing mirrors weespas.
# SQLite (tests) doesn't accept pool_size/max_overflow on its default pool, so only
# pass them for real server DBs.
_is_sqlite = settings.database_url.startswith("sqlite")
if _is_sqlite:
    engine = create_engine(settings.database_url)
else:
    engine = create_engine(
        settings.database_url,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables() -> None:
    # Importing the models package registers every table on Base.metadata.
    import PE.commerce.models  # noqa: F401

    if engine.dialect.name == "postgresql":
        _ensure_postgis()

    Base.metadata.create_all(bind=engine)

    if engine.dialect.name == "postgresql":
        _apply_additive_columns()
        _apply_search_indexes()

    # Seed the Nairobi-metro neighbourhood rectangles (§8 Chunk C+). Idempotent: INSERT OR
    # IGNORE / ON CONFLICT DO NOTHING per slug — a restart on an already-seeded DB is a
    # bounded no-op. Runs for BOTH dialects (SQLite tests + PostgreSQL prod). Deliberately
    # imported inside the function so an app that only wants `create_tables` without the
    # seed doesn't pay the import cost.
    from PE.commerce.services.reverse_geocode import ensure_seeded as _seed_neighbourhoods
    with SessionLocal() as _db:
        _seed_neighbourhoods(_db)


# Additive, idempotent column/index migrations for tables that already exist.
#
# ``create_all`` creates missing TABLES but never alters an existing table to add new COLUMNS
# (a live DB that predates a model change keeps its old shape). Rather than pull in Alembic for
# the greenfield commerce DB, we apply purely-additive DDL idempotently at boot — every
# statement is ``IF NOT EXISTS`` so it is a no-op once applied and safe on every restart. This
# is the same discipline the weespas service uses for its live-schema column adds.
#
# RULES for anything added here (keep it safe):
#   * additive only — ADD COLUMN / CREATE INDEX, never DROP or type-narrow;
#   * every statement guarded with IF NOT EXISTS;
#   * new non-null columns MUST carry a server_default so existing rows backfill.
_ADDITIVE_DDL: tuple[str, ...] = (
    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS stock_qty INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS low_stock_threshold INTEGER NOT NULL DEFAULT 0",
    "CREATE INDEX IF NOT EXISTS ix_listings_feed ON listings (is_active, stock_qty, created_at)",
    # Settlement (increment 4):
    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS pricing_mode VARCHAR(16) NOT NULL DEFAULT 'fixed'",
    # One OPEN negotiation per (buyer, listing): a PARTIAL unique index over open statuses only.
    # The service also guards this (the SQLite test path can't express a partial unique), but in
    # prod this index is the hard backstop against a buyer spamming duplicate open orders.
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_open_per_buyer_listing "
    "ON orders (buyer_uuid, listing_id) "
    "WHERE status IN ('REQUESTED', 'OFFERED', 'COUNTERED')",
    # Widen idempotency_keys.scope 64→255 on a DB provisioned before this fix. The settle/accept/
    # counter scope is "action:{user_uuid}:{order_id}" — ~80 chars with real UUIDs, so 64 truncated
    # and every bargain settle 500'd on Postgres. ALTER ... TYPE is a safe no-op widening when the
    # column is already 255 (idempotent across restarts). SQLite ignores VARCHAR length, so this
    # is Postgres-only (the additive DDL runs only on postgresql anyway).
    "ALTER TABLE idempotency_keys ALTER COLUMN scope TYPE VARCHAR(255)",
    # Ephemeral "selling now" promotion (§8) — nullable, so existing rows are simply un-promoted.
    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS promo_mode VARCHAR(16)",
    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS promo_started_at TIMESTAMPTZ",
    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS promo_expires_at TIMESTAMPTZ",
    # Index the expiry so the feed's story-exclusion predicate is index-friendly.
    "CREATE INDEX IF NOT EXISTS ix_listings_promo_expires ON listings (promo_expires_at)",
    # Flash Sale (§8) — the nationwide 1-hour "crazy offer" window. All nullable ⇒ existing rows are
    # simply not on flash. flash_price_cents is a temporary override (price_cents is never touched);
    # flash_score is the precomputed margin the read sorts on.
    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS flash_price_cents INTEGER",
    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS flash_started_at TIMESTAMPTZ",
    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS flash_expires_at TIMESTAMPTZ",
    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS flash_score DOUBLE PRECISION",
    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS flash_reference_cents INTEGER",
    # Index the expiry so the active-window predicate is index-friendly, and a PARTIAL index on
    # flash_score (descending, only over rows that ever had a flash sale) so the nationwide
    # "ORDER BY flash_score DESC" read range-scans a tiny pre-sorted set — never the whole table.
    "CREATE INDEX IF NOT EXISTS ix_listings_flash_expires ON listings (flash_expires_at)",
    "CREATE INDEX IF NOT EXISTS ix_listings_flash_active ON listings (flash_score DESC) "
    "WHERE flash_expires_at IS NOT NULL",
    # Social feed post type (§8) — the seller's declared "short video" post kind, powering the
    # feed's Listings|Videos toggle. server_default false ⇒ existing rows are ordinary listings.
    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS is_short_video BOOLEAN NOT NULL DEFAULT false",
    "CREATE INDEX IF NOT EXISTS ix_listings_is_short_video ON listings (is_short_video)",
    # Display-name snapshots (commerce owns no identity — these are captured from the token's name
    # claim at write time so the comment thread + seller inbox show a name, not a raw user id).
    # Nullable: pre-existing rows have none and the UI falls back to a neutral label.
    "ALTER TABLE listing_comments ADD COLUMN IF NOT EXISTS author_name VARCHAR(255)",
    "ALTER TABLE listing_inquiries ADD COLUMN IF NOT EXISTS from_user_name VARCHAR(255)",
    # Free-text product description for the §8 social feed card (paragraphs preserved). Nullable —
    # pre-existing listings have none and the card omits it.
    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS description TEXT",
    # Social timeline post kind (§8): 'product' (sellable, hidden when out of stock) vs 'post'
    # (plain social content, no inventory, never stock-hidden). server_default 'product' backfills
    # every existing row as a product; indexed so the feed's (post OR in-stock) gate stays
    # index-backed.
    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS post_kind VARCHAR(16) NOT NULL DEFAULT 'product'",
    "CREATE INDEX IF NOT EXISTS ix_listings_post_kind ON listings (post_kind)",
    # Comment likes (§8) — a like is a (user, comment) row, mirroring saved_listings: idempotent
    # via the unique constraint, counted by a batch GROUP BY. Created as a whole table here for a
    # DB provisioned before this feature (SQLite tests get it via Base.metadata.create_all).
    "CREATE TABLE IF NOT EXISTS comment_likes ("
    "id VARCHAR PRIMARY KEY, "
    "comment_id VARCHAR NOT NULL REFERENCES listing_comments(id), "
    "user_uuid VARCHAR NOT NULL, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT now())",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_comment_like_user ON comment_likes (user_uuid, comment_id)",
    "CREATE INDEX IF NOT EXISTS ix_comment_like_comment ON comment_likes (comment_id)",
    "CREATE INDEX IF NOT EXISTS ix_comment_like_user_created ON comment_likes (user_uuid, created_at)",
    # Seller-published shop business card (§8 profile hovercard) — both nullable, so existing shops
    # (incl. auto-provisioned personal timelines) simply have none and the card omits them.
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS description TEXT",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS contact VARCHAR(255)",
    # Shop profile picture / logo (§8) — a media URL (absolute or /uploads/... relative), same shape
    # as a listing's media_urls. Nullable: a shop without one falls back to the initials avatar.
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(512)",
    # Shop banner / cover image (§8 shop profile) — wide hero backdrop, same media-URL shape as
    # avatar_url. Nullable: a shop without one shows a plain profile header.
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS banner_url VARCHAR(512)",
    # Shop trade category (§8 trending rail color + feed signal) — a slug from
    # core.categories.SHOP_CATEGORIES, validated at the API edge. Nullable: existing shops are
    # un-categorised until the seller picks one. Indexed for the trending slate / category filters.
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS category VARCHAR(40)",
    "CREATE INDEX IF NOT EXISTS ix_shops_category ON shops (category)",
    # Shop handle (§8 shareable storefront URL: /shop/<handle>). Nullable — a shop without a claimed
    # handle simply keeps NULL and its storefront URL degrades to the seller_id form, so shareable
    # links exist from day one. Case-insensitive uniqueness is enforced by a FUNCTIONAL UNIQUE index
    # on lower(handle) with a partial WHERE that lets multiple NULLs coexist (see model:seller.py).
    # Both statements are idempotent (IF NOT EXISTS) so this is safe on every boot.
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS handle VARCHAR(40)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_shops_handle_lower ON shops (lower(handle)) "
    "WHERE handle IS NOT NULL",
    # Shop "follow"/Notify subscriptions (§8) — a (user, shop) row mirroring saved_listings:
    # idempotent via the unique constraint, counted by a batch GROUP BY. Whole-table create for a
    # DB provisioned before this feature (SQLite tests get it via Base.metadata.create_all).
    "CREATE TABLE IF NOT EXISTS shop_subscriptions ("
    "id VARCHAR PRIMARY KEY, "
    "user_uuid VARCHAR NOT NULL, "
    "shop_id VARCHAR NOT NULL REFERENCES shops(id), "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT now())",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_shop_sub_user_shop ON shop_subscriptions (user_uuid, shop_id)",
    "CREATE INDEX IF NOT EXISTS ix_shop_sub_shop ON shop_subscriptions (shop_id)",
    "CREATE INDEX IF NOT EXISTS ix_shop_sub_user_created ON shop_subscriptions (user_uuid, created_at)",
    # Per-shop sponsored-cap override (§8.3, item 1) — a shop APPLIES for an absolute cap that staff
    # APPROVE, overriding the global feed_sponsored_max_per_shop for that shop only. One row per shop
    # (UNIQUE ⇒ apply is an idempotent UPSERT). Only status='approved' with a positive approved_cap
    # affects the feed; pending/rejected are inert. Whole-table create for a DB provisioned before
    # this feature (SQLite tests get it via Base.metadata.create_all). decided_by is a token-sub
    # snapshot, NOT a FK — weespas owns identity (doc §3).
    "CREATE TABLE IF NOT EXISTS shop_sponsored_cap_overrides ("
    "id VARCHAR PRIMARY KEY, "
    "shop_id VARCHAR NOT NULL REFERENCES shops(id), "
    "requested_cap INTEGER NOT NULL, "
    "status VARCHAR(16) NOT NULL DEFAULT 'pending', "
    "approved_cap INTEGER, "
    "decided_by VARCHAR, "
    "decided_at TIMESTAMPTZ, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
    "updated_at TIMESTAMPTZ NOT NULL DEFAULT now())",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_shop_cap_override_shop ON shop_sponsored_cap_overrides (shop_id)",
    "CREATE INDEX IF NOT EXISTS ix_shop_cap_override_status ON shop_sponsored_cap_overrides (status)",
    # ---------------------------------------------------------------------------------------------
    # Deterministic newest-first ordering for the three social-engagement threads (saves, seller
    # inquiries, comments). Each formerly sorted by (created_at DESC, id DESC), but ``id`` is a
    # random uuid4 — so two rows sharing a created_at MICROSECOND (rapid programmatic writes)
    # ordered non-deterministically (this flaked the comment newest-first test under load). The
    # fix is a per-scope monotonic ``seq`` (assigned in the service, mirroring OrderEvent.seq); the
    # read + cursor now order by seq, a total order with no tie.
    #
    # This runs only on Postgres DBs provisioned BEFORE the change (fresh + SQLite DBs get the
    # column and UNIQUE constraint straight from the model via create_all). For each table:
    #   1) ADD COLUMN nullable — a monotonic per-scope value has no valid constant server_default,
    #      so existing rows land NULL and are backfilled next.
    #   2) BACKFILL — assign 0-based row numbers per scope in the OLD (created_at, id) order, so
    #      existing threads keep exactly the order they display today. Guarded by ``seq IS NULL``:
    #      a no-op on every boot after the first (idempotent), and it runs in the same transaction
    #      as the ADD so no NULL is ever observable to the running app.
    #   3) UNIQUE INDEX (scope, seq) — guarantees the total order and backs the keyset range-scan
    #      (the service catches a concurrent-seq IntegrityError and retries with the next seq).
    #   4) SET NOT NULL — now that every existing row is backfilled (step 2 precedes this in the
    #      SAME transaction) and every writer assigns seq, promote the soft index-enforced invariant
    #      into a hard boot-time guarantee. SET NOT NULL is idempotent on Postgres (a no-op if the
    #      constraint already holds, never an error), so this is safe on every boot; it validates the
    #      whole table once, which is cheap here (0 NULLs live) and turns any future NULL-writing
    #      regression into an immediate, loud failure instead of a silent ordering flake.
    # Superseded (created_at) indexes are left in place — this list is additive-only by policy; their
    # removal is a follow-up.
    "ALTER TABLE saved_listings ADD COLUMN IF NOT EXISTS seq INTEGER",
    "UPDATE saved_listings s SET seq = sub.rn - 1 FROM ("
    "SELECT id, row_number() OVER (PARTITION BY user_uuid ORDER BY created_at, id) AS rn "
    "FROM saved_listings) sub WHERE s.id = sub.id AND s.seq IS NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_saved_user_seq ON saved_listings (user_uuid, seq)",
    "ALTER TABLE listing_inquiries ADD COLUMN IF NOT EXISTS seq INTEGER",
    "UPDATE listing_inquiries i SET seq = sub.rn - 1 FROM ("
    "SELECT id, row_number() OVER (PARTITION BY seller_id ORDER BY created_at, id) AS rn "
    "FROM listing_inquiries) sub WHERE i.id = sub.id AND i.seq IS NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_inquiry_seller_seq ON listing_inquiries (seller_id, seq)",
    "ALTER TABLE listing_comments ADD COLUMN IF NOT EXISTS seq INTEGER",
    "UPDATE listing_comments c SET seq = sub.rn - 1 FROM ("
    "SELECT id, row_number() OVER (PARTITION BY listing_id ORDER BY created_at, id) AS rn "
    "FROM listing_comments) sub WHERE c.id = sub.id AND c.seq IS NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_comment_listing_seq ON listing_comments (listing_id, seq)",
    # (4) Tighten to NOT NULL — runs AFTER the backfills above in the same transaction; idempotent.
    "ALTER TABLE saved_listings ALTER COLUMN seq SET NOT NULL",
    "ALTER TABLE listing_inquiries ALTER COLUMN seq SET NOT NULL",
    "ALTER TABLE listing_comments ALTER COLUMN seq SET NOT NULL",
    # Chunk B (§8 seller-console ranking card): paywall entitlement for >200 km ranking radius.
    # A row grants the caller access until `expires_at`. Payment integration is deferred; this is
    # the stub table the endpoint checks so the frontend can be built + tested end-to-end.
    # ``kind`` is 'one_time_2h' or 'annual' (both purchasable later; today only granted by admin
    # scripts / tests). Idempotent CREATE — pre-existing schemas are untouched.
    "CREATE TABLE IF NOT EXISTS ranking_entitlements ("
    "  id VARCHAR NOT NULL PRIMARY KEY,"
    "  user_uuid VARCHAR NOT NULL,"
    "  kind VARCHAR(16) NOT NULL,"
    "  expires_at TIMESTAMPTZ NOT NULL,"
    "  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
    ")",
    # Two-column index (user, expires) → the "does the caller have an ACTIVE entitlement" probe
    # is a range scan tight to the user's rows only. Not unique — a user may re-purchase after
    # expiry, so multiple rows per user are legal; only the newest matters.
    "CREATE INDEX IF NOT EXISTS ix_ranking_entitlements_user_expires "
    "ON ranking_entitlements (user_uuid, expires_at DESC)",
    # Chunk C (§8 seller-console Viewing Card): one row per (shop_id, session_id) visit; a
    # heartbeat UPDATE-in-places instead of inserting a new row. UNIQUE (shop_id, session_id)
    # is the upsert key; the two secondary indexes serve the live-count and history probes.
    "CREATE TABLE IF NOT EXISTS shop_view_events ("
    "  id VARCHAR NOT NULL PRIMARY KEY,"
    "  shop_id VARCHAR NOT NULL REFERENCES shops(id) ON DELETE CASCADE,"
    "  viewer_uuid VARCHAR NULL,"
    "  session_id VARCHAR(64) NOT NULL,"
    "  viewed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
    "  last_heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
    "  source VARCHAR(24) NOT NULL DEFAULT 'storefront_page'"
    ")",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_shop_view_events_shop_session "
    "ON shop_view_events (shop_id, session_id)",
    "CREATE INDEX IF NOT EXISTS ix_shop_view_events_shop_hb "
    "ON shop_view_events (shop_id, last_heartbeat_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_shop_view_events_shop_viewed "
    "ON shop_view_events (shop_id, viewed_at DESC)",
    # Chunk C+ (§8 humanized viewers): the listing the visitor is looking at right now. Nullable;
    # every heartbeat overwrites (latest wins). Not a FK — a viewer looking at a since-deleted
    # listing keeps a valid heartbeat row; the FE drops the "viewing X" fragment when the
    # listing lookup returns null.
    "ALTER TABLE shop_view_events ADD COLUMN IF NOT EXISTS viewing_listing_id VARCHAR",
    # Chunk C+ (§8 humanized viewers): the visitor's coarse coord at heartbeat time, for the
    # seller's reverse-geocode label. Nullable — a visitor without Geolocation permission
    # sends null. Latest wins: overwritten every ping.
    "ALTER TABLE shop_view_events ADD COLUMN IF NOT EXISTS last_lat DOUBLE PRECISION",
    "ALTER TABLE shop_view_events ADD COLUMN IF NOT EXISTS last_lng DOUBLE PRECISION",
    # Chunk C+ (§8 humanized viewers): coarse Nairobi-metro neighbourhood table. Rectangle-
    # per-row (min/max lat + min/max lng) so the reverse-geocode probe is a two-BETWEEN
    # filter, dialect-agnostic. Rows are seeded on boot from a static tuple; adding a new
    # area is a data-only change.
    "CREATE TABLE IF NOT EXISTS neighbourhoods ("
    "  slug VARCHAR(40) NOT NULL PRIMARY KEY,"
    "  name VARCHAR(80) NOT NULL,"
    "  min_lat DOUBLE PRECISION NOT NULL,"
    "  max_lat DOUBLE PRECISION NOT NULL,"
    "  min_lng DOUBLE PRECISION NOT NULL,"
    "  max_lng DOUBLE PRECISION NOT NULL,"
    "  priority INTEGER NOT NULL DEFAULT 100"
    ")",
    "CREATE INDEX IF NOT EXISTS ix_neighbourhoods_bbox "
    "ON neighbourhoods (min_lat, max_lat)",
)


# Global trade-search trigram indexes (navbar unified search) — a PERFORMANCE optimisation applied
# SEPARATELY from the hard additive DDL above, and fail-SOFT. services.search matches a
# ``lower(col) LIKE '%term%'`` (substring, case-insensitive) over the listing title, listing
# description and owning shop name; a plain btree can't serve a leading-wildcard LIKE, so a pg_trgm
# GIN index on ``lower(col)`` makes the bounded candidate pull index-ASSISTED instead of a sequential
# scan. All three are gin_trgm_ops on lower(col) to match the EXACT expression the service filters on.
#
# WHY separate + best-effort (not in _ADDITIVE_DDL): ``CREATE EXTENSION pg_trgm`` needs a privilege the
# recommended least-privilege prod role may lack. If it ran inside the single _apply_additive_columns
# transaction, that failure would roll back EVERY additive migration and break boot. Search works
# WITHOUT the index (it degrades to a bounded seq scan — the candidate cap still bounds cost), so a
# missing index must never take the service down. Each statement is attempted in its OWN transaction
# and a failure is logged and swallowed (mirrors the _ensure_postgis fail-soft philosophy, but softer
# since this is pure perf, never correctness). Idempotent (IF NOT EXISTS) ⇒ safe on every boot.
_SEARCH_TRGM_DDL: tuple[str, ...] = (
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE INDEX IF NOT EXISTS ix_listings_title_trgm "
    "ON listings USING gin (lower(title) gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS ix_listings_description_trgm "
    "ON listings USING gin (lower(description) gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS ix_shops_name_trgm "
    "ON shops USING gin (lower(name) gin_trgm_ops)",
)


def _apply_additive_columns() -> None:
    """Run the additive DDL (PostgreSQL only — SQLite tests build the current schema directly
    via create_all, so they never need it). Each statement is idempotent."""
    with engine.begin() as conn:
        for stmt in _ADDITIVE_DDL:
            conn.execute(text(stmt))


def _apply_search_indexes() -> None:
    """Best-effort creation of the pg_trgm search indexes (PostgreSQL only). Each statement runs in
    its OWN transaction so one failure can't roll back the others, and any failure is logged and
    swallowed — search is CORRECT without these indexes (it degrades to a bounded seq scan), so a
    missing extension privilege must never break boot. Idempotent (IF NOT EXISTS)."""
    for stmt in _SEARCH_TRGM_DDL:
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
        except SQLAlchemyError as exc:
            logger.warning(
                "commerce: could not apply search index step (%s): %s — trade search will run "
                "without the trigram index (bounded seq scan; still correct). Have a superuser run "
                "'CREATE EXTENSION IF NOT EXISTS pg_trgm;' once to enable index-assisted search.",
                exc.__class__.__name__, str(stmt).split("\n")[0],
            )


def _ensure_postgis() -> None:
    """Guarantee the PostGIS extension exists before ``create_all`` builds the geography
    columns. Three boot scenarios, distinguished so failures are actionable, not opaque:

      1. Already present (dev, and prod after the first provision) → IF NOT EXISTS is a
         clean no-op for any role, even a non-superuser.
      2. Absent + role may CREATE EXTENSION (superuser/dev) → self-provisions.
      3. Absent + role lacks the privilege (the recommended least-privilege prod role) →
         CREATE EXTENSION raises. We must NOT swallow this: without PostGIS the geography
         columns can't be built and ``create_all`` would fail later with a more confusing
         error. Instead we re-check whether the extension is in fact present (it may have
         been pre-provisioned by a superuser, the documented prod path) and only then
         proceed; otherwise we raise a message that names the exact fix.
    """
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        return
    except SQLAlchemyError as exc:
        # Could be a privilege error (role can't CREATE EXTENSION) or the extension's
        # binaries not being installed on the server at all. Re-check actual state on a
        # fresh connection — the failed transaction above is already rolled back.
        if _postgis_installed():
            logger.warning(
                "commerce: could not run CREATE EXTENSION postgis (%s), but the extension "
                "is already present — continuing. (Expected for the least-privilege prod "
                "role; a superuser provisioned PostGIS out of band.)",
                exc.__class__.__name__,
            )
            return
        raise RuntimeError(
            "PostGIS is required but not provisioned in the commerce database, and the "
            "configured role could not create it. Have a superuser run once:\n"
            "    psql -d commerce -c 'CREATE EXTENSION IF NOT EXISTS postgis;'\n"
            "(or install the server package, e.g. postgresql-14-postgis-3, if missing)."
        ) from exc


def _postgis_installed() -> bool:
    """True iff the PostGIS extension is registered in the current database. Uses a fresh
    connection so it is unaffected by the rolled-back CREATE EXTENSION transaction."""
    with engine.connect() as conn:
        return bool(
            conn.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = 'postgis'")
            ).scalar()
        )
