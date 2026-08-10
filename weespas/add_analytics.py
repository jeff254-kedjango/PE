#!/usr/bin/env python3
"""
Migration: add analytics tables and extend contact_submissions.

- Creates user_sessions, property_view_events, search_logs, favorites
  (handled by Base.metadata.create_all via main.py startup, but we call
  it here too so the script is self-sufficient).
- Adds property_id (FK) and ip_address columns to contact_submissions.

Idempotent — safe to re-run.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text
from PE.weespas.core.database import SessionLocal, Base, engine
from PE.weespas.models import (  # noqa: F401  (ensures models register on Base)
    UserSession, PropertyViewEvent, SearchLog, Favorite, ContactSubmission,
)


def migrate():
    print("Creating analytics tables (if missing)...")
    Base.metadata.create_all(bind=engine)
    print("  Tables ensured.")

    db = SessionLocal()
    try:
        print("\nExtending contact_submissions...")
        db.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'contact_submissions' AND column_name = 'property_id'
                ) THEN
                    ALTER TABLE contact_submissions
                        ADD COLUMN property_id VARCHAR;
                    ALTER TABLE contact_submissions
                        ADD CONSTRAINT fk_contact_property
                        FOREIGN KEY (property_id) REFERENCES properties(id) ON DELETE SET NULL;
                    CREATE INDEX idx_contact_property_id ON contact_submissions(property_id);
                END IF;

                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'contact_submissions' AND column_name = 'ip_address'
                ) THEN
                    ALTER TABLE contact_submissions
                        ADD COLUMN ip_address VARCHAR(64);
                END IF;
            END $$;
        """))
        db.commit()
        print("  contact_submissions extended.")

        print("\nMigration complete.")
    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate()
