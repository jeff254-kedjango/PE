# Weespas Backend — Current State (authoritative)

_Last reconciled: 2026-06-22._

This is the single source of truth for what the backend **actually is today**.
The older docs (`README_V2.md`, `BACKEND_ARCHITECTURE.md`, `MIGRATION_GUIDE.md`,
`IMPLEMENTATION_SUMMARY.md`, `CHANGELOG_V2.md`, the various `*_Audit.md`) are
**historical** — they describe earlier phases and are kept for context, not as a
description of the running system.

## What it is
A FastAPI + SQLAlchemy property-marketplace API for the Kenyan market
(~13.8k LOC). Far beyond the "6 models / 9 endpoints" the original README
describes.

## Architecture
- **Layers:** `models/` · `schemas/` · `services/` · `routers/` · `middleware/`
  · `core/`.
- **15 routers** (`main.py`), all under `/api/v1`: properties, auth, contact,
  agents, admin, staff, media, analytics, favorites, dismissals, sessions, me,
  saved_searches, role_applications.
- **Database: PostgreSQL is the live DB.** `core/config.py` → `core/database.py`
  builds a SQLAlchemy engine from `DATABASE_URL` (e.g.
  `postgresql://…@localhost:5432/commercial`), pool_size=10, max_overflow=20.
  Schema is created via `Base.metadata.create_all` (`create_tables()`), **not**
  Alembic. The legacy `weespas.db` SQLite file has been removed (was unused).
- **Auth:** JWT (HS256, `python-jose`) + bcrypt (`passlib`); OTP login via
  Africa's Talking SMS. Helpers in `services/auth_service.py`.
- **RBAC:** roles user/agent/staff/admin; `require_agent/staff/admin`
  dependencies + `verify_property_ownership` (admins bypass). Multi-role via the
  `user_roles` table with fallback to `users.role`.
- **Async stack:** Celery + Redis (broker/result on DB index 1), Flower; each
  offload is behind a per-feature `CELERY_*` flag (default off) with a
  synchronous fallback. Analytics caching uses stale-while-revalidate.
- **Analytics:** per-visitor session middleware, GeoIP (MaxMind), engagement /
  heatmap / funnel aggregations.

## Configuration (env-driven)
Settings load via `pydantic-settings` from `.env` (git-ignored). See
`.env.example` for the full contract.

- **Required (app won't start without them):** `DATABASE_URL`, `SECRET_KEY`.
  These were previously hard-coded in `core/config.py`; they are now mandatory
  env vars so no usable secret lives in source.
- Other vars: `ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `DEBUG`,
  `COOKIE_SECURE`, `AT_USERNAME`/`AT_API_KEY`/`AT_SENDER_ID`, `GEOIP_DB_PATH`,
  `REDIS_URL`, `CELERY_*`.

## Run
```bash
cd weespas
python -m venv .venv && source .venv/bin/activate   # (.venv already present)
pip install -r requirements.txt
cp .env.example .env        # then fill in DATABASE_URL + SECRET_KEY
uvicorn main:app --reload --port 8000
# docs at /docs, health at /health
```

Seed data: `seed.py` → `seed_expanded.py` → `seed_stats.py` (see `setup_db.sh`);
analytics demo data: `seed_analytics_edges.py`, `seed_heatmaps.py`.

## Test
```bash
cd weespas && pytest        # starter suite under tests/ (DB-independent)
```
See `tests/README.md`. Covers app boot, JWT/bcrypt, and RBAC logic.

## Known follow-ups (not done — tracked for later)
1. **Migrations.** ~14 ad-hoc `add_*.py` / `backfill_*.py` / `migrate_*.py`
   scripts at the repo root act as an informal migration history. Replace with
   **Alembic** (init, baseline current schema, retire the scripts). Deferred
   because they run against the live `commercial` DB.
2. **Secret rotation.** The values now in `.env` are the previously-leaked ones.
   Rotate the Postgres password and regenerate `SECRET_KEY` before any shared
   deploy (rotating `SECRET_KEY` logs all users out).
3. **Dev seed credentials.** `add_roles.py`, `add_staff_roles.py`,
   `seed_stats.py` contain hard-coded passwords (`admin123`, `agent123`,
   `WeespasAdmin2024!`) for local seeding — not for production.
4. **Admin seeding.** `main.py:_seed_admin()` always promotes
   `kwemangenyagrowa@gmail.com` to admin on startup — fine for the demo, remove
   for production.
5. **Production CORS.** `main.py` allows a localhost origin list with
   `allow_methods=["*"]`/`allow_headers=["*"]` — restrict origins/methods in prod.
6. **Test depth.** Current suite is starter-level; add HTTP-layer integration
   tests against a transactional session fixture.
