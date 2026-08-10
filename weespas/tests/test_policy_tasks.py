"""The company-detection beat job: scoping + the metered verdict it writes.

billing_architecture.md §8.2. The job recomputes UserUsageProfile only for users with
a commercial event in the window (bounded work), and writes is_metered per the
threshold. The task opens its own SessionLocal, so we point it at an in-memory SQLite
engine (same pattern as test_billing_tasks).
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from PE.weespas.core.config import settings
from PE.weespas.core.database import Base
from PE.weespas.models.user import User, UserRole
from PE.weespas.models.metering import (
    MeteringEvent, UserUsageProfile,
    EVENT_INSAR_BUILDING_VIEW, EVENT_INSAR_EXPORT, EVENT_MAP_OPEN,
)
from PE.weespas.services import policy_tasks as pt


@pytest.fixture()
def sm():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture()
def setup(monkeypatch, sm):
    monkeypatch.setattr(pt, "SessionLocal", sm)
    return sm


def _user(db, email=None):
    u = User(name="t", email=email or f"{uuid.uuid4()}@gmail.com",
             phone=f"07{uuid.uuid4().int % 10**8:08d}", hashed_password="x", role=UserRole.USER)
    db.add(u); db.commit(); db.refresh(u)
    return u


def _emit(db, user, action, *, n=1, aoi=None):
    now = datetime.now(timezone.utc)
    for _ in range(n):
        db.add(MeteringEvent(action=action, user_id=user.id, aoi_code=aoi, created_at=now))
    db.commit()


def test_job_writes_metered_profile_for_heavy_user(setup):
    sm = setup
    db = sm()
    heavy = _user(db)
    heavy_id = heavy.id
    for aoi in ("huruma", "kilimani", "kileleshwa", "south_c", "mombasa"):
        _emit(db, heavy, EVENT_INSAR_BUILDING_VIEW, n=settings.company_volume_saturation, aoi=aoi)
    _emit(db, heavy, EVENT_INSAR_EXPORT, n=settings.company_export_saturation, aoi="huruma")
    db.close()

    res = pt.recompute_usage_profiles()
    assert res["scanned"] == 1 and res["metered"] == 1

    db2 = sm()
    p = db2.get(UserUsageProfile, heavy_id)
    assert p is not None and p.is_metered == 1
    db2.close()


def test_job_skips_users_with_no_commercial_events(setup):
    """A user whose only events are non-commercial (map_open) isn't scanned —
    they can't be a company by definition, so we don't even score them."""
    sm = setup
    db = sm()
    casual = _user(db)
    casual_id = casual.id
    _emit(db, casual, EVENT_MAP_OPEN, n=10)   # non-commercial only
    db.close()

    res = pt.recompute_usage_profiles()
    assert res["scanned"] == 0 and res["metered"] == 0

    db2 = sm()
    assert db2.get(UserUsageProfile, casual_id) is None
    db2.close()


def test_job_writes_unmetered_profile_for_light_user(setup):
    """A light commercial user (a few reveals) is scanned but left unmetered —
    the individual envelope, recorded honestly."""
    sm = setup
    db = sm()
    light = _user(db)
    light_id = light.id
    from PE.weespas.models.metering import EVENT_REVEAL
    _emit(db, light, EVENT_REVEAL, n=2)
    db.close()

    res = pt.recompute_usage_profiles()
    assert res["scanned"] == 1 and res["metered"] == 0

    db2 = sm()
    p = db2.get(UserUsageProfile, light_id)
    assert p is not None and p.is_metered == 0
    db2.close()
