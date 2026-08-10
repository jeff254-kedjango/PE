"""Metering service — event recording + the action vocabulary guard.

billing_architecture.md §8.1. The metering write is best-effort: a known action is
persisted, an unknown action is dropped (never raised) so a bad label can't break the
action it was metering. Uses a throwaway SQLite session (no Postgres / Redis / broker).
"""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from PE.weespas.core.database import Base
from PE.weespas.models.user import User, UserRole
from PE.weespas.models.metering import (
    MeteringEvent, EVENT_REVEAL, EVENT_MAP_OPEN, EVENT_INSAR_EXPORT,
)
from PE.weespas.services import metering_service as ms


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def user(db):
    u = User(name="t", email=f"{uuid.uuid4()}@e.com", phone=f"07{uuid.uuid4().int % 10**8:08d}",
             hashed_password="x", role=UserRole.USER)
    db.add(u); db.commit(); db.refresh(u)
    return u


def test_record_event_persists_known_action(db, user):
    row = ms.record_event(db, action=EVENT_REVEAL, user_id=user.id, target_ref="L1")
    assert row is not None
    got = db.query(MeteringEvent).filter_by(user_id=user.id).one()
    assert got.action == EVENT_REVEAL
    assert got.target_ref == "L1"
    assert got.created_at is not None


def test_record_event_drops_unknown_action(db, user):
    row = ms.record_event(db, action="not_a_real_action", user_id=user.id)
    assert row is None
    assert db.query(MeteringEvent).count() == 0


def test_record_event_anonymous_is_session_anchored(db):
    """No user (anonymous browse) is fine — the event anchors on the session only."""
    row = ms.record_event(db, action=EVENT_MAP_OPEN, user_id=None, session_id="sess-1")
    assert row is not None and row.user_id is None and row.session_id == "sess-1"


def test_record_event_keeps_aoi_and_meta(db, user):
    """InSAR-side events carry aoi_code (breadth signal) + meta (e.g. export count)."""
    row = ms.record_event(db, action=EVENT_INSAR_EXPORT, user_id=user.id,
                          aoi_code="huruma", meta="250")
    assert row.aoi_code == "huruma" and row.meta == "250"
