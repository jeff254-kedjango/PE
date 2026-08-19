#!/usr/bin/env python3
"""
Migration script: Add role & agent_id columns to users table.
Seeds an admin user and links existing agents to new user accounts.
Safe to re-run (idempotent).
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text
from PE.weespas.core.database import SessionLocal
from PE.weespas.models.property import Agent
from PE.weespas.models.user import User
from PE.weespas.services.auth_service import hash_password
import uuid


def migrate():
    db = SessionLocal()

    try:
        # ── 1. Add columns to users table ──
        print("Adding role and agent_id columns to users table...")

        db.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(20) NOT NULL DEFAULT 'user'"
        ))
        db.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)"
        ))

        # agent_id FK — add column first, then constraint separately for IF NOT EXISTS safety
        db.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'users' AND column_name = 'agent_id'
                ) THEN
                    ALTER TABLE users ADD COLUMN agent_id VARCHAR;
                    ALTER TABLE users ADD CONSTRAINT fk_users_agent_id
                        FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE SET NULL;
                    CREATE UNIQUE INDEX uq_users_agent_id ON users(agent_id);
                END IF;
            END $$;
        """))

        db.commit()
        print("  Columns added successfully.")

        # ── 2. Seed admin user ──
        print("\nSeeding admin user...")
        existing_admin = db.query(User).filter(User.email == "admin@weespas.com").first()
        if existing_admin:
            # Promote to admin if not already
            if existing_admin.role != "admin":
                existing_admin.role = "admin"
                db.commit()
                print(f"  Existing user promoted to admin: {existing_admin.email}")
            else:
                print(f"  Admin already exists: {existing_admin.email}")
        else:
            admin = User(
                id=str(uuid.uuid4()),
                name="Weespas Admin",
                email="admin@weespas.com",
                phone="+254700000000",
                hashed_password=hash_password("admin123"),
                role="admin",
                is_active=True,
            )
            db.add(admin)
            db.commit()
            print(f"  Created admin: admin@weespas.com / admin123")

        # ── 3. Link existing agents to user accounts ──
        print("\nLinking agents to user accounts...")
        agents = db.query(Agent).filter(Agent.is_active == True).all()
        linked = 0

        for agent in agents:
            # Skip if already linked
            already_linked = db.query(User).filter(User.agent_id == agent.id).first()
            if already_linked:
                print(f"  - {agent.agent_name}: already linked to {already_linked.email}")
                continue

            # Try to find an existing user by phone match
            phone = agent.agent_phone_number
            existing_user = db.query(User).filter(User.phone == phone).first()

            if existing_user:
                existing_user.role = "agent"
                existing_user.agent_id = agent.id
                db.commit()
                print(f"  + {agent.agent_name}: linked to existing user {existing_user.email}")
                linked += 1
            else:
                # Create a new user for this agent
                email = agent.email or f"agent_{phone.replace('+', '')}@weespas.com"
                # Ensure email uniqueness
                if db.query(User).filter(User.email == email).first():
                    email = f"agent_{str(uuid.uuid4())[:8]}@weespas.com"

                new_user = User(
                    id=str(uuid.uuid4()),
                    name=agent.agent_name,
                    email=email,
                    phone=phone,
                    hashed_password=hash_password("agent123"),
                    role="agent",
                    agent_id=agent.id,
                    is_active=True,
                )
                db.add(new_user)
                db.commit()
                print(f"  + {agent.agent_name}: created user {email} / agent123")
                linked += 1

        # ── 4. Summary ──
        total_users = db.query(User).count()
        admin_count = db.query(User).filter(User.role == "admin").count()
        agent_count = db.query(User).filter(User.role == "agent").count()
        user_count = db.query(User).filter(User.role == "user").count()

        print(f"\nMigration complete!")
        print(f"  Total users: {total_users}")
        print(f"  Admins: {admin_count}")
        print(f"  Agents: {agent_count} ({linked} newly linked)")
        print(f"  Regular users: {user_count}")

    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate()
