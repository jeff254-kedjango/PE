"""Trust-weighted featured listings (the free 'advertisement' surface).

Covers the four moving parts of the trust-aware featured system:
  * services.ranking.trust_signal — the safety/anti-scam relevance score
  * PropertyService.get_featured_properties — geo path (certified outranks equidistant
    plain) + no-geo path (trust-then-recency, expired/non-featured excluded)
  * feeds.expire_featured — the housekeeping beat job
  * GET /admin/featured + POST /admin/properties/{id}/feature — the admin panel API
  * personalization._featured_boost — trust-graded feed boost

Throwaway in-memory SQLite; no Redis/broker (safe_delay falls back inline, and the
admin endpoint's cache fanout is monkeypatched to a no-op).
"""
import uuid
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from PE.weespas.core.database import Base, get_db
from PE.weespas.models.user import User, UserRole
from PE.weespas.models.property import (
    Property, Agent, Address, PropertyCategory,
    PropertyListingType as ModelListingType,
)
from PE.weespas.services.ranking import trust_signal
from PE.weespas.services.property_service import PropertyService
from PE.weespas.services import personalization


# --------------------------------------------------------------------------- #
#  fixtures + factories
# --------------------------------------------------------------------------- #
@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _category(db):
    cat = PropertyCategory(name="Apartment", slug="apartment")
    db.add(cat); db.commit(); db.refresh(cat)
    return cat


def _agent(db, *, verified=False):
    a = Agent(agent_name="A", agent_phone_number=f"07{uuid.uuid4().int % 10**8:08d}",
              is_verified=verified)
    db.add(a); db.commit(); db.refresh(a)
    return a


def _listing(db, cat, *, featured=True, certified=False, verified_agent=False,
             lat=-1.29, lon=36.82, created=None, expires="unset", views=0,
             active=True):
    """Create a Property (+ Address) with the trust knobs we test on."""
    agent = _agent(db, verified=verified_agent)
    prop = Property(
        title="T", price=1000.0, listing_type=ModelListingType.RENT, category_id=cat.id,
        agent_id=agent.id, is_featured=featured, is_active=active,
        is_engineer_certified=certified, view_count=views,
    )
    if created is not None:
        prop.created_at = created
    if expires != "unset":
        prop.featured_expires_at = expires
    db.add(prop); db.commit(); db.refresh(prop)
    addr = Address(property_id=prop.id, location_name="Kilimani", latitude=lat, longitude=lon)
    db.add(addr); db.commit()
    db.refresh(prop)
    return prop


# --------------------------------------------------------------------------- #
#  trust_signal unit
# --------------------------------------------------------------------------- #
def test_trust_signal_weights(db):
    cat = _category(db)
    cert = _listing(db, cat, certified=True, verified_agent=True)
    plain = _listing(db, cat, certified=False, verified_agent=False)
    cert_only = _listing(db, cat, certified=True, verified_agent=False)

    assert trust_signal(plain) == 0.0
    assert trust_signal(cert_only) == pytest.approx(0.55)
    assert trust_signal(cert) == pytest.approx(0.90)
    assert trust_signal(cert, monitored_ids={cert.id}) == pytest.approx(1.0)
    # capped at 1.0 even if every signal present
    assert trust_signal(cert, monitored_ids={cert.id}) <= 1.0


# --------------------------------------------------------------------------- #
#  geo path: trust breaks ties between equidistant featured listings
# --------------------------------------------------------------------------- #
def test_geo_path_certified_outranks_equidistant_plain(db):
    cat = _category(db)
    # Same coordinates → identical proximity; trust must decide order.
    plain = _listing(db, cat, certified=False, lat=-1.290, lon=36.820)
    cert = _listing(db, cat, certified=True, verified_agent=True, lat=-1.290, lon=36.820)

    out = PropertyService.get_featured_properties(db, limit=10, latitude=-1.290, longitude=36.820)
    ids = [r.id for r in out]
    assert ids.index(cert.id) < ids.index(plain.id)


