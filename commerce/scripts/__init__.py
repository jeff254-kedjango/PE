"""Standalone operational scripts for the commerce service (seed/backfill utilities).

These run against the live DB via the existing sync ``SessionLocal`` — they are NOT imported by
the app at runtime. Each is idempotent and guarded so a re-run is safe.
"""
