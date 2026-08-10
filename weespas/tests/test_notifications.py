"""In-app notification inbox — service + router, with the cardinal SECURITY guard.

Load-bearing properties under test:
  1. create + list + unread_count + mark_read/mark_all_read behave (the happy path).
  2. SECURITY: a user can NEVER read or mutate another user's notifications — not via
     the list, not via the unread count, not by guessing a notification id. This is the
     whole point of scoping every query by user_id; the test would fail loudly if a
     future refactor dropped the filter.
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from PE.weespas.main import app
from PE.weespas.core.database import Base, get_db
from PE.weespas.models.user import User, UserRole
from PE.weespas.models.notification import Notification
from PE.weespas.services import notification_service
from PE.weespas.services.auth_service import get_current_user


@pytest.fixture
def env():
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
    yield db, Session
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)
    db.close()


def _user(db, name="A"):
    u = User(
        id=str(uuid.uuid4()), name=name, email=f"{name}@t.co",
        phone=f"+2547{uuid.uuid4().int % 100000000:08d}",
        hashed_password="x", role=UserRole.USER,
    )
    db.add(u)
    db.commit()
    return u


def _as(user):
    app.dependency_overrides[get_current_user] = lambda: user


# ── Service unit tests ────────────────────────────────────────────────────────

def test_create_and_unread_count(env):
    db, _ = env
    u = _user(db)
    notification_service.create(db, user_id=u.id, title="Hi", body="b")
    notification_service.create(db, user_id=u.id, title="Yo", body="b2")
    db.commit()
    assert notification_service.unread_count(db, u.id) == 2


def test_mark_read_decrements_unread(env):
    db, _ = env
    u = _user(db)
    n = notification_service.create(db, user_id=u.id, title="Hi", body="b")
    db.commit()
    assert notification_service.unread_count(db, u.id) == 1
    assert notification_service.mark_read(db, u.id, n.id) is True
    assert notification_service.unread_count(db, u.id) == 0


def test_mark_all_read(env):
    db, _ = env
    u = _user(db)
    for i in range(3):
        notification_service.create(db, user_id=u.id, title=f"n{i}", body="b")
    db.commit()
    assert notification_service.mark_all_read(db, u.id) == 3
    assert notification_service.unread_count(db, u.id) == 0


# ── SECURITY: cross-user isolation ─────────────────────────────────────────────

def test_user_cannot_read_others_notifications(env):
    db, _ = env
    a = _user(db, "alice")
    b = _user(db, "bob")
    notification_service.create(db, user_id=a.id, title="alice-secret", body="b")
    db.commit()

    # Bob's list + count must not see Alice's row.
    assert notification_service.list_for_user(db, b.id) == []
    assert notification_service.unread_count(db, b.id) == 0


def test_user_cannot_mark_others_notification_read(env):
    db, _ = env
    a = _user(db, "alice")
    b = _user(db, "bob")
    n = notification_service.create(db, user_id=a.id, title="alice", body="b")
    db.commit()

    # Bob guesses Alice's notification id → refused, and Alice's stays unread.
    assert notification_service.mark_read(db, b.id, n.id) is False
    assert notification_service.unread_count(db, a.id) == 1


# ── Router tests (auth-scoped) ─────────────────────────────────────────────────

def test_endpoints_scope_to_current_user(env):
    db, _ = env
    a = _user(db, "alice")
    b = _user(db, "bob")
    na = notification_service.create(db, user_id=a.id, title="alice", body="b")
    db.commit()

    client = TestClient(app)

    # As Bob: empty inbox, zero unread, and a 404 trying to read Alice's id.
    _as(b)
    assert client.get("/api/v1/notifications").json() == []
    assert client.get("/api/v1/notifications/unread-count").json()["count"] == 0
    assert client.post(f"/api/v1/notifications/{na.id}/read").status_code == 404

    # As Alice: sees her own and can mark it read.
    _as(a)
    body = client.get("/api/v1/notifications").json()
    assert len(body) == 1 and body[0]["title"] == "alice"
    assert client.post(f"/api/v1/notifications/{na.id}/read").status_code == 204
    assert client.get("/api/v1/notifications/unread-count").json()["count"] == 0
