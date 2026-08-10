#!/usr/bin/env python3
"""
Migration: introduce additive multi-role support.

Creates the `user_roles` association table and backfills one row per user
based on their current `users.role` column. The column itself is kept
(as the user's "primary" role for back-compat / indexing) — this migration
is non-destructive.

Safe to re-run.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text
from PE.weespas.core.database import SessionLocal


def migrate():
    db = SessionLocal()
    try:
        print("Creating user_roles table...")
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS user_roles (
                user_id VARCHAR NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                role VARCHAR(20) NOT NULL,
                granted_at TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (user_id, role)
            );
        """))
        db.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_user_roles_user_id ON user_roles(user_id)"
        ))
        db.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_user_roles_role ON user_roles(role)"
        ))
        db.commit()
        print("  user_roles table ready.")

        print("\nBackfilling user_roles from users.role...")
        result = db.execute(text("""
            INSERT INTO user_roles (user_id, role)
            SELECT id, role::text FROM users
            ON CONFLICT (user_id, role) DO NOTHING
        """))
        db.commit()
        backfilled = result.rowcount if result.rowcount is not None else 0
        print(f"  Backfilled {backfilled} role rows.")

        total_users = db.execute(text("SELECT COUNT(*) FROM users")).scalar()
        total_role_rows = db.execute(text("SELECT COUNT(*) FROM user_roles")).scalar()
        print(f"\nMigration complete.")
        print(f"  Users: {total_users}")
        print(f"  user_roles rows: {total_role_rows}")
    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate()