def test_geo_never_empty_falls_back_to_nationwide(db):
    """"Search near me" must not empty the carousel: when NOTHING featured is in
    radius, the geo path tops up with nationwide featured (distance omitted)."""
    cat = _category(db)
    # Two featured listings, both far from the search point (Mombasa-ish), so the
    # 25km bounding box around Nairobi catches neither.
    far_a = _listing(db, cat, certified=True, lat=-4.043, lon=39.668)
    far_b = _listing(db, cat, certified=False, lat=-4.050, lon=39.670)

    out = PropertyService.get_featured_properties(
        db, limit=10, latitude=-1.290, longitude=36.820, radius_km=25.0,
    )
    ids = {r.id for r in out}
    assert ids == {far_a.id, far_b.id}                      # not empty
    # Topped-up (out-of-radius) fillers carry no distance.
    assert all(r.distance is None for r in out)


def test_geo_in_radius_first_then_fallback_fills(db):
    """In-radius featured lead (with distance); the remainder is filled from
    nationwide so the carousel reaches `limit` without duplicates."""
    cat = _category(db)
    near = _listing(db, cat, certified=True, lat=-1.2905, lon=36.8205)   # ~metres away
    far = _listing(db, cat, certified=True, lat=-4.043, lon=39.668)      # out of radius

    out = PropertyService.get_featured_properties(
        db, limit=10, latitude=-1.290, longitude=36.820, radius_km=25.0,
    )
    ids = [r.id for r in out]
    assert ids[0] == near.id                                # nearest in-radius leads
    assert set(ids) == {near.id, far.id}                    # filler added, no dupes
    assert len(ids) == len(set(ids))
    near_row = next(r for r in out if r.id == near.id)
    far_row = next(r for r in out if r.id == far.id)
    assert near_row.distance is not None                    # in-radius → has distance
    assert far_row.distance is None                         # fallback → no distance


# --------------------------------------------------------------------------- #
#  no-geo path: trust-then-recency + exclusion of expired/non-featured
# --------------------------------------------------------------------------- #
def test_no_geo_trust_then_recency(db):
    cat = _category(db)
    old = datetime(2025, 1, 1, tzinfo=timezone.utc)
    new = datetime(2026, 6, 1, tzinfo=timezone.utc)
    older_certified = _listing(db, cat, certified=True, created=old)
    newer_plain = _listing(db, cat, certified=False, created=new)

    out = PropertyService.get_featured_properties(db, limit=10)
    ids = [r.id for r in out]
    # Trust beats recency: the older-but-certified listing leads.
    assert ids.index(older_certified.id) < ids.index(newer_plain.id)


def test_expired_and_nonfeatured_excluded(db):
    cat = _category(db)
    past = datetime.now(timezone.utc) - timedelta(days=1)
    active = _listing(db, cat, featured=True, expires=None)              # no expiry → shows
    expired = _listing(db, cat, featured=True, expires=past)            # past → hidden
    not_featured = _listing(db, cat, featured=False, expires=None)      # not featured → hidden

    ids = {r.id for r in PropertyService.get_featured_properties(db, limit=10)}
    assert active.id in ids
    assert expired.id not in ids
    assert not_featured.id not in ids


# --------------------------------------------------------------------------- #
#  expire_featured beat job
# --------------------------------------------------------------------------- #
def test_expire_featured_flips_only_past_expiry(db, monkeypatch):
    from PE.weespas.services import property_tasks as pt
    # The task opens (and closes) its own session; give it a sessionmaker on the SAME
    # in-memory engine so it sees our rows without detaching `db`'s instances.
    SM = sessionmaker(bind=db.get_bind())
    monkeypatch.setattr(pt, "SessionLocal", SM)
    monkeypatch.setattr(pt.redis_client, "delete", lambda *a, **k: 0)

    cat = _category(db)
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    future = datetime.now(timezone.utc) + timedelta(days=5)
    expired_id = _listing(db, cat, featured=True, expires=past).id
    permanent_id = _listing(db, cat, featured=True, expires=None).id
    future_id = _listing(db, cat, featured=True, expires=future).id

    res = pt.expire_featured()
    assert res["expired"] == 1
    check = SM()
    try:
        assert check.get(Property, expired_id).is_featured is False
        assert check.get(Property, permanent_id).is_featured is True
        assert check.get(Property, future_id).is_featured is True
    finally:
        check.close()


