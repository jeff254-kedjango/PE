"""Startup reconciliation — heal structural-flag exports that never got rebuilt.

WHY THIS EXISTS. When a structural flag is recorded in Weespas, the flag is
written to `data/structural_flags/<aoi>.json` (durable) and a *best-effort*
rebuild is triggered against the control API. "Best-effort" is deliberate — a
recorded flag must never fail because the InSAR side is down (see
weespas `structural_flag_export.trigger_rebuild`). But that leaves a gap: a flag
exported while this control stack was OFFLINE lands on disk with no rebuild, so
the served DuckDB keeps scoring the building as unflagged until the next manual
rebuild. The "Confirmed" shield silently disappears for that building.

This module closes the gap. On control-API startup it compares, per AOI, what the
export file *says* the flag states are against what the served DuckDB *currently*
carries, and enqueues a (debounced, provenance-aware) rebuild for any AOI that is
out of sync. After any downtime window the system self-heals with no operator
action.

DESIGN CHOICES (match the project's safety rules):
  * **Content comparison, not mtime.** We compare the actual resolved
    {building_id -> state} maps, so an already-in-sync AOI enqueues NOTHING — the
    check is idempotent and safe to run on every boot. (An mtime heuristic would
    re-fire spuriously and could thrash the seeder.)
  * **Reuses the existing debounce + provenance fan-out** (`tasks.request_rebuild`)
    rather than rebuilding here — reconciliation only DETECTS drift and asks; the
    one true rebuild path stays in tasks.py. A burst across AOIs still coalesces.
  * **Never raises into startup.** A reconciliation failure (missing DB, malformed
    export, broker down) is logged and skipped — serving the control API must not
    depend on the flag pipeline being perfectly healthy.
  * **O(buildings) once at boot**, one indexed read per AOI; no per-request cost.
"""
from __future__ import annotations

import logging
from pathlib import Path

import duckdb
import numpy as np

from scripts import aois
from scripts.structural_flags import _flags_path, fetch_structural_flags
from scripts.postprocess import STRUCT_NONE

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BACKEND_DIR / "data" / "demo.duckdb"


def _db_flag_states(con: duckdb.DuckDBPyConnection, aoi_code: str) -> dict[int, int]:
    """The NON-ZERO {building_id -> structural_flag_state} the served DB currently
    holds for this AOI. Only non-zero entries — an unflagged building and an absent
    row are the same thing for our comparison, so we never carry the 12k zeros."""
    rows = con.execute(
        "SELECT building_id, structural_flag_state FROM buildings "
        "WHERE aoi_code = ? AND structural_flag_state > 0",
        [aoi_code],
    ).fetchall()
    return {int(bid): int(state) for bid, state in rows}


def _export_flag_states(con: duckdb.DuckDBPyConnection, aoi_code: str) -> dict[int, int]:
    """What the export file resolves to for this AOI's buildings, computed through
    the SAME loader the build uses (`fetch_structural_flags`) so semantics match
    byte-for-byte (malformed/unknown -> NONE, etc.). Keyed to the AOI's real
    building ids (from the DB), so an export entry for an id we don't have is
    correctly ignored. Returns only non-zero states."""
    bid_rows = con.execute(
        "SELECT building_id FROM buildings WHERE aoi_code = ? ORDER BY building_id",
        [aoi_code],
    ).fetchall()
    bids = np.array([int(r[0]) for r in bid_rows], dtype=np.int64)
    if bids.size == 0:
        return {}
    state, _age, _obs, _src = fetch_structural_flags(aoi_code, bids)
    return {
        int(bids[i]): int(state[i])
        for i in range(len(bids))
        if int(state[i]) != STRUCT_NONE
    }


def find_drifted_aois() -> list[str]:
    """Return the AOI codes whose served flag states differ from their export file.

    Pure detection (no side effects). An AOI with no export file and no DB flags is
    in sync (both empty). An AOI is drifted iff its resolved export map != its DB
    map — covering a newly-added flag, a cleared/removed flag, or a changed state.
    """
    if not DB_PATH.exists():
        logger.info("reconcile: no served DB at %s yet — nothing to reconcile", DB_PATH)
        return []

    drifted: list[str] = []
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        for aoi in aois.REGISTRY:
            code = aoi.code
            # An AOI with neither an export nor any DB flag is trivially in sync —
            # skip the DB read entirely for the common (no-flags-yet) case.
            if not _flags_path(code).exists():
                # Still cheap-check the DB: if the export was DELETED but the DB
                # retains a stale flag, that is drift we should heal.
                db_only = _db_flag_states(con, code)
                if db_only:
                    logger.warning(
                        "reconcile: %s has %d DB flag(s) but no export file — drift",
                        code, len(db_only),
                    )
                    drifted.append(code)
                continue
            try:
                want = _export_flag_states(con, code)
                have = _db_flag_states(con, code)
            except (duckdb.Error, ValueError, OSError) as e:
                logger.warning("reconcile: could not compare aoi=%s, skipping: %s", code, e)
                continue
            if want != have:
                logger.warning(
                    "reconcile: %s out of sync — export has %d flag(s), DB has %d; "
                    "enqueuing rebuild", code, len(want), len(have),
                )
                drifted.append(code)
    finally:
        con.close()
    return drifted


def reconcile_on_startup() -> list[str]:
    """Detect drifted AOIs and enqueue a debounced rebuild for each. Returns the
    list of AOI codes for which a rebuild was requested (possibly empty).

    NEVER raises — any failure is logged and swallowed so the control API always
    starts. Importing tasks lazily keeps a broker hiccup from blocking startup.
    """
    try:
        drifted = find_drifted_aois()
    except Exception:  # pragma: no cover - defensive: detection must not crash boot
        logger.exception("reconcile: drift detection failed; skipping")
        return []

    if not drifted:
        logger.info("reconcile: all AOIs in sync with their flag exports")
        return []

    requested: list[str] = []
    for code in drifted:
        try:
            from scripts.pipeline import tasks
            tasks.request_rebuild.delay(code)
            requested.append(code)
        except Exception:  # pragma: no cover - broker down etc.
            logger.exception("reconcile: failed to enqueue rebuild for aoi=%s", code)
    if requested:
        logger.warning(
            "reconcile: enqueued debounced rebuild for %d drifted AOI(s): %s",
            len(requested), ", ".join(requested),
        )
    return requested
