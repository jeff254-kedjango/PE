"""Tests for the Celery build-pipeline orchestration (scripts/pipeline).

These run with NO broker, NO Redis, and NO heavy pipeline tools: the Celery app
is forced into eager mode and the heavy task bodies are stubbed. We test the
orchestration contract only — task registration, chain order, the subprocess
wrapper's exit-code handling, and the OpenSARLab gate — never the InSAR science
(that lives in the wrapped scripts and is covered elsewhere).
"""
import pytest

from scripts.pipeline import tasks
from scripts.pipeline.celery_app import app


@pytest.fixture
def eager():
    app.conf.task_always_eager = True
    app.conf.task_eager_propagates = True
    yield
    app.conf.task_always_eager = False
    app.conf.task_eager_propagates = False


def test_all_tasks_registered():
    names = {t for t in app.tasks if t.startswith("insar.")}
    assert {
        "insar.hyp3_submit_watch", "insar.gacos_submit", "insar.clip",
        "insar.reproject", "insar.mintpy_gate", "insar.join",
        "insar.refresh_aoi", "insar.rebuild_from_sbas",
        "insar.request_rebuild", "insar._debounced_rebuild", "insar.rebuild_aoi",
    } <= names


def test_run_module_raises_on_nonzero_exit():
    # join_insar with a bogus AOI exits non-zero → StageError with stage context.
    with pytest.raises(tasks.StageError) as exc:
        tasks._run_module("scripts.join_insar", ["--aoi", "definitely_not_an_aoi"],
                          stage="probe")
    assert "probe" in str(exc.value)


def test_mintpy_gate_blocks_without_sbas_outputs():
    with pytest.raises(tasks.AwaitingOpenSARLab):
        tasks.mintpy_gate.run("no_such_aoi_xyz", "ASCENDING/57")


def test_mintpy_gate_passes_when_velocity_present(tmp_path, monkeypatch):
    run_dir = tmp_path / "aoiX_ASCENDING_57"
    run_dir.mkdir()
    (run_dir / "velocity.h5").write_bytes(b"\x00")
    monkeypatch.setattr(tasks, "MINTPY_DIR", tmp_path)
    msg = tasks.mintpy_gate.run("aoiX", "ASCENDING/57")
    assert "present" in msg


def test_refresh_chain_runs_stages_in_order(eager, monkeypatch):
    order = []
    for name in ("hyp3_submit_watch", "clip", "reproject", "mintpy_gate", "join"):
        t = getattr(tasks, name)
        monkeypatch.setattr(
            t, "run",
            (lambda nm: (lambda *a, **k: order.append(nm) or nm))(name),
        )
    tasks.refresh_aoi.run("huruma", "ASCENDING/57", tracks="both")
    assert order == ["hyp3_submit_watch", "clip", "reproject", "mintpy_gate", "join"]


def test_rebuild_from_sbas_is_gate_then_join(eager, monkeypatch):
    order = []
    for name in ("mintpy_gate", "join"):
        t = getattr(tasks, name)
        monkeypatch.setattr(
            t, "run",
            (lambda nm: (lambda *a, **k: order.append(nm) or nm))(name),
        )
    tasks.rebuild_from_sbas.run("huruma", "ASCENDING/57")
    assert order == ["mintpy_gate", "join"]


# --------------------------------------------------------------------------
# Flag-triggered rebuild: debounce token logic + provenance-aware dispatch.
# A tiny in-memory fake Redis covers the incr/get token contract without a
# broker; the actual rebuild bodies are stubbed so we assert only orchestration.
# --------------------------------------------------------------------------

class _FakeRedis:
    """Minimal incr/get over an int store — enough for the debounce token."""
    def __init__(self):
        self.store = {}

    def incr(self, key):
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    def get(self, key):
        v = self.store.get(key)
        # real redis returns bytes; mirror that so int() coercion is exercised
        return None if v is None else str(v).encode()


