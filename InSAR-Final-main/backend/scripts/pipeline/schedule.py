"""Celery beat schedule for periodic AOI refresh — OPT-IN, default OFF.

Sentinel-1 re-observes each AOI on a ~12-day repeat cycle, so a real deployment
wants to periodically pull new acquisitions and rebuild. But auto-firing the
pipeline is only meaningful on an always-on host wired to ASF/HyP3 with the
real-data acquisition path live. On a laptop (the current setup) or in a
synthetic-only checkout, a beat tick would just spawn HyP3 tasks that fail.

So the schedule is EMPTY unless explicitly enabled:

    INSAR_BEAT_ENABLED=1        turn the schedule on
    INSAR_BEAT_AOIS=huruma,mombasa   which AOIs to refresh (default: all in registry)
    INSAR_BEAT_DAYS=12          cadence in days (default 12 — S1 repeat cycle)

Run beat alongside a worker (only on a host where real refresh makes sense):

    celery -A scripts.pipeline.celery_app beat --loglevel=info

The scheduled task is `insar.refresh_aoi`, the same chain an operator triggers
by hand — it halts cleanly at the OpenSARLab MintPy gate if SBAS outputs aren't
present, so even an enabled schedule never fabricates data.
"""
from __future__ import annotations

import os
from datetime import timedelta


def build_beat_schedule() -> dict:
    """Return a Celery beat_schedule dict — empty unless INSAR_BEAT_ENABLED is set."""
    if os.environ.get("INSAR_BEAT_ENABLED", "").lower() not in ("1", "true", "yes"):
        return {}

    # Lazy import so this module is import-safe without the registry/deps loaded.
    aois_env = os.environ.get("INSAR_BEAT_AOIS", "").strip()
    if aois_env:
        codes = [c.strip() for c in aois_env.split(",") if c.strip()]
    else:
        from scripts import aois
        codes = [a.code for a in aois.REGISTRY]

    try:
        days = max(1, int(os.environ.get("INSAR_BEAT_DAYS", "12")))
    except ValueError:
        days = 12

    schedule: dict = {}
    for code in codes:
        schedule[f"refresh-{code}"] = {
            "task": "insar.refresh_aoi",
            "schedule": timedelta(days=days),
            "args": (code,),
            "options": {"queue": "insar"},
        }
    return schedule
