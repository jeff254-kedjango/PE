"""The Weespas → InSAR structural-flag export (sync seam).

Verifies the exporter writes the exact JSON shape the InSAR build's
`fetch_structural_flags()` consumes, that latest-flag-wins, and that disabling the
export (no dir) is a safe no-op. The InSAR-side loader has its own tests; here we
pin the producer + a producer→consumer round-trip parse (without importing InSAR).
"""
import json
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from PE.weespas.models.insar_link import (
    StructuralFlag, FLAG_CLEARED, FLAG_UNSAFE, FLAG_AUTH_UNSAFE,
)
from PE.weespas.services import structural_flag_export as exp


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    StructuralFlag.__table__.create(bind=engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _add(db, *, aoi, bid, state, source, observed=None):
    db.add(StructuralFlag(aoi_code=aoi, insar_building_id=bid, state=state,
                          source=source, observed_at=observed))
    db.commit()


def test_export_writes_loader_format(db, tmp_path):
    _add(db, aoi="huruma", bid=100000, state=FLAG_UNSAFE, source="engineer",
         observed=date(2026, 6, 20))
    path = exp.export_aoi(db, "huruma", export_dir=str(tmp_path), as_of=date(2026, 6, 22))
    assert path is not None and path.exists()
    doc = json.loads(path.read_text())
    assert doc["as_of"] == "2026-06-22"
    assert doc["flags"] == {
        "100000": {"state": 2, "observed_at": "2026-06-20", "source": "engineer"}
    }


def test_latest_flag_wins(db, tmp_path):
    # An older CLEARED then a newer UNSAFE on the same building → export the UNSAFE.
    _add(db, aoi="huruma", bid=7, state=FLAG_CLEARED, source="engineer",
         observed=date(2025, 1, 1))
    _add(db, aoi="huruma", bid=7, state=FLAG_UNSAFE, source="engineer",
         observed=date(2026, 1, 1))
    path = exp.export_aoi(db, "huruma", export_dir=str(tmp_path))
    flags = json.loads(path.read_text())["flags"]
    assert flags["7"]["state"] == FLAG_UNSAFE
    assert flags["7"]["observed_at"] == "2026-01-01"


def test_export_all_only_aois_with_flags(db, tmp_path):
    _add(db, aoi="huruma", bid=1, state=FLAG_UNSAFE, source="engineer")
    _add(db, aoi="mombasa", bid=2, state=FLAG_AUTH_UNSAFE, source="authority")
    paths = exp.export_all(db, export_dir=str(tmp_path))
    names = sorted(p.name for p in paths)
    assert names == ["huruma.json", "mombasa.json"]


def test_export_disabled_when_no_dir(db, monkeypatch):
    # No dir configured ⇒ no-op, returns None/[]. Force the setting empty so the test is
    # hermetic regardless of any INSAR_FLAGS_EXPORT_DIR in the ambient .env.
    monkeypatch.setattr(exp.settings, "insar_flags_export_dir", "")
    assert exp.export_aoi(db, "huruma", export_dir="") is None
    assert exp.export_all(db, export_dir="") == []


def test_export_is_atomic_overwrite(db, tmp_path):
    _add(db, aoi="huruma", bid=1, state=FLAG_UNSAFE, source="engineer")
    exp.export_aoi(db, "huruma", export_dir=str(tmp_path))
    # add another and re-export — file is replaced cleanly, no .tmp left behind
    _add(db, aoi="huruma", bid=2, state=FLAG_CLEARED, source="engineer")
    exp.export_aoi(db, "huruma", export_dir=str(tmp_path))
    flags = json.loads((tmp_path / "huruma.json").read_text())["flags"]
    assert set(flags) == {"1", "2"}
    assert not list(tmp_path.glob("*.tmp"))


# ---- trigger_rebuild: best-effort, fail-safe, never raises ---------------

def test_trigger_rebuild_disabled_without_url(monkeypatch):
    # No control-API URL ⇒ no-op (returns False), no network call attempted.
    monkeypatch.setattr(exp.settings, "insar_control_api_url", "")
    monkeypatch.setattr(exp.settings, "insar_admin_token", "tok")
    monkeypatch.setattr(exp.urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("must not call out when disabled"))
    assert exp.trigger_rebuild("huruma") is False


def test_trigger_rebuild_disabled_without_token(monkeypatch):
    monkeypatch.setattr(exp.settings, "insar_control_api_url", "http://insar:8001")
    monkeypatch.setattr(exp.settings, "insar_admin_token", "")
    monkeypatch.setattr(exp.urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("must not call out when disabled"))
    assert exp.trigger_rebuild("huruma") is False


def test_trigger_rebuild_posts_with_token(monkeypatch):
    monkeypatch.setattr(exp.settings, "insar_control_api_url", "http://insar:8001/")
    monkeypatch.setattr(exp.settings, "insar_admin_token", "sekret")
    seen = {}

    class _Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["token"] = req.headers.get("X-admin-token")
        seen["body"] = json.loads(req.data.decode())
        seen["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(exp.urllib.request, "urlopen", _fake_urlopen)
    assert exp.trigger_rebuild("huruma") is True
    # trailing slash on base is collapsed to a single join
    assert seen["url"] == "http://insar:8001/admin/request-rebuild"
    assert seen["method"] == "POST"
    assert seen["token"] == "sekret"
    assert seen["body"] == {"aoi": "huruma"}


def test_trigger_rebuild_swallows_network_error(monkeypatch):
    import urllib.error
    monkeypatch.setattr(exp.settings, "insar_control_api_url", "http://insar:8001")
    monkeypatch.setattr(exp.settings, "insar_admin_token", "sekret")

    def _boom(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(exp.urllib.request, "urlopen", _boom)
    # never raises — a recorded flag must not fail because InSAR is down
    assert exp.trigger_rebuild("huruma") is False


def test_trigger_rebuild_non_2xx_is_false(monkeypatch):
    monkeypatch.setattr(exp.settings, "insar_control_api_url", "http://insar:8001")
    monkeypatch.setattr(exp.settings, "insar_admin_token", "sekret")

    class _Resp:
        status = 503
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(exp.urllib.request, "urlopen", lambda req, timeout=None: _Resp())
    assert exp.trigger_rebuild("huruma") is False
