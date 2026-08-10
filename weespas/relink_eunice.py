#!/usr/bin/env python3
"""
One-shot fix: restore Eunice's access to her property listings.

Background: an earlier role-change (Agent -> Staff) cleared her users.agent_id,
which orphaned her from her property listings. We now keep agent_id on every
role change. This script:

  1. Finds her user row by email (set EUNICE_EMAIL or pass --email).
  2. Looks up the Agent record matching her phone OR by name OR by agent_id
     argument; relinks users.agent_id.
  3. Sets her roles to [agent, staff] (or whatever you pass via --roles).

Safe to re-run.

Usage:
  python relink_eunice.py --email eunice@example.com [--agent-id <uuid>] \
      [--roles agent staff]
"""

import argparse
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import or
from PE.weespas.core.database import SessionLocal
from PE.weespas.models.user import User, UserRole, UserRoleRow
from PE.weespas.models.property import Property

# Agent model lives in models/property.py per the existing import patterns;
# fall back to a query by name if needed.
try:
    from PE.weespas.models.property import Agent  # noqa: F401
except ImportError:
    Agent = None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", default=os.getenv("EUNICE_EMAIL"))
    parser.add_argument("--agent-id", help="If known, link directly to this Agent UUID")
    parser.add_argument("--roles", nargs="+", default=["agent", "staff"])
    args = parser.parse_args()

    if not args.email:
        print("ERROR: pass --email or set EUNICE_EMAIL")
        sys.exit(2)

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == args.email.lower()).first()
        if not user:
            print(f"ERROR: no user found with email={args.email}")
            sys.exit(1)
        print(f"Found user: id={user.id} name={user.name} email={user.email}")
        print(f"  Current role={user.role}, agent_id={user.agent_id}, roles={user.roles}")

        # Re-link agent_id if missing.
        if not user.agent_id:
            if args.agent_id:
                user.agent_id = args.agent_id
                print(f"  Linking agent_id -> {args.agent_id} (from --agent-id)")
            else:
                # Try to find an Agent record that owns properties created
                # while she was an agent. The Property.agent_id column points
                # at agents.id — find the most-used one historically by name.
                if Agent is None:
                    print("ERROR: Agent model not importable; pass --agent-id explicitly")
                    sys.exit(1)
                candidate = (
                    db.query(Agent)
                    .filter(or_(Agent.name.ilike(f"%{user.name}%"),))
                    .first()
                )
                if not candidate:
                    print(
                        "ERROR: could not locate an Agent record by name. "
                        "Pass --agent-id explicitly."
                    )
                    sys.exit(1)
                user.agent_id = candidate.id
                print(f"  Linking agent_id -> {candidate.id} (matched Agent.name)")

        # Replace roles.
        db.query(UserRoleRow).filter(UserRoleRow.user_id == user.id).delete(
            synchronize_session=False
        )
        for r in args.roles:
            db.add(UserRoleRow(user_id=user.id, role=r))

        priority = ["admin", "staff", "agent", "user"]
        primary = next((p for p in priority if p in args.roles), args.roles[0])
        user.role = UserRole(primary)

        db.commit()
        db.refresh(user)

        # Sanity-check that listings now resolve.
        listing_count = (
            db.query(Property)
            .filter(Property.agent_id == user.agent_id, Property.is_active == True)
            .count()
        )
        print(f"\nDone. role={user.role.value}, roles={user.roles}, "
              f"agent_id={user.agent_id}, active listings={listing_count}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
