"""Flag-review queue — service + router, the staff/admin side of "flag a building".

Load-bearing properties under test:
  1. Recording a flag opens exactly ONE review, atomically (the loop is completed).
  2. open_count tracks unseen reviews (the badge).
  3. mark_seen is FIRST-WINS + immutable: the first staff/admin to ack is recorded, a
     second ack by anyone else never overwrites the acknowledger.
  4. "views" counts DISTINCT people (a repeat view by the same user does not inflate it).
  5. SECURITY: the endpoints are staff/admin only (a normal user gets 403), and a
     non-existent review id returns 404 (never confirms another id exists).
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
from PE.weespas.services import structural_flag_service, flag_review_service
from PE.weespas.services.auth_service import get_current_user
from PE.weespas.models.insar_link import FLAG_UNSAFE


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


def _user(db, name="A", role=UserRole.USER):
    u = User(
        id=str(uuid.uuid4()), name=name, email=f"{name}@t.co",
        phone=f"+2547{uuid.uuid4().int % 100000000:08d}",
        hashed_password="x", role=role,
    )
    db.add(u)
    db.commit()
    return u


def _as(user):
    app.dependency_overrides[get_current_user] = lambda: user


def _flag(db, actor, building_id=100000, note="cracks in column"):
    return structural_flag_service.record_flag(
        db, actor=actor, aoi_code="huruma", insar_building_id=building_id,
        state=FLAG_UNSAFE, source="engineer", note=note,
    )


# ── 1. A flag opens exactly one review, atomically ─────────────────────────────

def test_recording_a_flag_opens_one_review(env):
    db, _ = env
    eng = _user(db, "eng", UserRole.PROFESSIONAL)
    flag = _flag(db, eng)

    recs = flag_review_service.list_reviews(db, status="all")
    assert len(recs) == 1
    rec = recs[0]
    assert rec.flag_id == flag.id
    assert rec.insar_building_id == 100000
    assert rec.note == "cracks in column"
    assert rec.flagged_by_id == eng.id
    assert rec.flagged_by_name == "eng"
    assert rec.seen is False
    assert rec.views == 0


def test_create_for_flag_is_idempotent(env):
    db, _ = env
    eng = _user(db, "eng", UserRole.PROFESSIONAL)
    flag = _flag(db, eng)
    # A re-run (e.g. retry) must not spawn a second alert.
    flag_review_service.create_for_flag(db, flag)
    db.commit()
    assert len(flag_review_service.list_reviews(db, status="all")) == 1


# ── 2. open_count (the badge) ──────────────────────────────────────────────────

def test_open_count_tracks_unseen(env):
    db, _ = env
    eng = _user(db, "eng", UserRole.PROFESSIONAL)
    staff = _user(db, "staff", UserRole.STAFF)
    _flag(db, eng, building_id=1)
    _flag(db, eng, building_id=2)
    assert flag_review_service.open_count(db) == 2

    rec = flag_review_service.list_reviews(db, status="all")[0]
    flag_review_service.mark_seen(db, review_id=rec.id, user_id=staff.id)
    assert flag_review_service.open_count(db) == 1


# ── 3. mark_seen is first-wins + immutable ─────────────────────────────────────

def test_mark_seen_first_wins(env):
    db, _ = env
    eng = _user(db, "eng", UserRole.PROFESSIONAL)
    s1 = _user(db, "staff1", UserRole.STAFF)
    s2 = _user(db, "staff2", UserRole.STAFF)
    _flag(db, eng)
    rec = flag_review_service.list_reviews(db, status="all")[0]

    flag_review_service.mark_seen(db, review_id=rec.id, user_id=s1.id)
    # A second ack by another staff must NOT overwrite the original acknowledger.
    flag_review_service.mark_seen(db, review_id=rec.id, user_id=s2.id)

    after = flag_review_service.get_record(db, rec.id)
    assert after.seen is True
    assert after.seen_by_id == s1.id
    assert after.seen_by_name == "staff1"
    # Both staff viewed it → distinct views == 2.
    assert after.views == 2


# ── 4. views count DISTINCT people ─────────────────────────────────────────────

def test_views_count_distinct_people(env):
    db, _ = env
    eng = _user(db, "eng", UserRole.PROFESSIONAL)
    staff = _user(db, "staff", UserRole.STAFF)
    _flag(db, eng)
    rec = flag_review_service.list_reviews(db, status="all")[0]

    assert flag_review_service.record_view(db, review_id=rec.id, user_id=staff.id) == 1
    # Same person re-viewing does not inflate the count.
    assert flag_review_service.record_view(db, review_id=rec.id, user_id=staff.id) == 1


def test_record_view_missing_review(env):
    db, _ = env
    staff = _user(db, "staff", UserRole.STAFF)
    assert flag_review_service.record_view(db, review_id="nope", user_id=staff.id) == -1


# ── 5. SECURITY: staff-gated routes, 404 on unknown id ─────────────────────────

def test_routes_require_staff(env):
    db, _ = env
    normal = _user(db, "normal", UserRole.USER)
    _as(normal)
    client = TestClient(app)
    assert client.get("/api/v1/flag-reviews").status_code == 403
    assert client.get("/api/v1/flag-reviews/open-count").status_code == 403


def test_staff_can_list_and_mark_seen(env):
    db, _ = env
    eng = _user(db, "eng", UserRole.PROFESSIONAL)
    staff = _user(db, "staff", UserRole.STAFF)
    flag = _flag(db, eng)

    _as(staff)
    client = TestClient(app)

    listed = client.get("/api/v1/flag-reviews").json()
    assert len(listed) == 1
    review_id = listed[0]["id"]
    assert listed[0]["flagged_by_name"] == "eng"

    assert client.get("/api/v1/flag-reviews/open-count").json()["count"] == 1

    seen = client.post(f"/api/v1/flag-reviews/{review_id}/seen").json()
    assert seen["seen"] is True
    assert seen["seen_by_id"] == staff.id
    assert client.get("/api/v1/flag-reviews/open-count").json()["count"] == 0


def test_unknown_id_is_404(env):
    db, _ = env
    staff = _user(db, "staff", UserRole.STAFF)
    _as(staff)
    client = TestClient(app)
    assert client.post("/api/v1/flag-reviews/does-not-exist/seen").status_code == 404
    assert client.post("/api/v1/flag-reviews/does-not-exist/view").status_code == 404
