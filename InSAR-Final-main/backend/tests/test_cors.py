"""CORS is origin-locked: only configured origins may read the data cross-origin.

In prod the InSAR frontend calls the data API cross-origin with an Authorization header — a
non-simple request, so the browser preflights OPTIONS. These tests assert the preflight is
answered for an allow-listed origin (and permits the Authorization header) and is NOT
green-lit for an unknown origin.
"""
from __future__ import annotations


def _preflight(client, origin: str):
    return client.options(
        "/aois",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )


def test_allowed_origin_preflight_echoes_origin(auth_app):
    """An allow-listed origin's preflight returns the matching ACAO header."""
    r = _preflight(auth_app, "http://localhost:5174")
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5174"


def test_allowed_origin_permits_authorization_header(auth_app):
    """The preflight advertises that the Authorization header is allowed."""
    r = _preflight(auth_app, "http://localhost:5174")
    allowed = (r.headers.get("access-control-allow-headers") or "").lower()
    assert "authorization" in allowed


def test_unknown_origin_is_not_allowed(auth_app):
    """A non-allow-listed origin gets no ACAO header — the browser blocks the read."""
    r = _preflight(auth_app, "http://evil.example.com")
    assert r.headers.get("access-control-allow-origin") != "http://evil.example.com"
