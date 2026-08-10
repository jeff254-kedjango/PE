"""Commerce → weespas user-summary bridge (§8 Chunk C+).

Load-bearing properties:
  1. The S2S secret is REQUIRED — missing / mismatched → 401; unset config → 503 (fail-closed).
  2. A valid call returns display_name + avatar + phone for each existing user; missing users
     are simply absent (the caller de-dups against its input).
  3. Duplicate uuids in the request collapse to ONE row in the response.
  4. The uuid list is capped (413/422 above the cap) — a rogue caller can't turn this into
     a bulk exfiltration primitive.
  5. NOT reachable with a user-scoped bearer — the endpoint takes ONLY the shared secret.
     (No user auth path; a browser can never reach it.)
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from PE.weespas.core.config import settings
from PE.weespas.core.database import Base, get_db
from PE.weespas.main import app
from PE.weespas.models.user import User, UserRole


URL = "/api/v1/commerce/users/lookup"
GOOD_SECRET = "s2s-test-secret"


@pytest.fixture
def env(monkeypatch):
    """Configure a fresh in-memory DB and set the shared secret to a known value. Every test
    starts with the bridge ENABLED unless a specific test overrides `commerce_users_lookup_secret`."""
    monkeypatch.setattr(settings, "commerce_users_lookup_secret", GOOD_SECRET)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    def _override_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = _override_db
    yield db
    app.dependency_overrides.pop(get_db, None)
    db.close()


def _user(db, name="Alice", avatar=None, phone_suffix="00000001"):
    u = User(
        id=str(uuid.uuid4()), name=name, email=f"{name.lower()}-{uuid.uuid4().hex[:6]}@t.co",
        phone=f"+2547{phone_suffix}", hashed_password="x", role=UserRole.USER,
        avatar=avatar,
    )
    db.add(u)
    db.commit()
    return u


def _headers(secret=GOOD_SECRET):
    return {"X-Service-Secret": secret} if secret is not None else {}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class TestAuth:
    def test_missing_secret_401(self, env):
        u = _user(env)
        with TestClient(app) as c:
            r = c.post(URL, json={"uuids": [u.id]}, headers={})
        assert r.status_code == 401

    def test_wrong_secret_401(self, env):
        u = _user(env)
        with TestClient(app) as c:
            r = c.post(URL, json={"uuids": [u.id]}, headers=_headers("wrong-secret"))
        assert r.status_code == 401

    def test_config_unset_503(self, env, monkeypatch):
        """Fail-closed: if the secret is unset, the endpoint disables itself entirely — a dev
        environment must never silently accept the wrong secret because we forgot to configure it."""
        monkeypatch.setattr(settings, "commerce_users_lookup_secret", "")
        u = _user(env)
        with TestClient(app) as c:
            r = c.post(URL, json={"uuids": [u.id]}, headers=_headers("anything"))
        assert r.status_code == 503

    def test_no_user_token_path(self, env):
        """The endpoint does NOT accept a user bearer as an alternative auth path — it's S2S
        only. A user token in Authorization is ignored; without the shared secret we still 401."""
        u = _user(env)
        with TestClient(app) as c:
            r = c.post(
                URL, json={"uuids": [u.id]},
                headers={"Authorization": "Bearer some-user-token"},
            )
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestLookup:
    def test_returns_summary_for_existing_uuid(self, env):
        u = _user(env, name="Alice", avatar="/uploads/a.png", phone_suffix="00000010")
        with TestClient(app) as c:
            r = c.post(URL, json={"uuids": [u.id]}, headers=_headers())
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        assert len(items) == 1
        assert items[0]["uuid"] == u.id
        assert items[0]["display_name"] == "Alice"
        assert items[0]["avatar_url"] == "/uploads/a.png"
        assert items[0]["phone"] == "+254700000010"

    def test_missing_user_is_absent(self, env):
        u = _user(env)
        ghost_id = str(uuid.uuid4())
        with TestClient(app) as c:
            r = c.post(URL, json={"uuids": [u.id, ghost_id]}, headers=_headers())
        assert r.status_code == 200
        items = r.json()["items"]
        # Only the real user comes back; the caller diffs to see that ghost_id had no summary.
        assert len(items) == 1
        assert items[0]["uuid"] == u.id

    def test_avatar_null_survives(self, env):
        # A user with no avatar returns avatar_url: null (not missing key).
        u = _user(env, avatar=None)
        with TestClient(app) as c:
            r = c.post(URL, json={"uuids": [u.id]}, headers=_headers())
        assert r.status_code == 200
        assert r.json()["items"][0]["avatar_url"] is None

    def test_duplicate_uuids_collapse(self, env):
        u = _user(env)
        with TestClient(app) as c:
            r = c.post(URL, json={"uuids": [u.id, u.id, u.id]}, headers=_headers())
        assert r.status_code == 200
        assert len(r.json()["items"]) == 1

    def test_many_users_one_query(self, env):
        # 5 users, one call → 5 rows in stable order (id order, not input order — we don't guarantee
        # the caller's order, only that missing users are absent).
        ids = []
        for i in range(5):
            u = _user(env, name=f"U{i}", phone_suffix=f"1000000{i}")
            ids.append(u.id)
        with TestClient(app) as c:
            r = c.post(URL, json={"uuids": ids}, headers=_headers())
        assert r.status_code == 200
        assert len(r.json()["items"]) == 5


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------

class TestBounds:
    def test_empty_list_422(self, env):
        # Zero uuids is not a legal request — the caller has nothing to look up.
        with TestClient(app) as c:
            r = c.post(URL, json={"uuids": []}, headers=_headers())
        assert r.status_code == 422

    def test_over_cap_422(self, env):
        # 101 uuids exceeds the 100 cap → validation error before the query runs.
        oversized = [str(uuid.uuid4()) for _ in range(101)]
        with TestClient(app) as c:
            r = c.post(URL, json={"uuids": oversized}, headers=_headers())
        assert r.status_code == 422
