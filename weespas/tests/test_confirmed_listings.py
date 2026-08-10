"""POST /insar/listings/confirmed — batch "is this listing ground-confirmed?" badge feed.

Load-bearing properties under test:
  1. A listing whose building has ANY structural flag (state != NONE) → confirmed True;
     a linked-but-unflagged listing → False; an unlinked listing → False.
  2. Every requested id appears in the response (False when not confirmed) — the caller
     needs no reconciliation.
  3. Auth-gated: an anonymous caller is refused.
  4. The batch is capped (a caller can't request an unbounded IN-list).
  5. ONE query does the whole page (no N+1) — exercised implicitly by the batch shape.
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
from PE.weespas.models.insar_link import BuildingLink, StructuralFlag, FLAG_UNSAFE, FLAG_CLEARED
from PE.weespas.services import structural_flag_service
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
    yield db
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)
    db.close()


def _user(db):
    u = User(
        id=str(uuid.uuid4()), name="agent", email=f"{uuid.uuid4()}@t.co",
        phone=f"+2547{uuid.uuid4().int % 10**8:08d}", hashed_password="x",
        role=UserRole.AGENT,
    )
    db.add(u)
    db.commit()
    return u


def _as(user):
    app.dependency_overrides[get_current_user] = lambda: user


def _link(db, listing_id, aoi="huruma", building=100000):
    db.add(BuildingLink(
        listing_id=listing_id, aoi_code=aoi, insar_building_id=building,
        match_method="pip", match_confidence=1.0,
    ))
    db.commit()


def _flag(db, aoi, building, state=FLAG_UNSAFE):
    db.add(StructuralFlag(
        aoi_code=aoi, insar_building_id=building, state=state, source="engineer",
    ))
    db.commit()


# ── 1 & 2. confirmed / not-confirmed / unlinked, all ids present ───────────────

def test_confirmed_flagged_only(env):
    db = env
    _as(_user(db))
    flagged = str(uuid.uuid4())     # linked + has a flag → confirmed
    unflagged = str(uuid.uuid4())   # linked, no flag → not confirmed
    unlinked = str(uuid.uuid4())    # no building link at all → not confirmed
    _link(db, flagged, "huruma", 1)
    _flag(db, "huruma", 1, FLAG_UNSAFE)
    _link(db, unflagged, "huruma", 2)

    client = TestClient(app)
    resp = client.post(
        "/api/v1/insar/listings/confirmed",
        json={"listing_ids": [flagged, unflagged, unlinked]},
    ).json()["confirmed"]

    assert resp == {flagged: True, unflagged: False, unlinked: False}


def test_cleared_flag_also_counts_as_assessed(env):
    db = env
    _as(_user(db))
    lid = str(uuid.uuid4())
    _link(db, lid, "kilimani", 5)
    _flag(db, "kilimani", 5, FLAG_CLEARED)  # an authority CLEARED it — still "assessed"

    client = TestClient(app)
    resp = client.post(
        "/api/v1/insar/listings/confirmed", json={"listing_ids": [lid]},
    ).json()["confirmed"]
    assert resp == {lid: True}


def test_service_returns_only_confirmed_ids(env):
    db = env
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    _link(db, a, "huruma", 10)
    _flag(db, "huruma", 10)
    _link(db, b, "huruma", 11)
    assert structural_flag_service.confirmed_listing_ids(db, [a, b]) == {a}
    # Empty input short-circuits to empty set (no query).
    assert structural_flag_service.confirmed_listing_ids(db, []) == set()


# ── 3. Auth ────────────────────────────────────────────────────────────────────

def test_requires_auth(env):
    client = TestClient(app)
    # No get_current_user override → the real dependency runs and rejects anon.
    resp = client.post("/api/v1/insar/listings/confirmed", json={"listing_ids": []})
    assert resp.status_code in (401, 403)


# ── 4. Batch cap ────────────────────────────────────────────────────────────────

def test_batch_is_capped(env):
    db = env
    _as(_user(db))
    client = TestClient(app)
    too_many = [str(uuid.uuid4()) for _ in range(201)]
    resp = client.post(
        "/api/v1/insar/listings/confirmed", json={"listing_ids": too_many},
    )
    # Pydantic max_length rejects an over-cap request (422) rather than scanning it.
    assert resp.status_code == 422
