"""Smoke tests: the app imports, boots, and serves liveness routes.

These exercise the full FastAPI app object (routers, middleware) via
TestClient without touching the real database — the session middleware
skips /health and swallows analytics-write failures on /.
"""

from fastapi.testclient import TestClient

from PE.weespas.main import app

client = TestClient(app)


def test_health_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


def test_root_metadata():
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Weespas API"
