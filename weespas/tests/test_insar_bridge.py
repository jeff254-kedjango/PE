"""InSAR↔Weespas bridge: scoped-token security boundary + telemetry sink + scorer e2e.

The bridge lets the stateless InSAR SPA emit commercial-usage events (insar_building_view
/ insar_export) into Weespas's metering spine, authenticated by a SHORT-LIVED, narrowly
SCOPED token. The load-bearing properties under test:

  1. A telemetry-scoped token is REJECTED by get_current_user / get_current_user_optional,
     so a token leaked in an InSAR deep-link URL can never be replayed against money
     (/reveal) or PII (/policy/me) endpoints.
  2. POST /insar-telemetry/event accepts ONLY insar_building_view / insar_export and
     records them under the token's user_id (no DB user load needed).
  3. The old narrow /metering/event STILL refuses insar_* (anti-forgery intact).
  4. End-to-end: enough InSAR views across enough AOIs flips UserUsageProfile.is_metered —
     i.e. the bridge genuinely feeds the §8 company-detection line.
"""
import uuid
from datetime import datetime, timezone, timedelta

import pytest
from jose import jwt
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from PE.weespas.main import app
from PE.weespas.core.config import settings
from PE.weespas.core.database import Base, get_db
from PE.weespas.models.user import User, UserRole
from PE.weespas.models.metering import (
    MeteringEvent, UserUsageProfile,
    EVENT_INSAR_BUILDING_VIEW, EVENT_INSAR_EXPORT, EVENT_INSAR_BUNDLE_FETCH,
)
from PE.weespas.services import auth_service
from PE.weespas.services import policy_tasks as pt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _telemetry_token(user_id="user-1", role="user"):
    return auth_service.create_insar_telemetry_token(user_id, role)


def _expired_telemetry_token(user_id="user-1"):
    payload = {
        "sub": user_id, "role": "user",
        "scope": auth_service.INSAR_TELEMETRY_SCOPE,
        "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 1. Security boundary — the scoped token is inert outside the telemetry sink
# ---------------------------------------------------------------------------
def test_scoped_token_rejected_by_get_current_user():
    """A telemetry token must NOT authenticate /policy/me (PII) — 401, no leak."""
    with TestClient(app) as c:
        r = c.get("/api/v1/policy/me", headers=_bearer(_telemetry_token()))
    assert r.status_code == 401


def test_scoped_token_cannot_spend_money():
    """A telemetry token must NOT reach /reveal (spends M-Pesa) — 401."""
    with TestClient(app) as c:
        r = c.post("/api/v1/reveal/some-listing", headers=_bearer(_telemetry_token()))
    assert r.status_code == 401


def test_scoped_token_rejected_by_optional_auth_returns_none():
    """get_current_user_optional treats a scoped token as anonymous (None), so it
    can't silently personalize a public endpoint either."""
    from PE.weespas.services.auth_service import get_current_user_optional
    from fastapi.security import HTTPAuthorizationCredentials
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=_telemetry_token())
    assert get_current_user_optional(credentials=creds, db=None) is None


def test_normal_access_token_still_works_as_identity():
    """The reject guards key on scope EQUALITY, so ordinary (claim-less) tokens are
    unaffected — verified by decoding through the same dependency path."""
    from PE.weespas.services.auth_service import require_insar_telemetry_token
    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials
    normal = auth_service.create_access_token("user-1", "user")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=normal)
    # A normal token must be refused by the telemetry dep (wrong/!= scope).
    with pytest.raises(HTTPException) as ei:
        require_insar_telemetry_token(credentials=creds)
    assert ei.value.status_code == 401


# ---------------------------------------------------------------------------
# 1b. The access gate — GET /insar/verify (InSAR is free but login-required)
# ---------------------------------------------------------------------------
def test_verify_grants_with_a_valid_telemetry_token():
    """The InSAR SPA calls /insar/verify on load; a valid scoped token → 200 (map renders)."""
    with TestClient(app) as c:
        r = c.get("/api/v1/insar/verify", headers=_bearer(_telemetry_token()))
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_verify_denies_anonymous():
    """No token → 401, so InSAR bounces a direct/anonymous visitor to the login."""
    with TestClient(app) as c:
        r = c.get("/api/v1/insar/verify")
    assert r.status_code in (401, 403)  # no Authorization header