def test_request_rebuild_runs_now_without_redis(monkeypatch):
    monkeypatch.setattr(tasks, "_redis_client", lambda: None)
    called = []
    monkeypatch.setattr(tasks, "rebuild_aoi", lambda aoi, track="ASCENDING/57": called.append(aoi) or "ran")
    out = tasks.request_rebuild.run("huruma")
    assert out == "ran" and called == ["huruma"]


def test_request_rebuild_schedules_debounced_with_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(tasks, "_redis_client", lambda: fake)
    scheduled = []
    monkeypatch.setattr(
        tasks._debounced_rebuild, "apply_async",
        lambda args, countdown: scheduled.append((args, countdown)),
    )
    out = tasks.request_rebuild.run("huruma")
    assert "scheduled" in out
    # one task scheduled with the freshly-incremented token (1) for this AOI
    (args, countdown), = scheduled
    assert args[0] == "huruma" and args[1] == 1
    assert countdown == tasks.REBUILD_DEBOUNCE_SECONDS


def test_debounce_coalesces_burst_to_latest_token(monkeypatch):
    """Three rapid requests bump the token to 3; only the token-3 deferred run
    actually rebuilds — the stale token-1/2 runs are no-ops."""
    fake = _FakeRedis()
    monkeypatch.setattr(tasks, "_redis_client", lambda: fake)
    scheduled = []
    monkeypatch.setattr(
        tasks._debounced_rebuild, "apply_async",
        lambda args, countdown: scheduled.append(args),
    )
    for _ in range(3):
        tasks.request_rebuild.run("huruma")
    tokens = [a[1] for a in scheduled]
    assert tokens == [1, 2, 3]

    ran = []
    monkeypatch.setattr(tasks, "rebuild_aoi", lambda aoi, track="ASCENDING/57": ran.append((aoi, "ran")) or "ran")
    # stale tokens skip
    assert "superseded" in tasks._debounced_rebuild.run("huruma", 1)
    assert "superseded" in tasks._debounced_rebuild.run("huruma", 2)
    assert ran == []
    # latest token runs exactly once
    tasks._debounced_rebuild.run("huruma", 3)
    assert ran == [("huruma", "ran")]


def test_debounced_rebuild_runs_when_no_redis(monkeypatch):
    # If Redis vanished between schedule and fire, fail safe to running the rebuild.
    monkeypatch.setattr(tasks, "_redis_client", lambda: None)
    ran = []
    monkeypatch.setattr(tasks, "rebuild_aoi", lambda aoi, track="ASCENDING/57": ran.append(aoi) or "ran")
    tasks._debounced_rebuild.run("huruma", 7)
    assert ran == ["huruma"]


def test_rebuild_aoi_insar_provenance_rejoins(eager, monkeypatch):
    """An 'insar' AOI must re-JOIN (gate→join), never re-seed (the seeder skips it)."""
    import scripts.provenance as provenance
    monkeypatch.setattr(provenance, "get_provenance", lambda aoi: "insar")
    order = []
    for name in ("mintpy_gate", "join"):
        t = getattr(tasks, name)
        monkeypatch.setattr(
            t, "run",
            (lambda nm: (lambda *a, **k: order.append(nm) or nm))(name),
        )
    # _run_module must NOT be called for an insar AOI
    monkeypatch.setattr(tasks, "_run_module",
                        lambda *a, **k: pytest.fail("insar AOI must not re-seed"))
    msg = tasks.rebuild_aoi.run("huruma", "ASCENDING/57")
    assert "re-join" in msg and order == ["mintpy_gate", "join"]


def test_rebuild_aoi_synthetic_provenance_reseeds(monkeypatch):
    """A synthetic/partial AOI must re-SEED via scripts.reseed_one."""
    import scripts.provenance as provenance
    monkeypatch.setattr(provenance, "get_provenance", lambda aoi: "synthetic")
    seen = {}
    monkeypatch.setattr(tasks, "_run_module",
                        lambda module, args, **k: seen.update(module=module, args=args) or "seeded")
    out = tasks.rebuild_aoi.run("kilimani", "ASCENDING/57")
    assert out == "seeded"
    assert seen["module"] == "scripts.reseed_one"
    assert seen["args"] == ["--aoi", "kilimani"]
