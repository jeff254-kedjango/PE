"""§8.1b — POST /insar/contact: the pair-radiate uplink (Chunk 2).

The uplink orchestrates the two halves of a contact (privacy decision #2, anonymized-on-browse):
  * BUYER half (always): returns the caller's OWN footprints in the AOI (glowed locally, from this
    response) + the glow TTL. Resolved from the verified token, never trusted from the client.
  * SELLER half (best-effort): publishes an ANONYMIZED {shop_building_id, aoi} pulse to the shop
    owner's channel. The buyer's building_ids NEVER cross to the seller (no home-location leak).

The commerce S2S seller lookup and the Pub/Sub publish are monkeypatched — this suite asserts the
ORCHESTRATION + the security invariants, not the network client or Redis (both covered elsewhere).
Load-bearing properties under test:
  1. buyer footprints are returned; the anonymized pulse carries ONLY shop_building_id + aoi;
  2. a forged/stale shop_building_id (no BuildingLink) is NOT published, but the buyer still glows;
  3. owner-opens-own-pin does not self-radiate;
  4. seller offline (0 subscribers) / commerce failure → radiated=false, buyer half intact;
  5. telemetry-gated; per-sub rate-limited before any S2S/publish work.
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from PE.weespas.main import app
from PE.weespas.core.config import settings
from PE.weespas.core.database import Base, get_db
from PE.weespas.models.insar_link import BuildingLink
from PE.weespas.models.property import (
    Agent, Property, PropertyCategory, PropertyListingType,
)
from PE.weespas.models.user import User, UserRole
from PE.weespas.services import commerce_read_client, entitlement_service, event_bus
from PE.weespas.services.auth_service import require_insar_telemetry_token

VIEWER = "viewer-sub-1"


@pytest.fixture
def env():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    def _override_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[require_insar_telemetry_token] = lambda: VIEWER
    # Rate limit open by default; the throttle test overrides it closed.
    import PE.weespas.services.entitlement_service as es
    _orig = es.check_rate_limit
    es.check_rate_limit = lambda *a, **k: True
    yield db
    es.check_rate_limit = _orig
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(require_insar_telemetry_token, None)
    db.close()


# ---- fixture builders (viewer owns a listing linked to a footprint) --------------

def _category(db) -> str:
    cid = str(uuid.uuid4())
    db.add(PropertyCategory(id=cid, name="house", slug="house"))
    db.commit()
    return cid


def _viewer_owns(db, building: int, aoi: str = "huruma", sub: str = VIEWER):
    """Make `sub` own a listing linked to `building` in `aoi`."""
    aid = str(uuid.uuid4())
    db.add(Agent(id=aid, agent_name="A", agent_phone_number=f"+2547{uuid.uuid4().int % 10**8:08d}"))
    db.commit()
    db.add(User(id=sub, name="U", email=f"{sub}@x.io", phone=f"+2546{uuid.uuid4().int % 10**8:08d}",
                hashed_password="x", role=UserRole.AGENT, agent_id=aid))
    cid = _category(db)
    lid = str(uuid.uuid4())
    db.add(Property(id=lid, title="L", price=1, currency="KES",
                    listing_type=PropertyListingType.SALE, category_id=cid, agent_id=aid))
    db.commit()
    _link(db, lid, building, aoi)


def _link(db, listing_id: str, building: int, aoi: str = "huruma"):
    db.add(BuildingLink(listing_id=listing_id, aoi_code=aoi, insar_building_id=building,
                        match_method="pip", match_confidence=1.0))
    db.commit()


def _patch_seller(monkeypatch, seller_uuid_or_exc):
    def _fake(user_id, role, shop_id):
        if isinstance(seller_uuid_or_exc, Exception):
            raise seller_uuid_or_exc
        return seller_uuid_or_exc
    monkeypatch.setattr(commerce_read_client, "seller_uuid_for_shop", _fake)


def _patch_publish(monkeypatch, *, subscribers=1):
    """Capture publishes; report `subscribers` as delivered (0 = seller offline)."""
    captured = []

    def _fake(channel, event):
        captured.append((channel, event))
        return subscribers
    monkeypatch.setattr(event_bus, "publish_sync", _fake)
    return captured


def _post(shop_building_id=500, aoi="huruma", shop_id="shop-1"):
    return TestClient(app).post("/api/v1/insar/contact", json={
        "shop_id": shop_id, "aoi": aoi, "shop_building_id": shop_building_id,
    })


# ── 1. happy path — buyer glows own footprint; seller gets ANONYMIZED pulse only ─

def test_buyer_glow_and_anonymized_seller_pulse(env, monkeypatch):
    db = env
    _viewer_owns(db, 500, aoi="huruma")     # viewer owns a footprint that is also a shop pin
    _link_second_own(db, 501, aoi="huruma")  # and a second own footprint in the same AOI
    _patch_seller(monkeypatch, "seller-uuid-9")
    captured = _patch_publish(monkeypatch, subscribers=1)

    body = _post(shop_building_id=500).json()

    assert sorted(body["own_building_ids"]) == [500, 501]
    assert body["glow_ttl_s"] == settings.contact_glow_ttl_s
    assert body["radiated"] is True
    # exactly one pulse, to the SELLER's channel, carrying ONLY shop_building_id + aoi.
    assert len(captured) == 1
    channel, event = captured[0]
    assert channel == "contact-events:seller-uuid-9"
    assert event == {"kind": "contact", "shop_building_id": 500, "aoi": "huruma"}
    # the buyer's OWN building_ids must NEVER appear in the seller payload (no home leak).
    assert "own_building_ids" not in event and "building_ids" not in event
    assert 501 not in event.values()


# helper: add a second footprint to the viewer's existing agent/listing
def _link_second_own(db, building, aoi="huruma"):
    prop = db.query(Property).join(User, User.agent_id == Property.agent_id).filter(
        User.id == VIEWER).first()
    _link(db, prop.id, building, aoi)


# ── 2. forged/stale shop_building_id is not published; buyer half still works ─────

def test_unlinked_building_id_not_radiated(env, monkeypatch):
    db = env
    _viewer_owns(db, 500, aoi="huruma")
    _patch_seller(monkeypatch, "seller-uuid-9")
    captured = _patch_publish(monkeypatch, subscribers=1)

    # 777 has no BuildingLink → not a real pin → must not radiate.
    body = _post(shop_building_id=777).json()

    assert body["radiated"] is False
    assert captured == []                       # nothing published for a forged id
    assert body["own_building_ids"] == [500]    # buyer glow unaffected


# ── 3. owner opening their OWN shop pin does not self-radiate ─────────────────────

def test_owner_self_open_does_not_radiate(env, monkeypatch):
    db = env
    _viewer_owns(db, 500, aoi="huruma")
    _patch_seller(monkeypatch, VIEWER)          # the shop's seller IS the viewer
    captured = _patch_publish(monkeypatch, subscribers=1)

    body = _post(shop_building_id=500).json()

    assert body["radiated"] is False
    assert captured == []                       # no self-pulse


# ── 4. seller offline / commerce down → radiated=false, buyer half intact ─────────

def test_seller_offline_is_nonfatal(env, monkeypatch):
    db = env
    _viewer_owns(db, 500, aoi="huruma")
    _patch_seller(monkeypatch, "seller-uuid-9")
    _patch_publish(monkeypatch, subscribers=0)  # seller not watching the map

    body = _post(shop_building_id=500).json()
    assert body["radiated"] is False
    assert body["own_building_ids"] == [500]


def test_commerce_failure_is_nonfatal(env, monkeypatch):
    db = env
    _viewer_owns(db, 500, aoi="huruma")
    _patch_seller(monkeypatch, commerce_read_client.CommerceReadError("commerce down"))
    captured = _patch_publish(monkeypatch, subscribers=1)

    resp = _post(shop_building_id=500)
    assert resp.status_code == 200              # never errors the contact
    body = resp.json()
    assert body["radiated"] is False
    assert captured == []
    assert body["own_building_ids"] == [500]


# ── 5a. plain buyer (owns nothing) — empty glow, seller still pulsed ──────────────

def test_plain_buyer_empty_glow(env, monkeypatch):
    db = env
    # No viewer-owned listing; but the shop's footprint must exist to radiate.
    _link(db, str(uuid.uuid4()), 500, aoi="huruma")
    _patch_seller(monkeypatch, "seller-uuid-9")
    captured = _patch_publish(monkeypatch, subscribers=1)

    body = _post(shop_building_id=500).json()
    assert body["own_building_ids"] == []       # buyer owns nothing → nothing to glow locally
    assert body["radiated"] is True             # seller still lights up
    assert len(captured) == 1


# ── 5b. telemetry gate ────────────────────────────────────────────────────────────

def test_requires_telemetry_token(env):
    app.dependency_overrides.pop(require_insar_telemetry_token, None)
    resp = _post()
    assert resp.status_code in (401, 403)


# ── 5c. per-sub rate limit throttles BEFORE any S2S/publish work ─────────────────

def test_rate_limit_refuses(env, monkeypatch):
    db = env
    _viewer_owns(db, 500, aoi="huruma")
    monkeypatch.setattr(entitlement_service, "check_rate_limit", lambda *a, **k: False)
    seller_called = {"n": 0}

    def _seller(*a, **k):
        seller_called["n"] += 1
        return "seller-uuid-9"
    monkeypatch.setattr(commerce_read_client, "seller_uuid_for_shop", _seller)
    captured = _patch_publish(monkeypatch, subscribers=1)

    resp = _post(shop_building_id=500)
    assert resp.status_code == 429
    assert seller_called["n"] == 0 and captured == []   # throttled before any work
