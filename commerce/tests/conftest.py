"""Shared pytest setup for the commerce service.

Runs BEFORE any test imports: puts the repo root on sys.path (so ``import PE.commerce...``
resolves), points DATABASE_URL at SQLite, and configures a dev JWT keypair so the
RS256 verifier is live in tests.

Two SQLite shims make the PostGIS-flavoured models run on SQLite (the fast, dependency-free
default test path):
  1. @compiles(Geography, "sqlite") → "TEXT" so create_all can build the table (the geog
     column is never READ on SQLite — the proximity service uses lat/lng there).
  2. a connect listener registering acos/cos/sin/radians, which stock SQLite lacks but the
     Haversine distance expression needs.
"""
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]  # /home/jeff
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_KEYS = _REPO_ROOT / "PE" / "dev" / "keys"

os.environ.setdefault("DATABASE_URL", "sqlite://")  # in-memory; overridden per-fixture
os.environ.setdefault("COMMERCE_ENV", "development")
os.environ.setdefault("COMMERCE_JWT_PUBLIC_KEY_PATH", str(_KEYS / "insar_jwt_public.pem"))

import pytest
from geoalchemy2 import Geography
from sqlalchemy import create_engine, event, types as sqltypes
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


# --- Shim 1: Geography compiles to TEXT on SQLite (column is inert there) ---------------
@compiles(Geography, "sqlite")
def _compile_geography_sqlite(_element, _compiler, **_kw):  # pragma: no cover - DDL hook
    return "TEXT"


# --- Shim 3: make SQLite's DateTime behave like Postgres timestamptz --------------------
# PROD is Postgres, whose ``timestamptz`` stores UTC and reads back tz-AWARE. SQLite has no
# native datetime type, so SQLAlchemy's ``DateTime(timezone=True)`` there silently (a) stores
# wall-clock with the tz DROPPED and (b) reads back offset-NAIVE. That mismatch is the root of a
# rare, deterministic test flake: the SQL window filters in boost.eligible_grants /
# flash_sales._active_flash push a tz-aware ``now`` bind and compare it against these naive
# read-backs, occasionally emptying a sponsored/flash lane that should be populated
# (`TypeError: can't compare offset-naive and offset-aware`, or a lexicographic string edge).
#
# This TypeDecorator restores timestamptz semantics on the SQLite TEST path ONLY: normalize any
# bound value to UTC (naive-UTC string on disk, matching SQLite's storage format), and re-attach
# UTC on read so every datetime the ORM hands back is tz-aware — exactly as Postgres does in prod.
# It touches NOTHING on the real Postgres path (colspecs is the SQLite dialect's map alone).
class _UTCDateTime(sqltypes.TypeDecorator):
    """SQLite-only prod-parity adapter for DateTime columns (see block comment above)."""

    impl = sqltypes.DateTime
    cache_ok = True
    _FORMATS = ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S")

    def process_bind_param(self, value, _dialect):
        if value is None:
            return None
        if value.tzinfo is None:  # assume already-UTC naive input (the codebase's convention)
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(tzinfo=None)  # naive-UTC → SQLite text

    def process_result_value(self, value, _dialect):
        if value is None:
            return None
        if isinstance(value, str):  # SQLite hands back the raw stored string
            for fmt in self._FORMATS:
                try:
                    value = datetime.strptime(value, fmt)
                    break
                except ValueError:
                    continue
            else:  # pragma: no cover - guards an unexpected stored format
                raise ValueError(f"unparseable SQLite datetime: {value!r}")
        return value.replace(tzinfo=timezone.utc)


# Register on the SQLite dialect's colspecs so EVERY DateTime column routes through the adapter
# in tests. Mutating the shared map once at import time is intentional (the whole suite runs on
# SQLite); it never affects a Postgres engine.
sqlite_dialect.dialect.colspecs = {
    **sqlite_dialect.dialect.colspecs,
    sqltypes.DateTime: _UTCDateTime,
}


# --- Shim 2: register the math funcs the Haversine SQL needs (SQLite lacks them) --------
def _register_sqlite_math(dbapi_conn, _rec):  # pragma: no cover - connection hook
    dbapi_conn.create_function("acos", 1, math.acos)
    dbapi_conn.create_function("cos", 1, math.cos)
    dbapi_conn.create_function("sin", 1, math.sin)
    dbapi_conn.create_function("radians", 1, math.radians)
    # GeoAlchemy2 wraps geography writes with ST_GeogFromText (the type's bind expression).
    # On SQLite that function doesn't exist; register identity functions (1- and 2-arg
    # forms) that just pass the WKT string through to the TEXT geog column — inert, since
    # the proximity service reads lat/lng on SQLite, never geog.
    dbapi_conn.create_function("ST_GeogFromText", 1, lambda wkt: wkt)
    dbapi_conn.create_function("ST_GeogFromText", 2, lambda wkt, srid: wkt)
    # GeoAlchemy2 also wraps geography READS with AsBinary/ST_AsBinary. Identity pass-through
    # keeps the TEXT value intact (still never interpreted on SQLite).
    dbapi_conn.create_function("AsBinary", 1, lambda v: v)
    dbapi_conn.create_function("ST_AsBinary", 1, lambda v: v)


@pytest.fixture
def db_session():
    """A fresh in-memory SQLite DB with all commerce tables, math funcs registered."""
    from PE.commerce.core.database import Base
    import PE.commerce.models  # noqa: F401 — register tables on Base.metadata

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    event.listen(engine, "connect", _register_sqlite_math)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture
def client(db_session):
    """A TestClient with get_db overridden to the test session and auth bypassed by a
    fixed principal. Use the `auth_*` helpers in test_auth.py for real token verification."""
    from fastapi.testclient import TestClient

    from PE.commerce.main import app
    from PE.commerce.core.auth import CommercePrincipal, get_current_principal
    from PE.commerce.core.database import get_db

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_principal] = lambda: CommercePrincipal(
        sub="test-user", role="user", scopes=("commerce_trade", "read:feed")
    )
    yield TestClient(app)
    app.dependency_overrides.clear()
