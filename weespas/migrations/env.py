"""Alembic environment — SCOPED, NON-DESTRUCTIVE setup for the live `commercial` DB.

The existing 17 tables were created by `Base.metadata.create_all()` plus a series
of ad-hoc `add_*`/`backfill_*` scripts that ALTER the live schema directly. Alembic
is introduced here to manage ONLY the new integration tables (building_link,
structural_flag, notification_audit) — it must never emit DROP/ALTER for the legacy
tables, whose live shape may differ from the ORM models (column-add scripts).

Two independent guards enforce that:
  1. `include_object` allow-list: autogenerate only ever considers MANAGED_TABLES.
     This is the primary guard — even if the baseline stamp is skipped, autogenerate
     cannot touch a legacy table.
  2. a baseline revision + `alembic stamp` records the starting point so Alembic
     doesn't try to re-create anything that already exists.

Run from the weespas/ directory; imports are rooted at the `PE.weespas` namespace
(the repo lives under /home/jeff, which is on sys.path).
"""
from logging.config import fileConfig
import os
import sys
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# --- make `PE.weespas...` importable regardless of CWD -----------------------
# This file is <repo>/PE/weespas/migrations/env.py; the import root is the parent
# of `PE/`, i.e. three levels up from this file.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from PE.weespas.core.config import settings          # noqa: E402
from PE.weespas.core.database import Base             # noqa: E402

# Import every model module so its tables register on Base.metadata. The new
# integration models MUST be imported for autogenerate to see them; the legacy
# models are imported too (harmless — the allow-list filters them out).
import PE.weespas.models.user             # noqa: F401,E402
import PE.weespas.models.property         # noqa: F401,E402
import PE.weespas.models.analytics        # noqa: F401,E402
import PE.weespas.models.saved_search     # noqa: F401,E402
import PE.weespas.models.contact          # noqa: F401,E402
import PE.weespas.models.role_application  # noqa: F401,E402
import PE.weespas.models.deletion_request  # noqa: F401,E402
import PE.weespas.models.insar_link       # noqa: F401,E402  (the new P4a tables)
import PE.weespas.models.billing          # noqa: F401,E402  (billing: payment_intent/ledger)
import PE.weespas.models.metering         # noqa: F401,E402  (metering_event/user_usage_profile)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# The ONLY tables Alembic is allowed to manage. Everything else (the 17 legacy
# tables) is invisible to autogenerate and to upgrades/downgrades.
MANAGED_TABLES = {"building_link", "building_link_candidate",
                  "structural_flag", "notification_audit",
                  "payment_intent", "payment_ledger",
                  "metering_event", "user_usage_profile",
                  "notifications",
                  "flag_review", "flag_review_view"}
# NOTE: the legacy `properties` table stays OUT of this set on purpose. The
# verification_status / verified_at columns are added by a HAND-WRITTEN migration
# (op.add_column), which runs regardless of this allow-list; keeping `properties`
# invisible to autogenerate prevents it from ever emitting DROP/ALTER on the other
# 20-odd legacy columns it doesn't know about.


def include_object(obj, name, type_, reflected, compare_to):
    """Allow-list filter. Only objects belonging to a MANAGED_TABLE are considered;
    legacy tables (and their indexes/constraints) are ignored entirely, so
    autogenerate can never emit DROP/ALTER against them."""
    if type_ == "table":
        return name in MANAGED_TABLES
    # For columns/indexes/constraints/etc., keep them only if they hang off a
    # managed table. Objects with no resolvable parent table are excluded.
    parent = getattr(obj, "table", None)
    return parent is not None and parent.name in MANAGED_TABLES


def _db_url() -> str:
    # Prefer the live settings URL; allow an env override for offline/CI generation.
    return os.environ.get("ALEMBIC_DATABASE_URL") or settings.database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_db_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _db_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
