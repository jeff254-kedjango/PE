#!/usr/bin/env python3
"""Migration: add users.last_seen_at TIMESTAMPTZ NULL + index. Idempotent."""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text
from PE.weespas.core.database import SessionLocal


def migrate():
    db = SessionLocal()
    try:
        print("Adding last_seen_at column to users table...")
        db.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'users' AND column_name = 'last_seen_at'
                ) THEN
                    ALTER TABLE users ADD COLUMN last_seen_at TIMESTAMPTZ NULL;
                    CREATE INDEX idx_users_last_seen_at ON users(last_seen_at);
                END IF;
            END $$;
        """))
        db.commit()
        print("  last_seen_at column ready.")
    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate()