def test_verify_denies_expired_token():
    """An expired token can't render the map — 401."""
    with TestClient(app) as c:
        r = c.get("/api/v1/insar/verify", headers=_bearer(_expired_telemetry_token()))
    assert r.status_code == 401


def test_verify_denies_a_normal_access_token():
    """A normal (non-telemetry) access token is NOT accepted by the gate — it lacks the
    telemetry scope, so a forged-by-substitution attempt with a real login token fails."""
    normal = auth_service.create_access_token("user-1", "user")
    with TestClient(app) as c:
        r = c.get("/api/v1/insar/verify", headers=_bearer(normal))
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# 2. The telemetry sink — accepts insar_*, drops everything else, no DB load
# ---------------------------------------------------------------------------
@pytest.fixture()
def captured(monkeypatch):
    """Capture safe_delay dispatches from the telemetry router (no Celery/DB)."""
    calls = []
    import PE.weespas.routers.insar_telemetry as itr

    def _fake_safe_delay(fn, *args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(itr, "safe_delay", _fake_safe_delay)
    return calls


def test_telemetry_accepts_building_view(captured):
    with TestClient(app) as c:
        r = c.post(
            "/api/v1/insar-telemetry/event",
            headers=_bearer(_telemetry_token("user-42")),
            json={"action": EVENT_INSAR_BUILDING_VIEW, "building_id": 100123, "aoi_code": "huruma"},
        )
    assert r.status_code == 202 and r.json()["accepted"] is True
    assert len(captured) == 1
    args, kwargs = captured[0]
    assert args[0] == EVENT_INSAR_BUILDING_VIEW
    assert kwargs["user_id"] == "user-42"          # from the signed sub, no DB load
    assert kwargs["session_id"] is None            # cross-origin → no session by design
    assert kwargs["target_ref"] == "100123"
    assert kwargs["aoi_code"] == "huruma"


def test_telemetry_export_carries_count(captured):
    with TestClient(app) as c:
        r = c.post(
            "/api/v1/insar-telemetry/event",
            headers=_bearer(_telemetry_token()),
            json={"action": EVENT_INSAR_EXPORT, "aoi_code": "kilimani", "count": 812},
        )
    assert r.status_code == 202
    _, kwargs = captured[0]
    assert kwargs["meta"] == "812"


def test_telemetry_accepts_bundle_fetch(captured):
    """The server-side access signal: a bundle pull reported by the InSAR data API is
    accepted and recorded with its aoi_code (so it feeds the breadth signal)."""
    with TestClient(app) as c:
        r = c.post(
            "/api/v1/insar-telemetry/event",
            headers=_bearer(_telemetry_token("user-77")),
            json={"action": EVENT_INSAR_BUNDLE_FETCH, "aoi_code": "south_c"},
        )
    assert r.status_code == 202 and r.json()["accepted"] is True
    args, kwargs = captured[0]
    assert args[0] == EVENT_INSAR_BUNDLE_FETCH
    assert kwargs["user_id"] == "user-77"
    assert kwargs["aoi_code"] == "south_c"


def test_telemetry_drops_forged_money_action(captured):
    """Even with a valid telemetry token, a non-InSAR action (e.g. checkout_paid) is
    dropped — the scoped token can ONLY emit the two InSAR signals."""
    with TestClient(app) as c:
        r = c.post(
            "/api/v1/insar-telemetry/event",
            headers=_bearer(_telemetry_token()),
            json={"action": "checkout_paid", "aoi_code": "huruma"},
        )
    assert r.status_code == 202 and r.json()["accepted"] is False
    assert captured == []                          # nothing dispatched


def test_telemetry_requires_a_token():
    with TestClient(app) as c:
        r = c.post("/api/v1/insar-telemetry/event",
                   json={"action": EVENT_INSAR_BUILDING_VIEW})
    assert r.status_code in (401, 403)             # no/!= scoped token


def test_telemetry_rejects_expired_token(captured):
    with TestClient(app) as c:
        r = c.post("/api/v1/insar-telemetry/event",
                   headers=_bearer(_expired_telemetry_token()),
                   json={"action": EVENT_INSAR_BUILDING_VIEW})
    assert r.status_code == 401
    assert captured == []


# ---------------------------------------------------------------------------
# 3. Anti-forgery on the OLD narrow endpoint stays intact
# ---------------------------------------------------------------------------
def test_old_metering_endpoint_still_refuses_insar_actions():
    """The browser-facing /metering/event must keep refusing insar_* (anti-forgery)
    — only the scoped telemetry sink may accept them."""
    with TestClient(app) as c:
        r = c.post("/api/v1/metering/event",
                   json={"action": EVENT_INSAR_BUILDING_VIEW})
    assert r.status_code == 202 and r.json()["accepted"] is False


# ---------------------------------------------------------------------------
# 4. End-to-end: the bridge actually unblocks the §8 metered verdict
# ---------------------------------------------------------------------------
@pytest.fixture()
def sm(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine)
    monkeypatch.setattr(pt, "SessionLocal", maker)
    return maker


def test_insar_views_flip_user_to_metered(sm):
    """Saturating InSAR views across all AOIs + exports → is_metered=1. This proves
    the events the bridge records genuinely drive the company-detection scorer."""
    db = sm()
    u = User(name="bank", email=f"{uuid.uuid4()}@gmail.com",
             phone=f"07{uuid.uuid4().int % 10**8:08d}", hashed_password="x", role=UserRole.USER)
    db.add(u); db.commit(); db.refresh(u)
    uid = u.id
    now = datetime.now(timezone.utc)
    for aoi in ("huruma", "kilimani", "kileleshwa", "south_c"):
        for _ in range(settings.company_volume_saturation):
            db.add(MeteringEvent(action=EVENT_INSAR_BUILDING_VIEW, user_id=uid,
                                 aoi_code=aoi, created_at=now))
    for _ in range(settings.company_export_saturation):
        db.add(MeteringEvent(action=EVENT_INSAR_EXPORT, user_id=uid,
                             aoi_code="huruma", created_at=now))
    db.commit(); db.close()

    res = pt.recompute_usage_profiles()
    assert res["metered"] == 1

    db2 = sm()
    p = db2.get(UserUsageProfile, uid)
    assert p is not None and p.is_metered == 1
    assert p.breadth >= 4
    db2.close()


def test_server_side_bundle_fetches_alone_flip_user_to_metered(sm):
    """A SCRAPER that only pulls bundles directly (no frontend clicks at all) is still
    caught: insar_bundle_fetch feeds volume + breadth, so saturating pulls across AOIs
    flips is_metered=1 — closing the 'curl bypasses the funnel' gap."""
    db = sm()
    u = User(name="scraper", email=f"{uuid.uuid4()}@gmail.com",
             phone=f"07{uuid.uuid4().int % 10**8:08d}", hashed_password="x", role=UserRole.USER)
    db.add(u); db.commit(); db.refresh(u)
    uid = u.id
    now = datetime.now(timezone.utc)
    # ONLY bundle fetches — no building_view, no export. Saturate volume across enough AOIs.
    for aoi in ("huruma", "kilimani", "kileleshwa", "south_c", "mombasa"):
        for _ in range(settings.company_volume_saturation):
            db.add(MeteringEvent(action=EVENT_INSAR_BUNDLE_FETCH, user_id=uid,
                                 aoi_code=aoi, created_at=now))
    db.commit(); db.close()

    res = pt.recompute_usage_profiles()
    assert res["metered"] == 1

    db2 = sm()
    p = db2.get(UserUsageProfile, uid)
    assert p is not None and p.is_metered == 1
    assert p.breadth >= settings.company_breadth_saturation
    db2.close()


# ---------------------------------------------------------------------------
# 5. RS256 cutover — minter signs RS256, verifiers accept BOTH algs (no lockout)
# ---------------------------------------------------------------------------
@pytest.fixture()
def rs256_enabled(monkeypatch):
    """Provision an RSA keypair and point settings at it so the minter signs RS256 and the
    dual-alg verifier checks RS256 against the public key. Mirrors a key-provisioned prod."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa as _rsa

    key = _rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    pub = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    # Patch the cached PEM properties directly (avoids writing temp files + cache busting).
    monkeypatch.setattr(type(settings), "insar_jwt_private_key",
                        property(lambda self: priv))
    monkeypatch.setattr(type(settings), "insar_jwt_public_key",
                        property(lambda self: pub))
    return priv, pub


def test_minter_signs_rs256_when_key_present(rs256_enabled):
    """With a private key provisioned, the telemetry token is RS256, not HS256."""
    tok = auth_service.create_insar_telemetry_token("user-rs", "user")
    assert jwt.get_unverified_header(tok)["alg"] == "RS256"


def test_rs256_token_accepted_by_verify_and_sink(rs256_enabled):
    """An RS256 telemetry token works on /insar/verify (200) and the telemetry sink (202)."""
    tok = auth_service.create_insar_telemetry_token("user-rs", "user")
    with TestClient(app) as c:
        assert c.get("/api/v1/insar/verify", headers=_bearer(tok)).status_code == 200
        r = c.post("/api/v1/insar-telemetry/event",
                   headers=_bearer(tok),
                   json={"action": "insar_building_view", "building_id": 1, "aoi_code": "huruma"})
    assert r.status_code == 202


def test_rs256_telemetry_token_still_cannot_reach_money_or_pii(rs256_enabled):
    """The scope guard is alg-agnostic: an RS256 telemetry token is STILL rejected by
    /policy/me (PII) and /reveal (money) — moving to RS256 didn't widen its powers."""
    tok = auth_service.create_insar_telemetry_token("user-rs", "user")
    with TestClient(app) as c:
        assert c.get("/api/v1/policy/me", headers=_bearer(tok)).status_code == 401
        assert c.post("/api/v1/reveal/some-listing", headers=_bearer(tok)).status_code == 401


def test_legacy_hs256_token_still_accepted_during_overlap(rs256_enabled):
    """A still-valid HS256 telemetry token (minted before the cutover) is ACCEPTED while
    RS256 is enabled — the dual-alg window prevents an in-flight-token lockout."""
    legacy = jwt.encode(
        {"sub": "u-legacy", "role": "user", "scope": auth_service.INSAR_TELEMETRY_SCOPE},
        settings.secret_key, algorithm="HS256",
    )
    with TestClient(app) as c:
        assert c.get("/api/v1/insar/verify", headers=_bearer(legacy)).status_code == 200


def test_require_rs256_flag_rejects_legacy_hs256(rs256_enabled, monkeypatch):
    """Post-cutover cleanup: with insar_telemetry_require_rs256=True, an HS256 telemetry
    token is rejected (telemetry path pinned to RS256) while a fresh RS256 token still
    works — and this does NOT affect access tokens (they keep using HS256 elsewhere)."""
    monkeypatch.setattr(settings, "insar_telemetry_require_rs256", True)
    rs256_tok = auth_service.create_insar_telemetry_token("user-rs", "user")
    legacy_hs = jwt.encode(
        {"sub": "u-legacy", "role": "user", "scope": auth_service.INSAR_TELEMETRY_SCOPE},
        settings.secret_key, algorithm="HS256",
    )
    with TestClient(app) as c:
        assert c.get("/api/v1/insar/verify", headers=_bearer(rs256_tok)).status_code == 200
        assert c.get("/api/v1/insar/verify", headers=_bearer(legacy_hs)).status_code == 401


def test_alg_confusion_rejected_on_weespas_side(rs256_enabled):
    """An HS256 token hand-signed with the RSA public PEM as the HMAC secret is rejected —
    Weespas only ever HMAC-verifies against secret_key, never the public key."""
    import base64, hashlib, hmac, json
    _, pub = rs256_enabled

    def b64(b): return base64.urlsafe_b64encode(b).rstrip(b"=")
    h = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    p = b64(json.dumps({"sub": "evil", "scope": auth_service.INSAR_TELEMETRY_SCOPE}).encode())
    sig = b64(hmac.new(pub.encode(), h + b"." + p, hashlib.sha256).digest())
    forged = (h + b"." + p + b"." + sig).decode()
    with TestClient(app) as c:
        assert c.get("/api/v1/insar/verify", headers=_bearer(forged)).status_code == 401
