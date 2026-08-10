"""Role-grant primitives shared by `routers/admin.py` and
`routers/role_applications.py`.

Three operations live here:

  - `ensure_agent_profile(db, user)`     — idempotent link/create of an
                                            Agent row for a user gaining
                                            the `agent` role.
  - `grant_role_additive(db, user, role)` — add ONE role to the user's
                                            existing set, preserving every
                                            other role and `agent_id`.
                                            Used by application-approval
                                            paths where we never want to
                                            silently *strip* a role.
  - `_recompute_primary_role(user)`      — set `users.role` to the
                                            highest-priority role the user
                                            now has, for back-compat.

`routers/admin.py:_replace_user_roles` continues to live there for the
*replace* semantics it implements; the helpers below cover the *additive*
case only. We deliberately do NOT merge the two: replace and add have
different invariants (replace is allowed to remove `admin`-from-self only
with a guard; add never needs that guard) and conflating them yields the
worst-of-both API.

Performance posture:
  - One INSERT per `grant_role_additive` (skipped on conflict).
  - One UPDATE on `users.role` only when the primary actually shifts.
  - Zero N+1 risk — `ensure_agent_profile` does its own batched lookup.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from PE.weespas.models.user import User, UserRole, UserRoleRow
from PE.weespas.models.property import Agent


_ROLE_PRIORITY = ("admin", "staff", "agent", "user")


def ensure_agent_profile(db: Session, user: User) -> None:
    """Link an existing Agent row by email/phone, or create a fresh one.

    No-op if `user.agent_id` is already populated. Raises 409 if an Agent
    row matching the user's contact info is already linked to a different
    User — admins must resolve that conflict before the role can be
    granted.

    Idempotent within a single transaction. Caller is expected to commit.
    """
    if user.agent_id:
        return

    existing = (
        db.query(Agent)
        .filter(
            Agent.is_active.is_(True),
            or_(Agent.email == user.email, Agent.agent_phone_number == user.phone),
        )
        .first()
    )
    if existing:
        already_linked = (
            db.query(User.id)
            .filter(User.agent_id == existing.id, User.id != user.id)
            .first()
        )
        if already_linked:
            raise HTTPException(
                status_code=409,
                detail=(
                    "An agent profile matching this user's email/phone is already "
                    "linked to another account. Resolve the conflict before "
                    "granting the agent role."
                ),
            )
        user.agent_id = existing.id
        return

    new_agent = Agent(
        agent_name=user.name,
        agent_phone_number=user.phone,
        email=user.email,
        agent_profile_picture=user.avatar,
        is_active=True,
    )
    db.add(new_agent)
    db.flush()  # populate new_agent.id without committing
    user.agent_id = new_agent.id


def _recompute_primary_role(user: User, current_roles: list[str]) -> None:
    """Set `users.role` to the highest-priority role the user holds.

    Kept in sync with `_replace_user_roles` in `routers/admin.py` so the
    invariant "users.role == max-priority of user_roles" holds platform-wide.
    """
    primary = next((r for r in _ROLE_PRIORITY if r in current_roles), current_roles[0])
    if user.role.value != primary:
        user.role = UserRole(primary)


def grant_role_additive(db: Session, user: User, role: str) -> list[str]:
    """Add `role` to the user's existing set of roles. Idempotent.

    Returns the user's final role list (sorted by priority). Caller is
    expected to commit; this function does NOT commit so it can compose
    inside larger transactions (e.g. the role-application approval flow
    that also updates the application row in the same txn).

    Why not reuse `_replace_user_roles`: that function expects a complete
    role list and would drop any role we don't pass in. Application
    approvals must NEVER drop a role — an admin who's also an agent who
    is being granted `staff` should end up with all three.
    """
    if role not in _ROLE_PRIORITY:
        raise HTTPException(status_code=400, detail=f"Unknown role: {role}")

    # Idempotency: existing UserRoleRow → return early. UNIQUE PK
    # (user_id, role) would raise IntegrityError but it's cheaper to
    # check first than to round-trip a rolled-back error to the client.
    existing = (
        db.query(UserRoleRow)
        .filter(UserRoleRow.user_id == user.id, UserRoleRow.role == role)
        .first()
    )
    if not existing:
        db.add(UserRoleRow(user_id=user.id, role=role))
        # If granting `agent`, ensure the Agent row exists in the same
        # transaction. ensure_agent_profile is a no-op when already
        # linked, so it's safe to call unconditionally for the agent case.
        if role == "agent":
            ensure_agent_profile(db, user)
        db.flush()

    # Recompute the union of roles AFTER the flush so the priority
    # calculation sees the new row.
    current_roles = [
        r.role
        for r in db.query(UserRoleRow).filter(UserRoleRow.user_id == user.id).all()
    ]
    if not current_roles:  # defensive — should never happen post-flush
        current_roles = [role]
    _recompute_primary_role(user, current_roles)
    return sorted(current_roles, key=lambda r: _ROLE_PRIORITY.index(r) if r in _ROLE_PRIORITY else 99)
