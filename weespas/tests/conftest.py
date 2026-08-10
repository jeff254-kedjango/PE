"""Shared pytest setup for the Weespas backend.

This MUST run before any `core.config` / `core.database` import so that:

  * the now-required `DATABASE_URL` / `SECRET_KEY` settings resolve, and
  * tests never touch the live Postgres `commercial` database — we point the
    engine at a throwaway local SQLite file instead.

pytest imports `conftest.py` before collecting test modules, so setting the
env vars at module top-level here is early enough.
"""

import os
import sys
import tempfile
from pathlib import Path

# The codebase imports everything via the `PE.weespas.*` namespace (main.py,
# routers, services all do). The import root is the parent of `PE/` — three levels
# up from this file (tests/conftest.py → weespas → PE → <repo root>). Put it on
# sys.path so `import PE.weespas...` resolves under pytest regardless of CWD, and
# so every test shares ONE module/Base registry (mixing short + namespaced imports
# would double-register the SQLAlchemy mappers).
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# A disposable SQLite DB just for the test process. Created lazily by
# SQLAlchemy; removed at session end (see _cleanup fixture below).
_TEST_DB = Path(tempfile.gettempdir()) / "weespas_test.sqlite3"

os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEST_DB}")
os.environ.setdefault("SECRET_KEY", "test-only-secret-not-used-in-prod")
os.environ.setdefault("DEBUG", "true")

import pytest


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_db():
    yield
    try:
        _TEST_DB.unlink()
    except FileNotFoundError:
        pass
