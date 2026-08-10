"""Tests for startup structural-flag reconciliation (scripts/pipeline/reconcile_flags).

This is the self-heal for the exact bug that hid the "Confirmed" shield: a flag
exported while the InSAR control stack was offline lands on disk, its best-effort
rebuild is a no-op, and the served DuckDB keeps scoring the building unflagged.
On next control-API boot, reconciliation must detect that drift and enqueue a
rebuild — while staying silent when everything is already in sync.

These tests build a tiny synthetic DuckDB + export dir in tmp_path and monkeypatch
the module's paths, so they touch NO real data and need no broker (enqueue is
stubbed). The comparison contract is what's under test, not the science.
"""
import json

import duckdb
import pytest

from scripts.pipeline import reconcile_flags as rf
from scripts.structural_flags import FLAGS_DIR as _REAL_FLAGS_DIR  # noqa: F401  (doc anchor)


# A two-AOI synthetic registry entry shape — only `.code` is read by reconcile.
class _AOI:
    def __init__(self, code):
        self.code = code


@pytest.fixture
def synthetic_db(tmp_path, monkeypatch):
    """A minimal served DB with a `buildings` view-equivalent table, plus an export
    dir, both wired into reconcile_flags + structural_flags. Returns helpers to set
    DB flags and export flags independently so a test can create drift."""
    db_path = tmp_path / "demo.duckdb"
    flags_dir = tmp_path / "structural_flags"
    flags_dir.mkdir()

    # Two AOIs: 'alpha' (will hold flags) and 'beta' (always empty / in sync).
    registry = [_AOI("alpha"), _AOI("beta")]
    monkeypatch.setattr(rf, "aois", type("M", (), {"REGISTRY": registry}))
    monkeypatch.setattr(rf, "DB_PATH", db_path)
    # Point the loader's flag dir at our tmp export dir (it reads FLAGS_DIR/<aoi>.json).
    import scripts.structural_flags as sf
    monkeypatch.setattr(sf, "FLAGS_DIR", flags_dir)

    # Seed the buildings table. structural_flag_state defaults to 0 (unflagged).
    con = duckdb.connect(str(db_path))
    con.execute(
        "CREATE TABLE buildings (aoi_code VARCHAR, building_id BIGINT, "
        "structural_flag_state UTINYINT)"
    )
    for code in ("alpha", "beta"):
        for bid in (1, 2, 3):
            con.execute("INSERT INTO buildings VALUES (?, ?, 0)", [code, bid])
    con.close()

    def set_db_flag(aoi, bid, state):
        c = duckdb.connect(str(db_path))
        c.execute(
            "UPDATE buildings SET structural_flag_state=? WHERE aoi_code=? AND building_id=?",
            [state, aoi, bid],
        )
        c.close()

    def write_export(aoi, flags: dict):
        # flags: {building_id(int): state(int)}
        doc = {
            "as_of": "2026-06-27",
            "flags": {
                str(bid): {"state": st, "observed_at": "2026-06-27", "source": "authority"}
                for bid, st in flags.items()
            },
        }
        (flags_dir / f"{aoi}.json").write_text(json.dumps(doc))

    return type("Ctx", (), {
        "db_path": db_path, "flags_dir": flags_dir,
        "set_db_flag": staticmethod(set_db_flag),
        "write_export": staticmethod(write_export),
    })


def test_no_drift_when_db_matches_export(synthetic_db):
    # alpha: one flag, present in BOTH export and DB → in sync.
    synthetic_db.write_export("alpha", {2: 1})
    synthetic_db.set_db_flag("alpha", 2, 1)
    assert rf.find_drifted_aois() == []


def test_no_drift_when_both_empty(synthetic_db):
    # No exports, no DB flags anywhere → fully in sync, no rebuilds.
    assert rf.find_drifted_aois() == []


def test_detects_export_ahead_of_db(synthetic_db):
    # THE BUG: export records a flag the served DB never picked up (rebuild was a no-op).
    synthetic_db.write_export("alpha", {2: 2})  # export says building 2 is AUTH_UNSAFE
    # DB still has it at 0 → drift on alpha only.
    drifted = rf.find_drifted_aois()
    assert drifted == ["alpha"]


def test_detects_changed_state(synthetic_db):
    # Export upgraded a building's state (e.g. CLEARED -> UNSAFE) but DB still old.
    synthetic_db.write_export("alpha", {1: 2})
    synthetic_db.set_db_flag("alpha", 1, 1)  # DB has the old (different) state
    assert rf.find_drifted_aois() == ["alpha"]


def test_detects_stale_db_flag_after_export_removed(synthetic_db):
    # Export no longer lists a building the DB still flags (flag was cleared/removed).
    # No export file for alpha, but DB carries a flag → drift to heal.
    synthetic_db.set_db_flag("alpha", 3, 1)
    assert rf.find_drifted_aois() == ["alpha"]


def test_export_entry_for_unknown_building_is_ignored(synthetic_db):
    # An export flag for a building id this AOI doesn't have must NOT count as drift
    # (the build aligns flags to real ids; a phantom id resolves to nothing).
    synthetic_db.write_export("alpha", {999: 2})  # 999 not in buildings
    assert rf.find_drifted_aois() == []


def test_reconcile_enqueues_rebuild_only_for_drifted(synthetic_db, monkeypatch):
    synthetic_db.write_export("alpha", {2: 2})  # drift on alpha
    enqueued = []

    class _Task:
        def delay(self, code):
            enqueued.append(code)

    monkeypatch.setattr(rf, "find_drifted_aois", lambda: ["alpha"])
    # Stub the lazily-imported tasks.request_rebuild.
    import scripts.pipeline.tasks as tasks_mod
    monkeypatch.setattr(tasks_mod, "request_rebuild", _Task())

    requested = rf.reconcile_on_startup()
    assert requested == ["alpha"]
    assert enqueued == ["alpha"]


def test_reconcile_never_raises_on_missing_db(tmp_path, monkeypatch):
    # No DB file at all → detection returns empty, reconcile is a clean no-op.
    monkeypatch.setattr(rf, "DB_PATH", tmp_path / "nonexistent.duckdb")
    monkeypatch.setattr(rf, "aois", type("M", (), {"REGISTRY": [_AOI("alpha")]}))
    assert rf.find_drifted_aois() == []
    assert rf.reconcile_on_startup() == []
