"""Shared pytest setup for the mobility service.

Runs BEFORE any test imports: puts the repo root on sys.path (so ``import PE.mobility...``
resolves), configures the dev JWT keypair so the RS256 verifier is live, and points REDIS_URL
at a dedicated test DB INDEX (db 15) so tests never touch the running dev stack's data (db 4).

Mobility is Redis-only (no SQLite/PostGIS shims needed — contrast commerce). The tests exercise
the REAL Redis dispatch spine (GEO + Pub/Sub + sets), so a Redis must be reachable; if it is not,
the redis-marked tests are skipped with a clear reason rather than failing spuriously.
"""
import os
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]  # /home/jeff
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_KEYS = _REPO_ROOT / "PE" / "dev" / "keys"

os.environ.setdefault("MOBILITY_ENV", "development")
os.environ.setdefault("MOBILITY_JWT_PUBLIC_KEY_PATH", str(_KEYS / "insar_jwt_public.pem"))
# Isolated test DB index — never the dev stack's db 4.
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

import jwt
import pytest
import pytest_asyncio

from PE.mobility.core.config import settings
from PE.mobility.services import event_bus


def _mint(sub: str, scopes: list[str], *, scope: str = "mobility_dispatch", ttl: int = 600) -> str:
    """Mint a test token signed with the dev PRIVATE key (the service verifies with the public
    half). ``scope`` lets a test forge a WRONG-audience token to prove the audience guard."""
    priv = (_KEYS / "insar_jwt_private.pem").read_text()
    payload = {
        "sub": sub,
        "role": "user",
        "scope": scope,
        "scopes": scopes,
        "exp": int(time.time()) + ttl,
    }
    return jwt.encode(payload, priv, algorithm="RS256")


@pytest.fixture
def mint():
    """Expose the token minter to tests."""
    return _mint


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def auth():
    return _auth


@pytest_asyncio.fixture(autouse=True)
async def _flush_test_redis():
    """FLUSH the isolated test DB before AND after each test so no state leaks between tests or
    into the dev stack. Skips the whole test cleanly if Redis is unreachable."""
    client = event_bus.get_client()
    try:
        await client.flushdb()
    except Exception as exc:  # redis down / refused
        pytest.skip(f"Redis not reachable for dispatch tests: {exc}")
    yield
    try:
        await client.flushdb()
    finally:
        # Drop the module-level singleton so each test file starts with a fresh pool and the
        # event loop it was created on is never reused across loops.
        await event_bus.aclose()