# --------------------------------------------------------------------------- #
#  admin endpoints
# --------------------------------------------------------------------------- #
@pytest.fixture()
def admin_client(db, monkeypatch):
    # Don't fire the real cache fanout (no Redis/broker in tests).
    import PE.weespas.routers.properties as props_router
    monkeypatch.setattr(props_router, "_dispatch_property_write_fanout", lambda *a, **k: None)

    from PE.weespas.main import app
    from PE.weespas.services.auth_service import require_admin
    admin = User(name="admin", email=f"{uuid.uuid4()}@e.com", phone="0700000000",
                 hashed_password="x", role=UserRole.ADMIN)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_admin] = lambda: admin
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(require_admin, None)


def test_admin_feature_with_duration(admin_client, db):
    cat = _category(db)
    prop = _listing(db, cat, featured=False, expires=None)
    r = admin_client.post(f"/api/v1/admin/properties/{prop.id}/feature",
                          json={"is_featured": True, "duration_days": 7})
    assert r.status_code == 200
    body = r.json()
    assert body["is_featured"] is True
    assert body["featured_expires_at"] is not None
    db.expire_all()
    saved = db.get(Property, prop.id)
    assert saved.is_featured is True
    # ~7 days out. SQLite drops tzinfo on round-trip, so coerce to aware for the math.
    exp = saved.featured_expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    delta = exp - datetime.now(timezone.utc)
    assert timedelta(days=6) < delta < timedelta(days=8)


def test_admin_unfeature_clears_expiry(admin_client, db):
    cat = _category(db)
    future = datetime.now(timezone.utc) + timedelta(days=5)
    prop = _listing(db, cat, featured=True, expires=future)
    r = admin_client.post(f"/api/v1/admin/properties/{prop.id}/feature",
                          json={"is_featured": False})
    assert r.status_code == 200
    db.expire_all()
    saved = db.get(Property, prop.id)
    assert saved.is_featured is False and saved.featured_expires_at is None


def test_admin_feature_404_unknown(admin_client):
    r = admin_client.post("/api/v1/admin/properties/nope/feature",
                          json={"is_featured": True})
    assert r.status_code == 404


def test_admin_list_featured_only_active(admin_client, db):
    cat = _category(db)
    past = datetime.now(timezone.utc) - timedelta(days=1)
    active = _listing(db, cat, featured=True, expires=None)
    _listing(db, cat, featured=True, expires=past)        # expired → excluded
    _listing(db, cat, featured=False, expires=None)       # not featured → excluded
    r = admin_client.get("/api/v1/admin/featured")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == active.id


def test_admin_featured_requires_admin(db, monkeypatch):
    """Without the admin override, a plain user is rejected (require_admin guards it)."""
    import PE.weespas.routers.properties as props_router
    monkeypatch.setattr(props_router, "_dispatch_property_write_fanout", lambda *a, **k: None)
    from PE.weespas.main import app
    from PE.weespas.services.auth_service import get_current_user
    plain = User(name="u", email=f"{uuid.uuid4()}@e.com", phone="0711111111",
                 hashed_password="x", role=UserRole.USER)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: plain
    try:
        c = TestClient(app)
        assert c.get("/api/v1/admin/featured").status_code == 403
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


# --------------------------------------------------------------------------- #
#  personalization _featured_boost is trust-graded
# --------------------------------------------------------------------------- #
def test_featured_boost_trust_graded(db):
    cat = _category(db)
    now = datetime.now(timezone.utc)
    plain = _listing(db, cat, featured=True, certified=False, verified_agent=False, expires=None)
    cert = _listing(db, cat, featured=True, certified=True, verified_agent=True, expires=None)
    not_feat = _listing(db, cat, featured=False, expires=None)

    b_plain = personalization._featured_boost(plain, now)
    b_cert = personalization._featured_boost(cert, now)
    assert personalization._featured_boost(not_feat, now) == 0.0
    assert 0.0 < b_plain < b_cert <= 1.0
    assert b_plain == pytest.approx(0.6)
    assert b_cert == pytest.approx(1.0)
