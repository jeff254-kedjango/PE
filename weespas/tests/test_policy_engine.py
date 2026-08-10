"""Company-detection policy engine — scoring + the free/metered gate.

billing_architecture.md §8.2. Invariants pinned here:
  * a vetted role (professional/authority/staff/admin/owner) is ALWAYS free — the
    structural exemption can't be out-scored;
  * an anonymous user is free (lives in the individual envelope);
  * the gate is a pure lookup of the precomputed is_metered flag (O(1), threshold
    lives in the beat job, not the request path);
  * a heavy bulk-InSAR user with no exempt role scores over threshold → metered;
  * a corporate email domain nudges the score but is never decisive alone.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from PE.weespas.core.config import settings
from PE.weespas.core.database import Base
from PE.weespas.models.user import User, UserRole, UserRoleRow
from PE.weespas.models.metering import (
    MeteringEvent, UserUsageProfile,
    EVENT_INSAR_BUILDING_VIEW, EVENT_INSAR_EXPORT, EVENT_REVEAL,
)
from PE.weespas.services import policy_engine as pe


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


def _user(db, *, email=None, role=UserRole.USER, extra_roles=()):
    u = User(name="t", email=email or f"{uuid.uuid4()}@gmail.com",
             phone=f"07{uuid.uuid4().int % 10**8:08d}", hashed_password="x", role=role)
    db.add(u); db.commit(); db.refresh(u)
    for r in extra_roles:
        db.add(UserRoleRow(user_id=u.id, role=r))
    if extra_roles:
        db.commit(); db.refresh(u)
    return u


def _emit(db, user, action, *, n=1, aoi=None, when=None):
    when = when or datetime.now(timezone.utc)
    for _ in range(n):
        db.add(MeteringEvent(action=action, user_id=user.id, aoi_code=aoi, created_at=when))
    db.commit()


# --------------------------------------------------------------------------- #
#  scoring
# --------------------------------------------------------------------------- #
def test_no_activity_scores_zero(db):
    u = _user(db)
    bd = pe.compute_score(db, u)
    assert bd.score == 0.0 and bd.volume == 0 and bd.breadth == 0


def test_heavy_bulk_insar_user_scores_over_threshold(db):
    """Saturate volume + breadth + exports the way a portfolio sweep would."""
    u = _user(db)
    for aoi in ("huruma", "kilimani", "kileleshwa", "south_c", "mombasa"):
        _emit(db, u, EVENT_INSAR_BUILDING_VIEW, n=settings.company_volume_saturation, aoi=aoi)
    _emit(db, u, EVENT_INSAR_EXPORT, n=settings.company_export_saturation, aoi="huruma")
    bd = pe.compute_score(db, u)
    assert bd.breadth >= settings.company_breadth_saturation
    assert bd.score >= settings.company_score_threshold


def test_old_events_outside_window_are_ignored(db):
    u = _user(db)
    stale = datetime.now(timezone.utc) - timedelta(days=settings.company_score_window_days + 5)
    _emit(db, u, EVENT_INSAR_BUILDING_VIEW, n=settings.company_volume_saturation, aoi="huruma", when=stale)
    bd = pe.compute_score(db, u)
    assert bd.volume == 0 and bd.score == 0.0


def test_corporate_domain_nudges_but_not_decisive(db):
    """A corporate email alone (no activity) must NOT cross the threshold — the
    exemption-by-structure principle: behaviour decides, not a label."""
    domain = sorted(settings.company_domain_set)[0]
    u = _user(db, email=f"analyst@{domain}")
    bd = pe.compute_score(db, u)
    assert bd.corporate_domain is True
    assert bd.score < settings.company_score_threshold   # 0.15 weight can't cross 0.6 alone


# --------------------------------------------------------------------------- #
#  gate
# --------------------------------------------------------------------------- #
def test_gate_anonymous_is_free(db):
    assert pe.gate(db, None, action=EVENT_INSAR_BUILDING_VIEW) is pe.Decision.FREE


def test_gate_reads_precomputed_metered_flag(db):
    u = _user(db)
    db.add(UserUsageProfile(user_id=u.id, score=0.9, is_metered=1))
    db.commit()
    assert pe.gate(db, u, action=EVENT_INSAR_BUILDING_VIEW) is pe.Decision.METERED


def test_gate_without_profile_is_free(db):
    u = _user(db)
    assert pe.gate(db, u, action=EVENT_INSAR_BUILDING_VIEW) is pe.Decision.FREE


def test_vetted_role_is_always_free_even_when_metered(db):
    """A professional with a metered profile is STILL free — the structural
    exemption wins (an engineer at scale is expected, not a company to bill)."""
    u = _user(db, extra_roles=(UserRole.PROFESSIONAL.value,))
    db.add(UserUsageProfile(user_id=u.id, score=1.0, is_metered=1))
    db.commit()
    assert pe.gate(db, u, action=EVENT_INSAR_BUILDING_VIEW) is pe.Decision.FREE


# --------------------------------------------------------------------------- #
#  upsert_profile (where the threshold actually bites)
# --------------------------------------------------------------------------- #
def test_upsert_marks_metered_above_threshold(db):
    u = _user(db)
    bd = pe.ScoreBreakdown(score=settings.company_score_threshold + 0.1, volume=99,
                           breadth=5, export_count=9, automation=1.0, corporate_domain=True)
    p = pe.upsert_profile(db, u, bd)
    assert p.is_metered == 1 and p.score > settings.company_score_threshold


def test_upsert_never_meters_a_vetted_role(db):
    u = _user(db, extra_roles=(UserRole.AUTHORITY.value,))
    bd = pe.ScoreBreakdown(score=1.0, volume=999, breadth=5, export_count=9,
                           automation=1.0, corporate_domain=True)
    p = pe.upsert_profile(db, u, bd)
    assert p.is_metered == 0   # vetted → never flagged, even at score 1.0


def test_upsert_is_idempotent_one_row_per_user(db):
    u = _user(db)
    bd = pe.ScoreBreakdown(score=0.2, volume=3, breadth=1, export_count=0,
                           automation=0.0, corporate_domain=False)
    pe.upsert_profile(db, u, bd)
    pe.upsert_profile(db, u, bd)
    assert db.query(UserUsageProfile).filter_by(user_id=u.id).count() == 1
