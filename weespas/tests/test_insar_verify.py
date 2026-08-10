"""Background InSAR footprint-verification of a listing (services.insar_verify_tasks).

Exercises the `_verify` core (the Celery wrapper only adds the retry + dedupe lock):
  1. each coverage maps to the right verification_status (identity map);
  2. a delivered notification lands in the UPLOADER's inbox on monitored/not_monitored;
  3. 'unavailable' is honest — status set, but NO notification (re-verifiable, not final);
  4. notify=False (backfill) never creates a notification;
  5. a listing with no address resolves to not_monitored, never silently 'safe'.

`resolve_and_link` is monkeypatched so the test needs no InSAR DuckDB — we assert the
task's branching, not the spatial resolve (covered by the resolver's own tests).
"""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from PE.weespas.core.database import Base
from PE.weespas.models.user import User, UserRole
from PE.weespas.models.property import (
    Property, Address, PropertyCategory, PropertyListingType,
    VERIFICATION_MONITORED, VERIFICATION_NOT_MONITORED, VERIFICATION_UNAVAILABLE,
)
from PE.weespas.models.notification import Notification
from PE.weespas.services import insar_verify_tasks, insar_resolver, notification_service
from PE.weespas.services.insar_resolver import ResolveResult


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _user(db):
    u = User(
        id=str(uuid.uuid4()), name="agent", email=f"{uuid.uuid4().hex}@t.co",
        phone=f"+2547{uuid.uuid4().int % 100000000:08d}",
        hashed_password="x", role=UserRole.AGENT,
    )
    db.add(u)
    db.commit()
    return u


def _listing(db, *, with_address=True):
    cat = PropertyCategory(id=str(uuid.uuid4()), name="House", slug=f"house-{uuid.uuid4().hex[:6]}")
    db.add(cat)
    db.flush()
    p = Property(
        id=str(uuid.uuid4()), title="Test home", price=1000, currency="KES",
        listing_type=PropertyListingType.SALE, category_id=cat.id,
    )
    if with_address:
        p.address = Address(location_name="x", latitude=-1.28, longitude=36.82)
    db.add(p)
    db.commit()
    return p


def _patch_resolve(monkeypatch, result: ResolveResult):
    # The resolver now also receives category/title/description/size_numeric for
    # disambiguation — accept **kwargs so the fake matches the real signature.
    def _fake(db, *, listing_id, lat, lon, **kwargs):
        return result
    monkeypatch.setattr(insar_verify_tasks.insar_resolver, "resolve_and_link", _fake)


def test_monitored_sets_status_and_notifies(db, monkeypatch):
    u = _user(db)
    p = _listing(db)
    _patch_resolve(monkeypatch, ResolveResult(
        coverage=insar_resolver.COVERAGE_MONITORED, aoi_code="south_c",
        insar_building_id=1, danger_level=2, match_method="pip", match_confidence=1.0,
    ))
    coverage = insar_verify_tasks._verify(db, listing_id=p.id, recipient_user_id=u.id, notify=True)

    assert coverage == VERIFICATION_MONITORED
    assert db.get(Property, p.id).verification_status == VERIFICATION_MONITORED
    assert db.get(Property, p.id).verified_at is not None
    notes = notification_service.list_for_user(db, u.id)
    assert len(notes) == 1 and "grid" in notes[0].title.lower()
    assert notes[0].link == f"/properties/{p.id}"


def test_not_monitored_sets_status_and_notifies(db, monkeypatch):
    u = _user(db)
    p = _listing(db)
    _patch_resolve(monkeypatch, ResolveResult(coverage=insar_resolver.COVERAGE_NOT_MONITORED))
    coverage = insar_verify_tasks._verify(db, listing_id=p.id, recipient_user_id=u.id, notify=True)

    assert coverage == VERIFICATION_NOT_MONITORED
    assert db.get(Property, p.id).verification_status == VERIFICATION_NOT_MONITORED
    assert notification_service.unread_count(db, u.id) == 1


def test_unavailable_sets_status_but_does_not_notify(db, monkeypatch):
    u = _user(db)
    p = _listing(db)
    _patch_resolve(monkeypatch, ResolveResult(coverage=insar_resolver.COVERAGE_UNAVAILABLE))
    coverage = insar_verify_tasks._verify(db, listing_id=p.id, recipient_user_id=u.id, notify=True)

    # Honest: status records the outage, but we send NO notification (not a final answer).
    assert coverage == VERIFICATION_UNAVAILABLE
    assert db.get(Property, p.id).verification_status == VERIFICATION_UNAVAILABLE
    assert notification_service.unread_count(db, u.id) == 0


def test_backfill_mode_never_notifies(db, monkeypatch):
    u = _user(db)
    p = _listing(db)
    _patch_resolve(monkeypatch, ResolveResult(
        coverage=insar_resolver.COVERAGE_MONITORED, aoi_code="south_c",
        insar_building_id=1, danger_level=0, match_method="pip", match_confidence=1.0,
    ))
    # notify=False (the backfill path): status updates, but no inbox spam.
    coverage = insar_verify_tasks._verify(db, listing_id=p.id, recipient_user_id=None, notify=False)

    assert coverage == VERIFICATION_MONITORED
    assert db.query(Notification).count() == 0


def test_listing_without_address_is_not_monitored(db, monkeypatch):
    u = _user(db)
    p = _listing(db, with_address=False)
    # resolve_and_link must NOT be called when there's no coordinate.
    def _boom(*a, **k):
        raise AssertionError("resolve_and_link should not run without an address")
    monkeypatch.setattr(insar_verify_tasks.insar_resolver, "resolve_and_link", _boom)

    coverage = insar_verify_tasks._verify(db, listing_id=p.id, recipient_user_id=u.id, notify=True)
    assert coverage == VERIFICATION_NOT_MONITORED
    assert db.get(Property, p.id).verification_status == VERIFICATION_NOT_MONITORED
