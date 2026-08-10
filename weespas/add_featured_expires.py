#!/usr/bin/env python3
"""
One-time migration: add `featured_expires_at` column to `properties`.

- Idempotent: safe to re-run.
- Backfills currently-featured rows with NOW() + 30 days so they keep showing
  while admins decide on real expiry dates.

Run: python3 add_featured_expires.py
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import inspect, text

from PE.weespas.core.database import SessionLocal, engine


COLUMN_NAME = "featured_expires_at"
TABLE_NAME = "properties"
BACKFILL_DAYS = 30


def column_exists(conn, table: str, column: str) -> bool:
    inspector = inspect(conn)
    return column in {col["name"] for col in inspector.get_columns(table)}


def add_column(conn, dialect: str) -> None:
    # NULL is the safe default. We index it for query performance.
    conn.execute(text(
        f"ALTER TABLE {TABLE_NAME} ADD COLUMN {COLUMN_NAME} TIMESTAMP NULL"
    ))
    # Index name mirrors the SQLAlchemy convention so future create_all is a no-op.
    conn.execute(text(
        f"CREATE INDEX IF NOT EXISTS ix_{TABLE_NAME}_{COLUMN_NAME} "
        f"ON {TABLE_NAME} ({COLUMN_NAME})"
    ))
    print(f"  + Added column {TABLE_NAME}.{COLUMN_NAME} ({dialect})")


def backfill(db) -> int:
    """Set featured_expires_at = now + 30 days for currently-featured rows that
    don't already have an expiry set. Returns rows updated."""
    dialect = db.bind.dialect.name
    if dialect == "sqlite":
        sql = text(
            f"UPDATE {TABLE_NAME} "
            f"SET {COLUMN_NAME} = datetime('now', '+{BACKFILL_DAYS} days') "
            f"WHERE is_featured = 1 AND {COLUMN_NAME} IS NULL"
        )
    elif dialect == "postgresql":
        sql = text(
            f"UPDATE {TABLE_NAME} "
            f"SET {COLUMN_NAME} = NOW() + INTERVAL '{BACKFILL_DAYS} days' "
            f"WHERE is_featured = TRUE AND {COLUMN_NAME} IS NULL"
        )
    else:
        # MySQL and friends
        sql = text(
            f"UPDATE {TABLE_NAME} "
            f"SET {COLUMN_NAME} = DATE_ADD(NOW(), INTERVAL {BACKFILL_DAYS} DAY) "
            f"WHERE is_featured = 1 AND {COLUMN_NAME} IS NULL"
        )
    result = db.execute(sql)
    return result.rowcount or 0


def main() -> None:
    dialect = engine.dialect.name
    with engine.begin() as conn:
        if column_exists(conn, TABLE_NAME, COLUMN_NAME):
            print(f"  = Column {TABLE_NAME}.{COLUMN_NAME} already exists, skipping ALTER")
        else:
            add_column(conn, dialect)

    db = SessionLocal()
    try:
        updated = backfill(db)
        db.commit()
        print(f"  + Backfilled {updated} featured row(s) with +{BACKFILL_DAYS}-day expiry")
    except Exception as exc:
        db.rollback()
        print(f"  ! Backfill failed: {exc}")
        raise
    finally:
        db.close()

    print("Done.")


if __name__ == "__main__":
    main()
