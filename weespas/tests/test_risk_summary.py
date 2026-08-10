"""Admin risk-oversight aggregate (analytics_service.aggregate_risk_summary + the
staff-gated /analytics/risk/summary endpoint).

Properties under test:
  1. coverage mix counts active listings by verification_status;
  2. unsafe_listings counts active listings whose linked building's LATEST flag is
     UNSAFE/AUTH_UNSAFE — and a later CLEARED flag clears it (latest-wins);
  3. the endpoint is staff-gated (agent/user are 403).
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from PE.weespas.main import app
from PE.weespas.core.database import Base, get_db
from PE.weespas.models.user import User, UserRole, UserRoleRow
from PE.weespas.models.property import (
    Property, PropertyCategory, PropertyListingType,
    VERIFICATION_MONITORED, VERIFICATION_NOT_MONITORED,
)
from PE.weespas.models.insar_link import (
    BuildingLink, StructuralFlag, FLAG_UNSAFE, FLAG_CLEARED,
)
from PE.weespas.services.analytics_service import aggregate_risk_summary
from PE.weespas.services.auth_service import require_staff


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s, Session
    s.close()


def _listing(db, *, status, aoi=None, bid=None, active=True):
    cat = PropertyCategory(id=str(uuid.uuid4()), name=f"H-{uuid.uuid4().hex[:6]}",
                           slug=f"h-{uuid.uuid4().hex[:6]}")
    db.add(cat); db.flush()
    p = Property(
        id=str(uuid.uuid4()), title="t", price=1, currency="KES",
        listing_type=PropertyListingType.SALE, category_id=cat.id,
        verification_status=status, is_active=active,
    )
    db.add(p); db.flush()
    if aoi and bid is not None:
        db.add(BuildingLink(listing_id=p.id, aoi_code=aoi, insar_building_id=bid,
                            match_method="pip", match_confidence=1.0))
    db.commit()
    return p


def _flag(db, aoi, bid, state, *, at=None):
    # Explicit created_at so "latest wins" is unambiguous (real flags are days apart;
    # the test would otherwise collide at SQLite's second granularity).
    db.add(StructuralFlag(aoi_code=aoi, insar_building_id=bid, state=state,
                          source="engineer", created_at=at))
    db.commit()


def test_coverage_mix_counts_by_status(db):
    s, _ = db
    _listing(s, status=VERIFICATION_MONITORED, aoi="south_c", bid=1)
    _listing(s, status=VERIFICATION_MONITORED, aoi="south_c", bid=2)
    _listing(s, status=VERIFICATION_NOT_MONITORED)
    # inactive listing must NOT be counted
    _listing(s, status=VERIFICATION_MONITORED, aoi="south_c", bid=3, active=False)

    out = aggregate_risk_summary(s)
    assert out["monitored"] == 2
    assert out["not_monitored"] == 1
    assert out["coverage"]["monitored"] == 2


def test_unsafe_listings_counts_latest_unsafe(db):
    s, _ = db
    _listing(s, status=VERIFICATION_MONITORED, aoi="south_c", bid=10)
    _flag(s, "south_c", 10, FLAG_UNSAFE)
    assert aggregate_risk_summary(s)["unsafe_listings"] == 1


def test_later_cleared_flag_clears_the_count(db):
    s, _ = db
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _listing(s, status=VERIFICATION_MONITORED, aoi="south_c", bid=20)
    _flag(s, "south_c", 20, FLAG_UNSAFE, at=t0)
    _flag(s, "south_c", 20, FLAG_CLEARED, at=t0 + timedelta(days=3))  # newer → latest wins
    assert aggregate_risk_summary(s)["unsafe_listings"] == 0


# ── endpoint role gate ─────────────────────────────────────────────────────────

def _override_user(roles):
    u = User(id=str(uuid.uuid4()), name="t", email=f"{uuid.uuid4().hex}@e.co",
             phone=f"+2547{uuid.uuid4().int % 100000000:08d}",
             hashed_password="x", role=UserRole.USER)
    u._role_rows = [UserRoleRow(role=r) for r in roles]
    return u


def test_endpoint_requires_staff(db):
    s, Session = db

    def _odb():
        d = Session()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_db] = _odb
    client = TestClient(app)
    try:
        # A plain agent must be refused (require_staff = staff/admin only).
        from PE.weespas.services.auth_service import get_current_user
        app.dependency_overrides[get_current_user] = lambda: _override_user(["agent"])
        r = client.get("/api/v1/analytics/risk/summary")
        assert r.status_code == 403

        # Staff is allowed.
        app.dependency_overrides[get_current_user] = lambda: _override_user(["staff"])
        r2 = client.get("/api/v1/analytics/risk/summary")
        assert r2.status_code == 200
        assert "unsafe_listings" in r2.json()
    finally:
        app.dependency_overrides.clear()
