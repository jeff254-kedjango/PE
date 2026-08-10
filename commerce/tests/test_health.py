"""Health endpoint smoke test (no auth required)."""
from fastapi.testclient import TestClient

from PE.commerce.main import app


def test_health_ok():
    resp = TestClient(app).get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body
