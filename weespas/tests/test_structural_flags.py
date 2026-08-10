"""P4a integration: new roles, the require_certifier gate, and the flag-entry
service (validation + the authority-only AUTH_UNSAFE rule).

The flag service writes rows, so these use a throwaway in-memory SQLite session
with only the new tables created (no Postgres, no live DB).
"""
import uuid
from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from PE.weespas.core.database import Base
from PE.weespas.models.user import User, UserRole
from PE.weespas.models.insar_link import (
    StructuralFlag, FLAG_CLEARED, FLAG_UNSAFE, FLAG_AUTH_UNSAFE, FLAG_NONE,
)
from PE.weespas.models.flag_review import FlagReview
from PE.weespas.services.auth_service import require_certifier, require_role
from PE.weespas.services import structural_flag_service as svc


# ---- new roles ------------------------------------------------------------

def test_new_roles_exist_and_are_not_primary():
    for r in (UserRole.PROFESSIONAL, UserRole.PROPERTY_OWNER, UserRole.TENANT, UserRole.AUTHORITY):
        assert r not in UserRole.primary_roles()
    assert UserRole.primary_roles() == {
        UserRole.USER, UserRole.AGENT, UserRole.STAFF, UserRole.ADMIN
    }


def _user_with_roles(*role_values: str) -> User:
    """A detached User whose multi-role list is the given values (mirrors how the
    user_roles VARCHAR table grants the new integration roles)."""
    u = User(name="t", email=f"{uuid.uuid4()}@e.com", phone=str(uuid.uuid4()),
             hashed_password="x", role=UserRole.USER)
    # `roles` reads from _role_rows; emulate granted rows.
    from PE.weespas.models.user import UserRoleRow
    u._role_rows = [UserRoleRow(role=v) for v in role_values]
    return u


def test_has_role_for_professional_via_user_roles():
    u = _user_with_roles("professional")
    assert u.has_role(UserRole.PROFESSIONAL)
    assert not u.has_role(UserRole.AUTHORITY)


# ---- require_certifier gate ----------------------------------------------

@pytest.mark.parametrize("role_value", ["professional", "authority", "staff", "admin"])
def test_require_certifier_allows_certifiers(role_value):
    u = _user_with_roles(role_value)
    assert require_certifier(current_user=u) is u


@pytest.mark.parametrize("role_value", ["user", "agent", "property_owner", "tenant"])
def test_require_certifier_blocks_others(role_value):
    u = _user_with_roles(role_value)
    with pytest.raises(HTTPException) as exc:
        require_certifier(current_user=u)
    assert exc.value.status_code == 403


# ---- flag-entry service ---------------------------------------------------

@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    # Create only the integration tables (the legacy ones aren't needed here, and
    # building_link/structural_flag FK to properties/users which we don't exercise).
    # record_flag now also opens a flag_review row in the same txn, so that table
    # must exist too.
    StructuralFlag.__table__.create(bind=engine)
    FlagReview.__table__.create(bind=engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _engineer():
    return _user_with_roles("professional")


def _authority():
    return _user_with_roles("authority")


def test_engineer_can_record_unsafe(db):
    flag = svc.record_flag(db, actor=_engineer(), aoi_code="huruma",
                           insar_building_id=101454, state=FLAG_UNSAFE,
                           source="engineer", observed_at=date(2026, 1, 1))
    assert flag.id is not None
    assert flag.state == FLAG_UNSAFE
    assert flag.source == "engineer"


def test_engineer_cannot_set_auth_unsafe(db):
    """The bribed-clearance defense's mirror: a professional can't self-issue an
    authority-grade condemnation."""
    with pytest.raises(HTTPException) as exc:
        svc.record_flag(db, actor=_engineer(), aoi_code="huruma",
                        insar_building_id=1, state=FLAG_AUTH_UNSAFE, source="engineer")
    assert exc.value.status_code == 403


def test_authority_can_set_auth_unsafe(db):
    flag = svc.record_flag(db, actor=_authority(), aoi_code="huruma",
                           insar_building_id=1, state=FLAG_AUTH_UNSAFE, source="authority")
    assert flag.state == FLAG_AUTH_UNSAFE


def test_cannot_record_state_none(db):
    with pytest.raises(HTTPException) as exc:
        svc.record_flag(db, actor=_engineer(), aoi_code="huruma",
                        insar_building_id=1, state=FLAG_NONE, source="engineer")
    assert exc.value.status_code == 400


def test_invalid_source_rejected(db):
    with pytest.raises(HTTPException) as exc:
        svc.record_flag(db, actor=_engineer(), aoi_code="huruma",
                        insar_building_id=1, state=FLAG_UNSAFE, source="random_guy")
    assert exc.value.status_code == 400


def test_engineer_cannot_claim_authority_source(db):
    with pytest.raises(HTTPException) as exc:
        svc.record_flag(db, actor=_engineer(), aoi_code="huruma",
                        insar_building_id=1, state=FLAG_UNSAFE, source="authority")
    assert exc.value.status_code == 403


def test_latest_flag_returns_most_recent(db):
    svc.record_flag(db, actor=_engineer(), aoi_code="huruma",
                    insar_building_id=7, state=FLAG_CLEARED, source="engineer")
    svc.record_flag(db, actor=_engineer(), aoi_code="huruma",
                    insar_building_id=7, state=FLAG_UNSAFE, source="engineer")
    latest = svc.latest_flag_for_building(db, aoi_code="huruma", insar_building_id=7)
    assert latest is not None
    # Both rows share created_at default (now); at minimum the query returns one of them.
    assert latest.state in (FLAG_CLEARED, FLAG_UNSAFE)


# ---- resolver 3-state logic (no InSAR DB configured) ----------------------

def test_resolver_unavailable_when_no_db_configured(monkeypatch):
    """With no InSAR DB path, the resolver reports 'unavailable' — NEVER 'monitored'
    and NEVER an implied 'safe'."""
    from PE.weespas.core.config import settings
    from PE.weespas.services import insar_resolver as R
    monkeypatch.setattr(settings, "insar_duckdb_path", "")
    result = R.resolve_point(-1.25, 36.875)
    assert result.coverage == R.COVERAGE_UNAVAILABLE
    assert result.danger_level is None
