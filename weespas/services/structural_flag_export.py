"""Export the structural_flag table → the per-AOI JSON the InSAR build reads.

This is the manual-entry → InSAR sync seam: an engineer/authority records a flag in
Weespas (structural_flag table), this exporter writes it to
`<insar_flags_export_dir>/<aoi>.json`, and the next InSAR build's
`fetch_structural_flags()` picks it up and fuses it into the collapse score.

Format MUST match scripts/structural_flags.py exactly:
    {
      "as_of": "2026-06-22",
      "flags": {
        "<building_id>": {"state": 2, "observed_at": "2025-01-10", "source": "engineer"},
        ...
      }
    }

Only the MOST-RECENT flag per (aoi, building) is exported — the loader expects one
entry per building id, and "latest judgement wins" is the right semantics (a later
inspection supersedes an earlier one). FLAG_NONE rows are skipped (they aren't
recordable anyway), so a building with no actionable flag simply isn't in the file —
which the loader treats as STRUCT_NONE. Writes atomically (temp + rename) so a build
never reads a half-written file.

Pure I/O over the DB session; no Celery dependency (an admin route or a CLI calls it).
`as_of` is injected by the caller (defaults to today) to keep this testable.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import urllib.error
import urllib.request
from datetime import date, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from PE.weespas.core.config import settings
from PE.weespas.models.insar_link import StructuralFlag, FLAG_NONE

logger = logging.getLogger(__name__)


def _latest_flags(db: Session, aoi_code: str) -> dict[str, dict]:
    """Most-recent recordable flag per building for one AOI, as the loader's `flags` map.

    "Latest" = the most recent JUDGEMENT, i.e. by `observed_at` (the inspection date),
    with `created_at` (record time) as the tiebreak. We sort in Python rather than SQL
    because (a) row counts per AOI are small (manual entries), and (b) NULL-ordering of
    `observed_at` differs between SQLite and Postgres — an explicit key is deterministic
    and DB-agnostic. A flag with no observed_at sorts oldest on that axis (it falls back
    to record time), so a dated judgement always supersedes an undated one.
    """
    rows = (
        db.query(StructuralFlag)
        .filter(StructuralFlag.aoi_code == aoi_code,
                StructuralFlag.state != FLAG_NONE)
        .all()
    )

    def _sort_key(r):
        # Newest judgement first: max observed_at, then max created_at. created_at is
        # tz-aware on Postgres but naive on SQLite; normalise to a comparable epoch so
        # the sort never raises on a naive/aware mismatch.
        obs = r.observed_at or date.min
        created = r.created_at
        if created is None:
            ts = 0.0
        else:
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            ts = created.timestamp()
        return (obs, ts)

    flags: dict[str, dict] = {}
    for r in sorted(rows, key=_sort_key, reverse=True):
        key = str(int(r.insar_building_id))
        if key in flags:
            continue  # already have the newer judgement for this building
        flags[key] = {
            "state": int(r.state),
            "observed_at": r.observed_at.isoformat() if r.observed_at else None,
            "source": r.source,
        }
    return flags


def export_aoi(db: Session, aoi_code: str, *, export_dir: str | None = None,
               as_of: date | None = None) -> Path | None:
    """Write `<export_dir>/<aoi_code>.json` for one AOI. Returns the path written,
    or None if export is disabled (no dir configured)."""
    target_dir = export_dir or settings.insar_flags_export_dir
    if not target_dir:
        return None
    out_dir = Path(target_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    doc = {
        "as_of": (as_of or date.today()).isoformat(),
        "flags": _latest_flags(db, aoi_code),
    }
    out_path = out_dir / f"{aoi_code}.json"
    # Atomic write: never let the InSAR build read a partial file.
    fd, tmp = tempfile.mkstemp(dir=str(out_dir), suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(doc, fh, indent=2, sort_keys=True)
        os.replace(tmp, out_path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return out_path


def export_all(db: Session, *, export_dir: str | None = None,
               as_of: date | None = None) -> list[Path]:
    """Export every AOI that has at least one flag. Returns the paths written."""
    target_dir = export_dir or settings.insar_flags_export_dir
    if not target_dir:
        return []
    aoi_codes = [
        row[0]
        for row in db.query(StructuralFlag.aoi_code)
        .filter(StructuralFlag.state != FLAG_NONE)
        .distinct()
        .all()
    ]
    return [
        p for code in aoi_codes
        if (p := export_aoi(db, code, export_dir=target_dir, as_of=as_of)) is not None
    ]


def trigger_rebuild(aoi_code: str) -> bool:
    """Best-effort: ask the InSAR control API to (debounced) rebuild this AOI so the
    just-exported flag reaches the score. Returns True if the enqueue was accepted.

    No-op (returns False) when the control API isn't configured — the export has
    already happened, so an operator/scheduled rebuild will pick it up regardless.
    NEVER raises: a recorded flag must not fail because the InSAR side is down.
    """
    base = settings.insar_control_api_url.rstrip("/")
    token = settings.insar_admin_token
    if not base or not token:
        return False
    url = f"{base}/admin/request-rebuild"
    body = json.dumps({"aoi": aoi_code}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", "X-Admin-Token": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=settings.insar_control_timeout_s) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, ValueError) as e:
        logger.warning("InSAR rebuild trigger failed for aoi=%s (flag exported, rebuild "
                       "deferred to operator): %s", aoi_code, e)
        return False
