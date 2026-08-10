#!/usr/bin/env python3
"""
Migration: create the `role_applications` table.

Backs the self-service Become Agent / Become Staff flow. Mirrors
`add_user_roles_table.py`'s idempotent CREATE IF NOT EXISTS style so it's
safe to re-run during phased rollout.

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
        print("Creating role_applications table...")
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS role_applications (
                id VARCHAR PRIMARY KEY,
                applicant_id VARCHAR NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                role_requested VARCHAR(20) NOT NULL,
                message TEXT NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                reviewed_by_id VARCHAR REFERENCES users(id) ON DELETE SET NULL,
                review_note TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                reviewed_at TIMESTAMPTZ
            );
        """))
        # Composite (status, role_requested) — admin queue scan. Covers
        # both the tab list (`WHERE status='pending' AND role_requested='agent'`)
        # and the badge counters in one index.
        db.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_role_apps_status_role "
            "ON role_applications(status, role_requested)"
        ))
        # (applicant_id, status) — duplicate-suppression check on every
        # POST. Without this, the duplicate guard would scan all of a
        # user's history.
        db.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_role_apps_applicant_status "
            "ON role_applications(applicant_id, status)"
        ))
        # (created_at index is added automatically by SQLAlchemy because
        # `created_at` is declared with `index=True` in models/role_application.py.
        # We intentionally do not duplicate it here.)
        db.commit()
        print("  role_applications table ready.")

        total = db.execute(text("SELECT COUNT(*) FROM role_applications")).scalar()
        print(f"\nMigration complete. Rows: {total}")
    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate()
