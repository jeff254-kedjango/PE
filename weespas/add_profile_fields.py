#!/usr/bin/env python3
"""Migration: add Phase 6/8/9 columns to `users`.

Idempotent — safe to re-run. Performance posture: every new column has
either a DEFAULT or is nullable so the ALTER TABLE is a metadata-only
operation on Postgres (no full table rewrite). On SQLite (dev), each
ALTER appends a column instantly.

Columns added:
  notify_inquiries_sms        BOOLEAN  NOT NULL DEFAULT TRUE
  notify_inquiries_email      BOOLEAN  NOT NULL DEFAULT FALSE
  notify_digest_email         BOOLEAN  NOT NULL DEFAULT FALSE
  notify_push                 BOOLEAN  NOT NULL DEFAULT FALSE
  default_radius_km           INTEGER  DEFAULT 10
  preferred_listing_type      VARCHAR(16)
  language                    VARCHAR(8) DEFAULT 'en'
  pending_phone               VARCHAR(20)
  pending_email               VARCHAR(255)
  pending_contact_otp_hash    VARCHAR(128)
  pending_contact_expires_at  TIMESTAMP WITH TIME ZONE
  pending_contact_kind        VARCHAR(8)

No index is created — none of these columns are filtered on a hot path.
The notify_inquiries_sms column is the only candidate for indexing if
the inquiry-fanout ever scales beyond a few thousand recipients/day.
"""
from __future__ import annotations

import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import inspect, text

from PE.weespas.core.database import SessionLocal, engine


COLUMNS = [
    ("notify_inquiries_sms", "BOOLEAN NOT NULL DEFAULT TRUE"),
    ("notify_inquiries_email", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("notify_digest_email", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("notify_push", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("default_radius_km", "INTEGER DEFAULT 10"),
    ("preferred_listing_type", "VARCHAR(16)"),
    ("language", "VARCHAR(8) DEFAULT 'en'"),
    ("pending_phone", "VARCHAR(20)"),
    ("pending_email", "VARCHAR(255)"),
    ("pending_contact_otp_hash", "VARCHAR(128)"),
    ("pending_contact_expires_at", "TIMESTAMP WITH TIME ZONE"),
    ("pending_contact_kind", "VARCHAR(8)"),
]


def existing_columns() -> set[str]:
    insp = inspect(engine)
    return {c["name"] for c in insp.get_columns("users")}


def main() -> None:
    have = existing_columns()
    added = 0
    with engine.begin() as conn:
        for name, ddl in COLUMNS:
            if name in have:
                print(f"  · {name} already present, skip")
                continue
            # SQLite doesn't support `TIMESTAMP WITH TIME ZONE` syntax —
            # downgrade gracefully so dev environments don't break.
            sql_ddl = ddl
            if engine.dialect.name == "sqlite":
                sql_ddl = sql_ddl.replace("TIMESTAMP WITH TIME ZONE", "TIMESTAMP")
                # SQLite ALTER TABLE … ADD COLUMN doesn't accept NOT NULL
                # without a default — every entry above already has one,
                # but strip the explicit NOT NULL to keep the syntax narrow.
                sql_ddl = sql_ddl.replace("NOT NULL ", "")
            conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {sql_ddl}"))
            print(f"  + added {name} {sql_ddl}")
            added += 1
    print(f"\nDone. {added} column(s) added.")


if __name__ == "__main__":
    main()
