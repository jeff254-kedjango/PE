"""§8.1b pair-radiate — contact_service.viewer_building_ids_in_aoi (Chunk 1).

The buyer half of pair-radiate: "which building footprints in this AOI does the viewer own?"
Ownership follows the shipped spine ``User.agent_id → Property.agent_id → BuildingLink`` scoped to
the AOI. Load-bearing properties under test:

  1. A viewer who owns a linked building in the AOI resolves to that building_id.
  2. Scoping is strict: buildings in OTHER AOIs, and buildings owned by OTHER agents, never leak.
  3. A plain buyer with no ``agent_id`` (owns no listings) resolves to ``[]`` — the common case.
  4. A footprint shared across the viewer's own listings glows ONCE (DISTINCT).
  5. The cap bounds the result deterministically (anti-O(n), lowest building_ids kept).
"""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from PE.weespas.core.database import Base
from PE.weespas.models.insar_link import BuildingLink
from PE.weespas.models.property import (
    Agent, Property, PropertyCategory, PropertyListingType,
)
from PE.weespas.models.user import User, UserRole
from PE.weespas.services import contact_service


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


# One shared category — Property.category_id is a RESTRICT FK, so it must exist.
def _category(db) -> str:
    cid = str(uuid.uuid4())
    db.add(PropertyCategory(id=cid, name="house", slug="house"))
    db.commit()
    return cid


def _agent(db, phone: str) -> str:
    aid = str(uuid.uuid4())
    db.add(Agent(id=aid, agent_name="A", agent_phone_number=phone))
    db.commit()
    return aid


def _user(db, agent_id: str | None, suffix: str) -> str:
    uid = str(uuid.uuid4())
    db.add(User(
        id=uid, name="U", email=f"u{suffix}@x.io", phone=f"+2547{suffix:0>8}",
        hashed_password="x", role=UserRole.AGENT if agent_id else UserRole.USER,
        agent_id=agent_id,
    ))
    db.commit()
    return uid


def _listing(db, category_id: str, agent_id: str) -> str:
    lid = str(uuid.uuid4())
    db.add(Property(
        id=lid, title="L", price=1, currency="KES",
        listing_type=PropertyListingType.SALE, category_id=category_id, agent_id=agent_id,
    ))
    db.commit()
    return lid


def _link(db, listing_id: str, building: int, aoi: str = "huruma"):
    db.add(BuildingLink(
        listing_id=listing_id, aoi_code=aoi, insar_building_id=building,
        match_method="pip", match_confidence=1.0,
    ))
    db.commit()


CAP = 50


# ── 1. happy path — viewer owns a linked building in the AOI ─────────────────────

def test_owned_building_resolves(db):
    cat = _category(db)
    agent = _agent(db, "+254700000001")
    viewer = _user(db, agent, "1")
    lid = _listing(db, cat, agent)
    _link(db, lid, 100, aoi="huruma")

    assert contact_service.viewer_building_ids_in_aoi(db, viewer, "huruma", cap=CAP) == [100]


# ── 2. strict scoping — other AOI and other agent never leak ─────────────────────

def test_other_aoi_and_other_agent_excluded(db):
    cat = _category(db)
    mine = _agent(db, "+254700000002")
    viewer = _user(db, mine, "2")
    my_listing = _listing(db, cat, mine)
    _link(db, my_listing, 200, aoi="huruma")     # mine, right AOI  → included
    _link(db, my_listing, 201, aoi="kilimani")   # mine, WRONG AOI  → excluded

    # A different agent's building in the SAME AOI must never appear for the viewer.
    other = _agent(db, "+254700000003")
    _user(db, other, "3")
    other_listing = _listing(db, cat, other)
    _link(db, other_listing, 202, aoi="huruma")  # not mine → excluded

    assert contact_service.viewer_building_ids_in_aoi(db, viewer, "huruma", cap=CAP) == [200]


# ── 3. plain buyer (no agent_id) owns nothing ────────────────────────────────────

def test_buyer_without_agent_id_resolves_empty(db):
    _category(db)
    buyer = _user(db, None, "4")   # no agent_id → owns no listings
    assert contact_service.viewer_building_ids_in_aoi(db, buyer, "huruma", cap=CAP) == []


def test_unknown_viewer_resolves_empty(db):
    assert contact_service.viewer_building_ids_in_aoi(db, "ghost", "huruma", cap=CAP) == []
    # Defensive guards: blank inputs never hit the DB.
    assert contact_service.viewer_building_ids_in_aoi(db, "", "huruma", cap=CAP) == []
    assert contact_service.viewer_building_ids_in_aoi(db, "x", "", cap=CAP) == []


# ── 4. a footprint shared across the viewer's own listings glows once ────────────

def test_shared_footprint_deduped(db):
    cat = _category(db)
    agent = _agent(db, "+254700000005")
    viewer = _user(db, agent, "5")
    a = _listing(db, cat, agent)
    b = _listing(db, cat, agent)
    _link(db, a, 300, aoi="huruma")
    _link(db, b, 300, aoi="huruma")   # same footprint, two of the viewer's listings

    assert contact_service.viewer_building_ids_in_aoi(db, viewer, "huruma", cap=CAP) == [300]


# ── 5. cap bounds the result deterministically (lowest building_ids kept) ────────

def test_cap_is_deterministic(db):
    cat = _category(db)
    agent = _agent(db, "+254700000006")
    viewer = _user(db, agent, "6")
    lid = _listing(db, cat, agent)
    for b in (405, 401, 403, 402, 404):
        _link(db, lid, b, aoi="huruma")

    got = contact_service.viewer_building_ids_in_aoi(db, viewer, "huruma", cap=3)
    assert got == [401, 402, 403]   # ordered by building_id, first 3
