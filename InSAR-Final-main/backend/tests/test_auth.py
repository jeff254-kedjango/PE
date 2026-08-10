"""The data API must require a valid RS256 telemetry token.

This is the hole the UI login-gate did NOT close: /aoi/{code}/bundle and friends served the
entire risk dataset to any anonymous curl. These tests prove every data endpoint now demands
a correctly-signed, correctly-scoped, unexpired token, while /health stays open — and that
the gate is inert when no key is configured (dev/public fallback).
"""
from __future__ import annotations

import jwt  # PyJWT
import pytest

# Every protected data endpoint with a ready-to-call URL (a valid AOI from the seeded DB).
PROTECTED = [
    "/aois",
    "/aoi/huruma/bundle",
    "/buildings?aoi=huruma&minlon=36.8&minlat=-1.3&maxlon=37.0&maxlat=-1.1",
    "/buildings/at-date?aoi=huruma&minlon=36.8&minlat=-1.3&maxlon=37.0&maxlat=-1.1&obs_date=2024-01-01",
    "/risk-summary?aoi=huruma",
]


def _auth(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


@pytest.mark.parametrize("url", PROTECTED)
def test_no_token_is_rejected(auth_app, url):
    """Anonymous request (no Authorization header) → 401. The scraper hole is closed."""
    assert auth_app.get(url).status_code == 401


@pytest.mark.parametrize("url", PROTECTED)
def test_valid_token_is_accepted(auth_app, make_token, url):
    """A valid, scoped, unexpired RS256 token → 200 on every protected endpoint."""
    r = auth_app.get(url, headers=_auth(make_token()))
    assert r.status_code == 200, (url, r.status_code, r.text[:200])


def test_wrong_scope_is_rejected(auth_app, make_token):
    """A correctly-signed token of the wrong KIND (not telemetry-scoped) → 401."""
    tok = make_token(scope="some_other_scope")
    assert auth_app.get("/aois", headers=_auth(tok)).status_code == 401


def test_expired_token_is_rejected(auth_app, make_token):
    """An expired token → 401 (PyJWT exp validation)."""
    tok = make_token(exp_delta_min=-1)
    assert auth_app.get("/aois", headers=_auth(tok)).status_code == 401


def test_missing_exp_is_rejected(auth_app, make_token):
    """A token with no exp claim → 401 (we require exp, so an immortal token can't slip in)."""
    tok = make_token(include_exp=False)
    assert auth_app.get("/aois", headers=_auth(tok)).status_code == 401


def test_bad_signature_is_rejected(auth_app, make_token, rsa_keypair):
    """A token signed by a DIFFERENT RSA key (forgery) → 401."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_pem = other.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    tok = make_token(alg="RS256", key=other_pem)
    assert auth_app.get("/aois", headers=_auth(tok)).status_code == 401


def test_hs256_token_is_rejected(auth_app, make_token):
    """A plain HS256 token is refused: the data API verifies RS256 ONLY."""
    tok = make_token(alg="HS256", key="some-hmac-secret")
    assert auth_app.get("/aois", headers=_auth(tok)).status_code == 401


def test_alg_confusion_attack_is_rejected(auth_app, rsa_keypair):
    """The classic attack: hand-roll an HS256 token signed with the RSA PUBLIC PEM as the
    HMAC secret (PyJWT/jose refuse to *create* this, so we build it with raw hmac). The
    RS256-only verifier must reject it — it never treats any key as an HMAC secret."""
    import base64
    import hashlib
    import hmac
    import json

    _, public_pem = rsa_keypair

    def b64(b: bytes) -> bytes:
        return base64.urlsafe_b64encode(b).rstrip(b"=")

    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = b64(json.dumps({"sub": "evil", "scope": "insar_telemetry"}).encode())
    sig = b64(hmac.new(public_pem.encode(), header + b"." + payload, hashlib.sha256).digest())
    forged = (header + b"." + payload + b"." + sig).decode()

    assert auth_app.get("/aois", headers=_auth(forged)).status_code == 401


def test_health_is_open(auth_app):
    """/health stays public even with auth enabled — liveness probes need no token."""
    assert auth_app.get("/health").status_code == 200


@pytest.mark.parametrize("url", PROTECTED)
def test_inert_when_no_key_configured(public_app, url):
    """With no public key set, the data API is its old public self (200, no token) — the
    dependency deploys inert so the rollout can land before keys are provisioned."""
    assert public_app.get(url).status_code == 200


def test_prod_without_key_refuses_to_start(monkeypatch):
    """The fail-closed boot guard: INSAR_ENV=production + no public key ⇒ the app refuses to
    start, so a forgotten key env can never silently re-expose the dataset."""
    import importlib

    from fastapi.testclient import TestClient

    monkeypatch.setenv("INSAR_ENV", "production")
    monkeypatch.delenv("INSAR_JWT_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("INSAR_JWT_PUBLIC_KEY_PATH", raising=False)

    import app.config as cfg
    importlib.reload(cfg)
    cfg.jwt_public_key.cache_clear()
    import app.main as main
    importlib.reload(main)

    with pytest.raises(RuntimeError, match="Refusing to start"):
        with TestClient(main.app):
            pass


def test_prod_with_key_starts(rsa_keypair, monkeypatch):
    """Production WITH a key boots normally and enforces auth (401 without a token)."""
    import importlib

    from fastapi.testclient import TestClient

    _, public_pem = rsa_keypair
    monkeypatch.setenv("INSAR_ENV", "production")
    monkeypatch.setenv("INSAR_JWT_PUBLIC_KEY", public_pem)

    import app.config as cfg
    importlib.reload(cfg)
    cfg.jwt_public_key.cache_clear()
    import app.auth as auth
    importlib.reload(auth)
    import app.ratelimit as rl
    importlib.reload(rl)
    import app.main as main
    importlib.reload(main)

    with TestClient(main.app) as client:
        assert client.get("/aois").status_code == 401  # auth enforced
        # HSTS present in prod
        assert "max-age" in client.get("/health").headers.get("strict-transport-security", "")
