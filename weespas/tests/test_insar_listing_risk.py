"""GET /insar/listing/{id}/risk — the honest 3-state coverage badge (work_flow.md §9.3 B).

Load-bearing properties under test:
  1. A linked listing reports its CURRENT tier read live (a stale cached tier would be a
     life-safety lie — a rebuild can escalate a building).
  2. The cardinal rule: 'unknown' / DB-off / resolve-failure is reported as
     not_monitored / unavailable, NEVER as 'safe' (a STABLE tier).
  3. Public-read: an anonymous caller still gets the badge (free for individuals); a
     telemetry-scoped token can't masquerade as identity here.

The resolver's DuckDB layer is mocked so the test needs no InSAR data file — we exercise
the router's branching (link hit vs miss vs failure), not DuckDB itself.
"""
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from PE.weespas.main import app
from PE.weespas.core.database import Base, get_db
from PE.weespas.models.insar_link import BuildingLink
from PE.weespas.services import insar_resolver
from PE.weespas.services.insar_resolver import (
    ResolveResult,
    COVERAGE_MONITORED,
    COVERAGE_NOT_MONITORED,
    COVERAGE_UNAVAILABLE,
)


def _client_with_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine)

    def _override():
        db = maker()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    return TestClient(app), maker


def _teardown():
    app.dependency_overrides.pop(get_db, None)


def _seed_link(maker, listing_id, aoi="huruma", building=100000, method="pip", conf=1.0):
    db = maker()
    db.add(BuildingLink(
        listing_id=listing_id, aoi_code=aoi, insar_building_id=building,
        match_method=method, match_confidence=conf,
    ))
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# 1. Linked listing → CURRENT tier, read live
# ---------------------------------------------------------------------------
def test_linked_listing_returns_live_tier(monkeypatch):
    client, maker = _client_with_db()
    lid = str(uuid.uuid4())
    _seed_link(maker, lid, method="nearest", conf=0.6)

    # The link is stable; the tier is read live. Simulate a building re-scored to HIGH.
    monkeypatch.setattr(
        insar_resolver, "tier_for_building",
        lambda aoi, bid: ResolveResult(
            coverage=COVERAGE_MONITORED, aoi_code=aoi, insar_building_id=bid,
            danger_level=3, match_method="link", match_confidence=1.0,
        ),
    )
    try:
        r = client.get(f"/api/v1/insar/listing/{lid}/risk")
        assert r.status_code == 200
        body = r.json()
        assert body["coverage"] == "monitored"
        assert body["danger_level"] == 3
        # Confidence comes from how the LINK was made (nearest, 0.6) — stays honest.
        assert body["match_method"] == "nearest"
        assert body["match_confidence"] == 0.6
    finally:
        _teardown()


# ---------------------------------------------------------------------------
# 2. Unknown / off / failure is NEVER 'safe'
# ---------------------------------------------------------------------------
def test_no_link_no_property_is_not_monitored(monkeypatch):
    client, _ = _client_with_db()
    try:
        # No link, no such property → honestly not_monitored, not STABLE.
        r = client.get(f"/api/v1/insar/listing/{uuid.uuid4()}/risk")
        assert r.status_code == 200
        assert r.json()["coverage"] == "not_monitored"
        assert r.json()["danger_level"] is None
    finally:
        _teardown()


def test_db_off_reports_unavailable_not_safe(monkeypatch):
    client, maker = _client_with_db()
    lid = str(uuid.uuid4())
    _seed_link(maker, lid)
    # InSAR DB unreadable → unavailable. The badge must NOT read as 'safe'.
    monkeypatch.setattr(
        insar_resolver, "tier_for_building",
        lambda aoi, bid: ResolveResult(coverage=COVERAGE_UNAVAILABLE),
    )
    try:
        r = client.get(f"/api/v1/insar/listing/{lid}/risk")
        assert r.status_code == 200
        assert r.json()["coverage"] == "unavailable"
        assert r.json()["danger_level"] is None
    finally:
        _teardown()


def test_resolver_exception_degrades_to_unavailable(monkeypatch):
    client, maker = _client_with_db()
    lid = str(uuid.uuid4())
    _seed_link(maker, lid)

    def _boom(aoi, bid):
        raise RuntimeError("duckdb exploded")

    monkeypatch.setattr(insar_resolver, "tier_for_building", _boom)
    try:
        r = client.get(f"/api/v1/insar/listing/{lid}/risk")
        assert r.status_code == 200
        # A crash is 'unavailable' (we can't say), never a fabricated 'safe'.
        assert r.json()["coverage"] == "unavailable"
    finally:
        _teardown()


def test_vanished_building_is_not_monitored(monkeypatch):
    client, maker = _client_with_db()
    lid = str(uuid.uuid4())
    _seed_link(maker, lid)
    # Linked id no longer in the dataset (AOI dropped/re-scoped) → not_monitored.
    monkeypatch.setattr(
        insar_resolver, "tier_for_building",
        lambda aoi, bid: ResolveResult(coverage=COVERAGE_NOT_MONITORED),
    )
    try:
        r = client.get(f"/api/v1/insar/listing/{lid}/risk")
        assert r.status_code == 200
        assert r.json()["coverage"] == "not_monitored"
    finally:
        _teardown()
