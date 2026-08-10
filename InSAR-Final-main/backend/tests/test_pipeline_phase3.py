"""Tests for Phase 3: opt-in beat schedule + the control-plane refresh API.

No broker/Redis needed: schedule building is pure, and the control API is
tested via FastAPI TestClient (enqueue paths are gated by auth before any
broker call, and we don't assert on actual task dispatch here).
"""
import os

import pytest
from fastapi.testclient import TestClient

from scripts.pipeline.schedule import build_beat_schedule
from scripts.pipeline.control_api import app

client = TestClient(app)


# ---- beat schedule (opt-in) ----------------------------------------------

def test_schedule_empty_by_default(monkeypatch):
    monkeypatch.delenv("INSAR_BEAT_ENABLED", raising=False)
    assert build_beat_schedule() == {}


def test_schedule_populates_when_enabled(monkeypatch):
    monkeypatch.setenv("INSAR_BEAT_ENABLED", "1")
    monkeypatch.delenv("INSAR_BEAT_AOIS", raising=False)
    sched = build_beat_schedule()
    assert len(sched) >= 1
    sample = next(iter(sched.values()))
    assert sample["task"] == "insar.refresh_aoi"


def test_schedule_respects_aoi_subset_and_cadence(monkeypatch):
    monkeypatch.setenv("INSAR_BEAT_ENABLED", "true")
    monkeypatch.setenv("INSAR_BEAT_AOIS", "huruma,mombasa")
    monkeypatch.setenv("INSAR_BEAT_DAYS", "6")
    sched = build_beat_schedule()
    assert set(sched) == {"refresh-huruma", "refresh-mombasa"}
    assert next(iter(sched.values()))["schedule"].days == 6


# ---- control API (fail-closed auth) --------------------------------------

def test_health_ok():
    assert client.get("/health").status_code == 200


def test_refresh_disabled_without_token(monkeypatch):
    monkeypatch.delenv("INSAR_ADMIN_TOKEN", raising=False)
    r = client.post("/admin/refresh", json={"aoi": "huruma"})
    assert r.status_code == 503   # disabled, NOT open


def test_refresh_rejects_wrong_token(monkeypatch):
    monkeypatch.setenv("INSAR_ADMIN_TOKEN", "sekret")
    r = client.post("/admin/refresh", json={"aoi": "huruma"},
                    headers={"X-Admin-Token": "wrong"})
    assert r.status_code == 401


def test_status_is_readable_without_token(monkeypatch):
    # Status of an unknown id returns a state (PENDING), no auth required.
    r = client.get("/admin/refresh/some-unknown-id")
    assert r.status_code == 200
    assert "state" in r.json()


# ---- request-rebuild endpoint (same fail-closed auth as refresh) ---------

def test_request_rebuild_disabled_without_token(monkeypatch):
    monkeypatch.delenv("INSAR_ADMIN_TOKEN", raising=False)
    r = client.post("/admin/request-rebuild", json={"aoi": "huruma"})
    assert r.status_code == 503   # disabled, NOT open


def test_request_rebuild_rejects_wrong_token(monkeypatch):
    monkeypatch.setenv("INSAR_ADMIN_TOKEN", "sekret")
    r = client.post("/admin/request-rebuild", json={"aoi": "huruma"},
                    headers={"X-Admin-Token": "wrong"})
    assert r.status_code == 401


def test_request_rebuild_enqueues_with_valid_token(monkeypatch):
    monkeypatch.setenv("INSAR_ADMIN_TOKEN", "sekret")
    from scripts.pipeline import tasks

    class _Res:
        id = "fake-task-id"

    monkeypatch.setattr(tasks.request_rebuild, "delay", lambda *a, **k: _Res())
    r = client.post("/admin/request-rebuild", json={"aoi": "huruma"},
                    headers={"X-Admin-Token": "sekret"})
    assert r.status_code == 200
    body = r.json()
    assert body["task_id"] == "fake-task-id"
    assert body["aoi"] == "huruma" and body["debounced"] is True
