#!/usr/bin/env python3
"""
Migration script: Add staff role support, is_public_profile column,
deletion_requests table, and grant Kwemange Nyagrowa admin + agent permissions.
Safe to re-run (idempotent).
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text
from PE.weespas.core.database import SessionLocal
from PE.weespas.models.user import User
from PE.weespas.services.auth_service import hash_password
import uuid


def migrate():
    db = SessionLocal()

    try:
        # ── 1. Add is_public_profile column to users table ──
        print("Adding is_public_profile column to users table...")
        db.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'users' AND column_name = 'is_public_profile'
                ) THEN
                    ALTER TABLE users ADD COLUMN is_public_profile BOOLEAN DEFAULT FALSE;
                    CREATE INDEX idx_users_is_public_profile ON users(is_public_profile);
                END IF;
            END $$;
        """))
        db.commit()
        print("  is_public_profile column ready.")

        # ── 2. Create deletion_requests table ──
        print("\nCreating deletion_requests table...")
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS deletion_requests (
                id VARCHAR PRIMARY KEY,
                target_user_id VARCHAR NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                requested_by_id VARCHAR REFERENCES users(id) ON DELETE SET NULL,
                reason TEXT NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                reviewed_by_id VARCHAR REFERENCES users(id) ON DELETE SET NULL,
                review_note TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                reviewed_at TIMESTAMPTZ
            );
        """))
        db.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_deletion_requests_status ON deletion_requests(status)"
        ))
        db.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_deletion_requests_target ON deletion_requests(target_user_id)"
        ))
        db.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_deletion_requests_requestor ON deletion_requests(requested_by_id)"
        ))
        db.commit()
        print("  deletion_requests table ready.")

        # ── 3. Grant Kwemange Nyagrowa admin + agent permissions ──
        print("\nGranting Kwemange Nyagrowa admin + agent permissions...")
        kwemange = db.query(User).filter(
            User.email == "kwemangenyagrowa@gmail.com"
        ).first()

        if kwemange:
            old_role = kwemange.role
            kwemange.role = "admin"
            db.commit()
            db.refresh(kwemange)
            print(f"  Found existing user: {kwemange.name} ({kwemange.email})")
            print(f"  Role changed: '{old_role}' -> 'admin'")
        else:
            # Create the user with admin role
            kwemange = User(
                id=str(uuid.uuid4()),
                name="Kwemange Nyagrowa",
                email="kwemangenyagrowa@gmail.com",
                phone="+254700000001",
                hashed_password=hash_password("WeespasAdmin2024!"),
                role="admin",
                is_active=True,
                is_public_profile=False,
            )
            db.add(kwemange)
            db.commit()
            db.refresh(kwemange)
            print(f"  Created admin user: {kwemange.name} ({kwemange.email})")
            print(f"  Temporary password: WeespasAdmin2024! (change immediately!)")

        # ── 4. Summary ──
        total_users = db.query(User).count()
        admin_count = db.query(User).filter(User.role == "admin").count()
        staff_count = db.query(User).filter(User.role == "staff").count()
        agent_count = db.query(User).filter(User.role == "agent").count()
        user_count = db.query(User).filter(User.role == "user").count()

        print(f"\nMigration complete!")
        print(f"  Total users: {total_users}")
        print(f"  Admins: {admin_count}")
        print(f"  Staff: {staff_count}")
        print(f"  Agents: {agent_count}")
        print(f"  Regular users: {user_count}")

    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate()
