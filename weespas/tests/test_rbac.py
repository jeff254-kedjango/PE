"""Role-based access control logic.

The require_* dependencies are factories returning a callable that takes
`current_user` (FastAPI injects it via Depends in production; here we call it
directly). We construct detached User objects — the `roles` property falls
back to `user.role` when no user_roles rows are attached, so no DB is needed.
"""

import types

import pytest
from fastapi import HTTPException

from PE.weespas.models.user import User, UserRole
from PE.weespas.services.auth_service import (
    require_admin,
    require_agent,
    require_staff,
    verify_property_ownership,
)


def _user(role: UserRole, agent_id: str | None = None) -> User:
    return User(name="t", email="t@e.com", phone="1", hashed_password="x",
                role=role, agent_id=agent_id)


# ── require_agent: agent, staff, admin pass; plain user is rejected ──────
@pytest.mark.parametrize("role", [UserRole.AGENT, UserRole.STAFF, UserRole.ADMIN])
def test_require_agent_allows_elevated(role):
    user = _user(role)
    assert require_agent(current_user=user) is user


def test_require_agent_blocks_plain_user():
    with pytest.raises(HTTPException) as exc:
        require_agent(current_user=_user(UserRole.USER))
    assert exc.value.status_code == 403


# ── require_staff: only staff/admin ──────────────────────────────────────
def test_require_staff_blocks_agent():
    with pytest.raises(HTTPException) as exc:
        require_staff(current_user=_user(UserRole.AGENT))
    assert exc.value.status_code == 403


def test_require_staff_allows_admin():
    user = _user(UserRole.ADMIN)
    assert require_staff(current_user=user) is user


# ── require_admin: only admin ─────────────────────────────────────────────
@pytest.mark.parametrize("role", [UserRole.USER, UserRole.AGENT, UserRole.STAFF])
def test_require_admin_blocks_non_admin(role):
    with pytest.raises(HTTPException) as exc:
        require_admin(current_user=_user(role))
    assert exc.value.status_code == 403


# ── verify_property_ownership ─────────────────────────────────────────────
def _prop(agent_id):
    return types.SimpleNamespace(agent_id=agent_id)


def test_owner_agent_may_modify():
    user = _user(UserRole.AGENT, agent_id="agent-1")
    verify_property_ownership(user, _prop("agent-1"))  # no raise


def test_non_owner_agent_blocked():
    user = _user(UserRole.AGENT, agent_id="agent-1")
    with pytest.raises(HTTPException) as exc:
        verify_property_ownership(user, _prop("agent-2"))
    assert exc.value.status_code == 403


def test_admin_bypasses_ownership():
    admin = _user(UserRole.ADMIN, agent_id=None)
    verify_property_ownership(admin, _prop("someone-else"))  # no raise
