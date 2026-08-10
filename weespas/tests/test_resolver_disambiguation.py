"""Attribute-aware resolver — the "bad pin" disambiguation, veto, land, provisional tier.

Two layers under test:
  1. `rank_candidates` (pure, no DB): scoring + veto invariants — including the security
     rules (text never PROMOTES; veto never empties the candidate set).
  2. `resolve_point` / endpoints: decision logic over a FAKE DuckDB connection, so the
     tests need no real InSAR data.

Cardinal rule throughout: an ambiguous / unknown / DB-off state is NEVER reported as a
confident monitored reading, and never as "safe".
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from PE.weespas.main import app
from PE.weespas.core.database import Base, get_db
from PE.weespas.core.config import settings
from PE.weespas.models.user import User, UserRole
from PE.weespas.models.insar_link import BuildingLink, BuildingLinkCandidate
from PE.weespas.services import insar_resolver as R
from PE.weespas.services.insar_resolver import Candidate, rank_candidates
from PE.weespas.services.insar_text_signals import FloorSignal, parse_floor_signals
from PE.weespas.services.auth_service import get_current_user


# --------------------------------------------------------------------------- pure scorer

def _c(bid, floors, dist, danger=0, imp=False, area=None, contains=False):
    return Candidate("huruma", bid, danger, floors, imp, floors * 3.2, dist, area, contains)


def test_single_candidate_scores_and_survives():
    cs = [_c(1, 6, 4)]
    live, empty = rank_candidates(cs, "apartment", FloorSignal(), None, buffer_radius_m=15)
    assert not empty and len(live) == 1 and live[0].score > 0


def test_floor_veto_eliminates_too_short_building():
    # A 5th-floor unit cannot be in a 1-floor footprint; the 10-floor one survives.
    cs = [_c(1, 1, 3), _c(2, 10, 5)]
    live, empty = rank_candidates(cs, "apartment", FloorSignal(min_required_floors=5),
                                  None, buffer_radius_m=15)
    assert not empty
    assert [c.building_id for c in live] == [2]
    assert cs[0].vetoed is True


def test_veto_never_empties_the_set():
    # Text says 5th floor but EVERY candidate is 1-floor → roll back the veto (a human
    # resolves), never erase the set (which would force not_monitored / steer off risk).
    cs = [_c(1, 1, 3), _c(2, 1, 5)]
    live, empty = rank_candidates(cs, "apartment", FloorSignal(min_required_floors=5),
                                  None, buffer_radius_m=15)
    assert empty is True
    assert len(live) == 2
    assert not any(c.vetoed for c in cs)


def test_text_never_promotes_toward_taller_building():
    # Identical except geometry: a CLOSE short building vs a FAR tall one, with "penthouse"
    # text. Penthouse must not pull the match to the tall/far one — distance still wins.
    cs = [_c(1, 2, 2), _c(2, 12, 9)]
    live, _ = rank_candidates(cs, "apartment", FloorSignal(penthouse=True),
                              None, buffer_radius_m=15)
    assert live[0].building_id == 1


def test_area_trap_apartment_ignores_unit_area():
    # An apartment's unit area is unrelated to footprint area → area_term must be 0, so two
    # equal-distance, equal-floor candidates score identically regardless of footprint size.
    cs = [_c(1, 6, 5, area=400), _c(2, 6, 5, area=120)]
    live, _ = rank_candidates(cs, "apartment", FloorSignal(), 120.0, buffer_radius_m=15)
    assert round(live[0].score, 6) == round(live[1].score, 6)


def test_area_helps_for_house():
    # For a house the footprint ≈ the unit, so a 120 m² house should prefer the 120 m² footprint.
    cs = [_c(1, 2, 5, area=400), _c(2, 2, 5, area=120)]
    live, _ = rank_candidates(cs, "house", FloorSignal(), 120.0, buffer_radius_m=15)
    assert live[0].building_id == 2


# --------------------------------------------------------------------------- fake DuckDB

class _FakeCon:
    """Minimal DuckDB stand-in: returns canned rows for the gather / tier / geojson queries."""

    def __init__(self, gather_rows=None, tier_rows=None, geojson_rows=None):
        self._gather = gather_rows or []
        self._tier = tier_rows or []
        self._geojson = geojson_rows or []
        self._last = None

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if "ST_DWithin" in s:
            self._last = self._gather
        elif "ST_AsGeoJSON" in s:
            self._last = self._geojson
        else:  # the single-tier lookup or IN-clause tier read
            self._last = self._tier
        return self

    def fetchall(self):
        return list(self._last or [])

    def fetchone(self):
        return (self._last[0] if self._last else None)

    def close(self):
        pass


# gather row shape: (aoi, bid, danger, height, n_floors, fused_h, h_imp, dist_deg, area_deg2, contains)
def _grow(bid, danger, floors, dist_m, contains=False):
    deg = dist_m * R._DEG_PER_M
    h = floors * 3.2
    return ("huruma", bid, danger, h, floors, h, False, deg, 100 * R._DEG_PER_M * R._DEG_PER_M, contains)


@pytest.fixture
def patch_con(monkeypatch):
    def _install(con):
        monkeypatch.setattr(R, "_connect", lambda: con)
    return _install


def test_resolve_db_off_is_unavailable(monkeypatch):
    monkeypatch.setattr(R, "_connect", lambda: None)
    res = R.resolve_point(-1.0, 36.0, category="apartment")
    assert res.coverage == R.COVERAGE_UNAVAILABLE


def test_resolve_pip_is_authoritative(patch_con):
    patch_con(_FakeCon(gather_rows=[_grow(7, 2, 6, 0.0, contains=True), _grow(8, 4, 3, 9)]))
    res = R.resolve_point(-1.0, 36.0, category="apartment", title="flat")
    assert res.coverage == R.COVERAGE_MONITORED
    assert res.insar_building_id == 7 and res.match_method == R.METHOD_PIP


def test_resolve_clear_winner_auto_links(patch_con):
    # One footprint much closer than the other → clear winner, monitored.
    patch_con(_FakeCon(gather_rows=[_grow(1, 1, 6, 1), _grow(2, 4, 6, 14)]))
    res = R.resolve_point(-1.0, 36.0, category="apartment")
    assert res.coverage == R.COVERAGE_MONITORED
    assert res.insar_building_id == 1 and res.match_method == R.METHOD_DISAMBIGUATED


def test_resolve_ambiguous_is_needs_confirmation_with_worst_case_tier(patch_con):
    # Two near-equal candidates → needs_confirmation; provisional tier = MAX danger (4).
    patch_con(_FakeCon(gather_rows=[_grow(1, 1, 6, 5), _grow(2, 4, 6, 5)]))
    res = R.resolve_point(-1.0, 36.0, category="apartment")
    assert res.coverage == R.COVERAGE_NEEDS_CONFIRMATION
    assert res.provisional is True and res.danger_level == 4
    assert res.candidate_count == 2


def test_resolve_no_candidates_is_not_monitored(patch_con):
    patch_con(_FakeCon(gather_rows=[]))
    res = R.resolve_point(-1.0, 36.0, category="apartment")
    assert res.coverage == R.COVERAGE_NOT_MONITORED


def test_land_aggregates_worst_neighbour_never_a_building_tier(patch_con):
    patch_con(_FakeCon(gather_rows=[_grow(1, 1, 2, 10), _grow(2, 3, 5, 20)]))
    res = R.resolve_point(-1.0, 36.0, category="land")
    assert res.coverage == R.COVERAGE_MONITORED_LAND
    assert res.land_ground_band == 3 and res.land_neighbor_count == 2
    assert res.danger_level is None        # land NEVER carries a building danger_level


def test_land_no_neighbours_is_not_monitored(patch_con):
    patch_con(_FakeCon(gather_rows=[]))
    res = R.resolve_point(-1.0, 36.0, category="land")
    assert res.coverage == R.COVERAGE_NOT_MONITORED


# --------------------------------------------------------------------------- endpoints

@pytest.fixture
def env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    yield db
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)
    db.close()


def _user(db, agent_id=None):
    u = User(id=str(uuid.uuid4()), name="agent", email=f"{uuid.uuid4()}@t.co",
             phone=f"+2547{uuid.uuid4().int % 10**8:08d}", hashed_password="x",
             role=UserRole.AGENT, agent_id=agent_id or str(uuid.uuid4()))
    db.add(u); db.commit()
    return u


def _listing(db, agent_id):
    """Minimal Property row owned by agent_id (only the fields the endpoints read)."""
    from PE.weespas.models.property import Property, PropertyListingType
    p = Property(id=str(uuid.uuid4()), title="t", price=1, listing_type=PropertyListingType.RENT,
                 category_id="c", agent_id=agent_id)
    db.add(p); db.commit()
    return p


def _auth_as(user):
    app.dependency_overrides[get_current_user] = lambda: user


def _cand(db, listing_id, bid, rank, vetoed=False):
    db.add(BuildingLinkCandidate(listing_id=listing_id, aoi_code="huruma",
                                 insar_building_id=bid, rank=rank, score=0.5,
                                 distance_m=5.0, height_m=20.0, n_floors=6, vetoed=vetoed))
    db.commit()


def test_candidates_requires_ownership(env, monkeypatch):
    db = env
    owner = _user(db)
    other = _user(db)
    p = _listing(db, owner.agent_id)
    _cand(db, p.id, 1, 0)
    client = TestClient(app)

    _auth_as(other)
    assert client.get(f"/api/v1/insar/listing/{p.id}/candidates").status_code == 403

    _auth_as(owner)
    monkeypatch.setattr(R, "_connect",
                        lambda: _FakeCon(geojson_rows=[("huruma", 1, 2, '{"type":"Polygon","coordinates":[]}')]))
    r = client.get(f"/api/v1/insar/listing/{p.id}/candidates")
    assert r.status_code == 200
    body = r.json()
    assert body["candidates"][0]["danger_level"] == 2          # LIVE tier, re-read
    assert body["candidates"][0]["insar_building_id"] == 1


def test_candidates_404_for_missing_listing(env):
    db = env
    _auth_as(_user(db))
    assert TestClient(app).get("/api/v1/insar/listing/nope/candidates").status_code == 404


def test_confirm_rejects_non_candidate_building(env):
    db = env
    owner = _user(db)
    p = _listing(db, owner.agent_id)
    _cand(db, p.id, 1, 0)
    _auth_as(owner)
    r = TestClient(app).post(f"/api/v1/insar/listing/{p.id}/confirm",
                             json={"insar_building_id": 999})   # 999 is NOT a candidate
    assert r.status_code == 400


def test_confirm_writes_authoritative_link_and_monitors(env, monkeypatch):
    db = env
    owner = _user(db)
    p = _listing(db, owner.agent_id)
    _cand(db, p.id, 1, 0)
    _cand(db, p.id, 2, 1)
    _auth_as(owner)
    monkeypatch.setattr(R, "_connect", lambda: _FakeCon(tier_rows=[(3,)]))

    r = TestClient(app).post(f"/api/v1/insar/listing/{p.id}/confirm",
                             json={"insar_building_id": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["coverage"] == R.COVERAGE_MONITORED
    assert body["danger_level"] == 3 and body["match_method"] == R.METHOD_AGENT_CONFIRMED

    link = db.query(BuildingLink).filter(BuildingLink.listing_id == p.id).first()
    assert link is not None and link.confirmed_by_agent is True
    assert int(link.insar_building_id) == 2

    from PE.weespas.models.property import VERIFICATION_MONITORED
    db.refresh(p)
    assert p.verification_status == VERIFICATION_MONITORED


def test_confirmed_link_is_never_overwritten_by_autoresolve(env, monkeypatch):
    # The cardinal anti-clobber guard: once a human confirms, resolve_and_link must not
    # re-resolve over it — it just re-reads the confirmed building's live tier.
    db = env
    owner = _user(db)
    p = _listing(db, owner.agent_id)
    db.add(BuildingLink(listing_id=p.id, aoi_code="huruma", insar_building_id=42,
                        match_method=R.METHOD_AGENT_CONFIRMED, match_confidence=1.0,
                        confirmed_by_agent=True))
    db.commit()
    monkeypatch.setattr(R, "_connect", lambda: _FakeCon(tier_rows=[(1,)]))

    res = R.resolve_and_link(db, listing_id=p.id, lat=-1.0, lon=36.0, category="apartment")
    assert res.coverage == R.COVERAGE_MONITORED and res.insar_building_id == 42
    link = db.query(BuildingLink).filter(BuildingLink.listing_id == p.id).first()
    assert int(link.insar_building_id) == 42 and link.confirmed_by_agent is True


def test_resolve_and_link_persists_candidates_for_ambiguous(env, monkeypatch):
    db = env
    owner = _user(db)
    p = _listing(db, owner.agent_id)
    monkeypatch.setattr(R, "_connect",
                        lambda: _FakeCon(gather_rows=[_grow(1, 1, 6, 5), _grow(2, 4, 6, 5)]))
    res = R.resolve_and_link(db, listing_id=p.id, lat=-1.0, lon=36.0, category="apartment")
    assert res.coverage == R.COVERAGE_NEEDS_CONFIRMATION
    # No authoritative link for an ambiguous state...
    assert db.query(BuildingLink).filter(BuildingLink.listing_id == p.id).first() is None
    # ...but the candidate set IS persisted for the confirm UI / provisional tier.
    cands = db.query(BuildingLinkCandidate).filter(
        BuildingLinkCandidate.listing_id == p.id).all()
    assert {int(c.insar_building_id) for c in cands} == {1, 2}
