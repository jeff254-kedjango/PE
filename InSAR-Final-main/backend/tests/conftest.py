"""Shared fixtures for the InSAR read-app security tests.

These prove the data API is auth-gated WITHOUT needing a running Weespas — the test
generates its own throwaway RSA keypair, configures the app to verify with the public half,
and signs tokens with the private half. So the test owns both ends of the trust boundary and
never imports anything from the weespas/ tree.
"""
from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone

import jwt  # PyJWT
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@pytest.fixture(scope="session")
def rsa_keypair() -> tuple[str, str]:
    """A throwaway 2048-bit RSA keypair as (private_pem, public_pem) strings."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


@pytest.fixture
def make_token(rsa_keypair):
    """Factory signing an RS256 telemetry token with the test PRIVATE key.

    Defaults produce a valid, unexpired, correctly-scoped token; override kwargs to forge
    the negative cases (wrong scope, expired, different key, HS256, …).
    """
    private_pem, _ = rsa_keypair

    def _make(
        sub: str = "user-1",
        scope: str = "insar_telemetry",
        exp_delta_min: int = 5,
        alg: str = "RS256",
        key: str | None = None,
        include_exp: bool = True,
    ) -> str:
        payload: dict = {"sub": sub, "role": "user", "scope": scope}
        if include_exp:
            payload["exp"] = datetime.now(timezone.utc) + timedelta(minutes=exp_delta_min)
        signing_key = key if key is not None else (private_pem if alg == "RS256" else "some-hmac-secret")
        return jwt.encode(payload, signing_key, algorithm=alg)

    return _make


@pytest.fixture
def auth_app(rsa_keypair, monkeypatch):
    """Import the app with auth ENABLED (public key configured) and return a TestClient.

    Env is set BEFORE importing app.config so its module-level reads pick up the key; the
    config caches are cleared and modules reloaded so each test starts from a clean state.
    """
    from fastapi.testclient import TestClient

    _, public_pem = rsa_keypair
    monkeypatch.setenv("INSAR_JWT_PUBLIC_KEY", public_pem)
    monkeypatch.delenv("INSAR_JWT_PUBLIC_KEY_PATH", raising=False)
    monkeypatch.setenv("INSAR_ALLOWED_ORIGINS", "http://localhost:5174,http://localhost:3000")
    monkeypatch.delenv("REDIS_URL", raising=False)  # rate-limit inert unless a test opts in

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
        yield client


@pytest.fixture
def public_app(monkeypatch):
    """Import the app with auth DISABLED (no key) — proves the inert/dev fallback."""
    from fastapi.testclient import TestClient

    monkeypatch.delenv("INSAR_JWT_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("INSAR_JWT_PUBLIC_KEY_PATH", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

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
        yield client
