"""GET /insar/shops/near — the shops-on-the-InSAR-map aggregator (§8.1a).

Weespas owns the BuildingLink spine + StructuralFlag "second sensor" and mints an S2S
read:feed commerce token to fetch shop display-meta. The commerce HTTP call is monkeypatched
(commerce_read_client.shops_by_property) — this suite asserts the AGGREGATION, not the network
client (which has commerce-side coverage). Load-bearing properties under test:

  1. Linked buildings that are shops → one pin each; the FE-key ``insar_building_id`` + non-PII
     meta ride along, NEVER lat/lng (S6).
  2. ``property_uuid`` is not unique — one uuid on TWO buildings yields TWO pins (nothing lost).
  3. ``confirmed`` is PER-BUILDING: only a footprint with its own structural flag is confirmed,
     never an unflagged building sharing a shop's listing.
  4. Graceful degradation: a commerce read failure → ``partial=true`` + empty pins, HTTP 200
     (the map must never go dark on a commerce hiccup).
  5. An empty AOI does ZERO commerce work (no wasted S2S call).
  6. Telemetry-gated: a caller without the telemetry token is refused.
  7. A malformed commerce row is skipped, not turned into a broken pin.
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from PE.weespas.main import app
from PE.weespas.core.database import Base, get_db
from PE.weespas.models.insar_link import BuildingLink, StructuralFlag, FLAG_UNSAFE, FLAG_CLEARED
from PE.weespas.services import commerce_read_client, entitlement_service
from PE.weespas.services.auth_service import require_insar_telemetry_token


@pytest.fixture
def env():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    def _override_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = _override_db
    # Default: the telemetry gate passes with a fixed user id. Individual auth tests remove it.
    app.dependency_overrides[require_insar_telemetry_token] = lambda: "user-1"
    yield db
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(require_insar_telemetry_token, None)
    db.close()


def _link(db, listing_id, building, aoi="huruma"):
    db.add(BuildingLink(
        listing_id=listing_id, aoi_code=aoi, insar_building_id=building,
        match_method="pip", match_confidence=1.0,
    ))
    db.commit()


def _flag(db, building, aoi="huruma", state=FLAG_UNSAFE):
    db.add(StructuralFlag(
        aoi_code=aoi, insar_building_id=building, state=state, source="engineer",
    ))
    db.commit()


def _shop(property_uuid, *, shop_id=None, name="Mama Mboga", category="grocery"):
    return {
        "property_uuid": property_uuid,
        "shop_id": shop_id or str(uuid.uuid4()),
        "name": name,
        "category": category,
    }


def _patch_commerce(monkeypatch, rows_or_exc):
    """Stub the S2S commerce read. Pass a list of shop dicts, or an exception instance to raise."""
    calls = {"n": 0, "last_uuids": None}

    def _fake(user_id, role, property_uuids):
        calls["n"] += 1
        calls["last_uuids"] = property_uuids
        if isinstance(rows_or_exc, Exception):
            raise rows_or_exc
        # Mirror the real client: only return rows for the uuids actually asked about.
        asked = set(property_uuids)
        return [r for r in rows_or_exc if r["property_uuid"] in asked]

    monkeypatch.setattr(commerce_read_client, "shops_by_property", _fake)
    return calls


# ── 1. happy path — a shop pins to its building, meta rides along, no lat/lng ────

def test_shop_pins_with_meta_no_coordinates(env, monkeypatch):
    db = env
    lid = str(uuid.uuid4())
    _link(db, lid, 100, aoi="huruma")
    _patch_commerce(monkeypatch, [_shop(lid, name="Duka la Juma", category="grocery")])

    client = TestClient(app)
    body = client.get("/api/v1/insar/shops/near", params={"aoi": "huruma"}).json()

    assert body["aoi_code"] == "huruma"
    assert body["partial"] is False
    assert len(body["shops"]) == 1
    pin = body["shops"][0]
    assert pin["property_uuid"] == lid
    assert pin["insar_building_id"] == 100
    assert pin["name"] == "Duka la Juma"
    assert pin["category"] == "grocery"
    assert pin["confirmed"] is False        # no structural flag on building 100
    # S6: a shop's raw coordinates NEVER leave commerce — the footprint is the location.
    assert "lat" not in pin and "lng" not in pin and "longitude" not in pin


# ── 2. shared footprint — one property_uuid on two buildings → two pins ──────────

def test_shared_uuid_on_two_buildings_yields_two_pins(env, monkeypatch):
    db = env
    lid = str(uuid.uuid4())
    _link(db, lid, 200, aoi="huruma")
    _link(db, lid, 201, aoi="huruma")
    _patch_commerce(monkeypatch, [_shop(lid, name="Chain Store")])

    client = TestClient(app)
    body = client.get("/api/v1/insar/shops/near", params={"aoi": "huruma"}).json()

    buildings = sorted(p["insar_building_id"] for p in body["shops"])
    assert buildings == [200, 201]          # nothing collapsed away by uuid


# ── 3. confirmed is PER-BUILDING, not per-listing ───────────────────────────────

def test_confirmed_is_per_building(env, monkeypatch):
    db = env
    lid = str(uuid.uuid4())
    _link(db, lid, 300, aoi="huruma")       # this footprint IS flagged
    _link(db, lid, 301, aoi="huruma")       # same listing, DIFFERENT footprint, unflagged
    _flag(db, 300, aoi="huruma", state=FLAG_UNSAFE)
    _patch_commerce(monkeypatch, [_shop(lid)])

    client = TestClient(app)
    body = client.get("/api/v1/insar/shops/near", params={"aoi": "huruma"}).json()

    confirmed_by_building = {p["insar_building_id"]: p["confirmed"] for p in body["shops"]}
    assert confirmed_by_building == {300: True, 301: False}


def test_cleared_flag_counts_as_confirmed(env, monkeypatch):
    db = env
    lid = str(uuid.uuid4())
    _link(db, lid, 400, aoi="kilimani")
    _flag(db, 400, aoi="kilimani", state=FLAG_CLEARED)  # an authority CLEARED it — still assessed
    _patch_commerce(monkeypatch, [_shop(lid)])

    client = TestClient(app)
    body = client.get("/api/v1/insar/shops/near", params={"aoi": "kilimani"}).json()
    assert body["shops"][0]["confirmed"] is True


# ── 4. graceful degradation — commerce down → partial, map still renders ─────────

def test_commerce_failure_degrades_to_partial(env, monkeypatch):
    db = env
    lid = str(uuid.uuid4())
    _link(db, lid, 500, aoi="huruma")
    _patch_commerce(monkeypatch, commerce_read_client.CommerceReadError("commerce down"))

    client = TestClient(app)
    resp = client.get("/api/v1/insar/shops/near", params={"aoi": "huruma"})

    assert resp.status_code == 200          # never errors the map
    body = resp.json()
    assert body["partial"] is True
    assert body["shops"] == []


# ── 5. empty AOI does zero commerce work ─────────────────────────────────────────

def test_empty_aoi_makes_no_commerce_call(env, monkeypatch):
    calls = _patch_commerce(monkeypatch, [])  # would count any call

    client = TestClient(app)
    body = client.get("/api/v1/insar/shops/near", params={"aoi": "nowhere"}).json()

    assert body["shops"] == [] and body["partial"] is False
    assert calls["n"] == 0                  # no links → no S2S round-trip


def test_linked_but_no_shops_is_complete_empty(env, monkeypatch):
    db = env
    _link(db, str(uuid.uuid4()), 600, aoi="huruma")   # a linked building that is NOT a shop
    calls = _patch_commerce(monkeypatch, [])          # commerce returns no shops

    client = TestClient(app)
    body = client.get("/api/v1/insar/shops/near", params={"aoi": "huruma"}).json()

    assert body["shops"] == [] and body["partial"] is False
    assert calls["n"] == 1                            # links existed → commerce WAS asked


# ── 6. telemetry gate ────────────────────────────────────────────────────────────

def test_requires_telemetry_token(env):
    # Remove the override so the real gate runs and rejects a token-less caller.
    app.dependency_overrides.pop(require_insar_telemetry_token, None)
    client = TestClient(app)
    resp = client.get("/api/v1/insar/shops/near", params={"aoi": "huruma"})
    assert resp.status_code in (401, 403)


# ── 7. malformed commerce row is skipped, not emitted as a broken pin ────────────

def test_malformed_row_is_skipped(env, monkeypatch):
    db = env
    good, bad = str(uuid.uuid4()), str(uuid.uuid4())
    _link(db, good, 700, aoi="huruma")
    _link(db, bad, 701, aoi="huruma")
    # `bad` row is missing a name (None) — must be dropped, not surfaced as a pin.
    _patch_commerce(monkeypatch, [
        _shop(good, name="Valid Shop"),
        _shop(bad, name=None),
    ])

    client = TestClient(app)
    body = client.get("/api/v1/insar/shops/near", params={"aoi": "huruma"}).json()

    assert len(body["shops"]) == 1
    assert body["shops"][0]["property_uuid"] == good


# ── 8. per-sub rate limit — an exhausted window is refused, other work is spared ──

def test_rate_limit_refuses_when_window_exhausted(env, monkeypatch):
    # Force the limiter closed regardless of Redis (which is absent in tests → fail-open otherwise).
    # Assert the aggregator throttles BEFORE any DB/commerce work: 429 and zero S2S calls.
    db = env
    _link(db, str(uuid.uuid4()), 800, aoi="huruma")
    calls = _patch_commerce(monkeypatch, [])
    monkeypatch.setattr(entitlement_service, "check_rate_limit", lambda *a, **k: False)

    client = TestClient(app)
    resp = client.get("/api/v1/insar/shops/near", params={"aoi": "huruma"})

    assert resp.status_code == 429
    assert calls["n"] == 0                    # throttled before the S2S round-trip


def test_rate_limit_allows_under_budget(env, monkeypatch):
    # The complement: with the limiter open the call proceeds exactly as before.
    db = env
    lid = str(uuid.uuid4())
    _link(db, lid, 801, aoi="huruma")
    _patch_commerce(monkeypatch, [_shop(lid, name="Under Budget")])
    monkeypatch.setattr(entitlement_service, "check_rate_limit", lambda *a, **k: True)

    client = TestClient(app)
    body = client.get("/api/v1/insar/shops/near", params={"aoi": "huruma"}).json()
    assert len(body["shops"]) == 1
