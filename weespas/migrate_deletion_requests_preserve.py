#!/usr/bin/env python3
"""
Migration: preserve confirmed deletion requests after the target user is deleted.

Changes:
  1. Drop the CASCADE FK on deletion_requests.target_user_id and recreate it with
     ON DELETE SET NULL so approving a request no longer wipes the audit row.
  2. Relax the NOT NULL constraint on target_user_id (it can now be NULL once
     the target user has been hard-deleted).
  3. Add target_user_name_snapshot so the UI can still display "who was
     deleted" after the user row is gone.

Safe to re-run.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text
from PE.weespas.core.database import SessionLocal


def migrate() -> None:
    db = SessionLocal()
    try:
        print("Adding target_user_name_snapshot column...")
        db.execute(text("""
            ALTER TABLE deletion_requests
                ADD COLUMN IF NOT EXISTS target_user_name_snapshot VARCHAR
        """))
        db.commit()

        print("Relaxing NOT NULL on target_user_id...")
        db.execute(text("""
            ALTER TABLE deletion_requests
                ALTER COLUMN target_user_id DROP NOT NULL
        """))
        db.commit()

        print("Replacing CASCADE FK with SET NULL on target_user_id...")
        db.execute(text("""
            DO $$
            DECLARE
                fk_name TEXT;
            BEGIN
                SELECT tc.constraint_name INTO fk_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema = kcu.table_schema
                WHERE tc.table_name = 'deletion_requests'
                  AND tc.constraint_type = 'FOREIGN KEY'
                  AND kcu.column_name = 'target_user_id'
                LIMIT 1;

                IF fk_name IS NOT NULL THEN
                    EXECUTE format('ALTER TABLE deletion_requests DROP CONSTRAINT %I', fk_name);
                END IF;
            END $$;
        """))
        db.execute(text("""
            ALTER TABLE deletion_requests
                ADD CONSTRAINT deletion_requests_target_user_id_fkey
                FOREIGN KEY (target_user_id) REFERENCES users(id) ON DELETE SET NULL
        """))
        db.commit()

        print("Done.")
    except Exception as exc:
        print(f"Error: {exc}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate()
