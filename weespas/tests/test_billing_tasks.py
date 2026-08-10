"""The reconciliation sweep task: no-op when billing is off, the age-window filter,
and that an in-window pending intent is handed to billing_service.reconcile_intent.

The task opens its own SessionLocal, so we point that at an in-memory SQLite engine
and stub reconcile_intent (its idempotent settle is already covered in
test_billing_service). This test is about the SELECT window + the disabled guard.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from PE.weespas.core.database import Base
from PE.weespas.models.user import User, UserRole
from PE.weespas.models.billing import PaymentIntent, INTENT_PENDING, INTENT_PAID
from PE.weespas.services import billing_tasks as bt
from PE.weespas.services import billing_service


@pytest.fixture()
def engine_sessionmaker():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _enable_billing(monkeypatch):
    """is_billing_enabled is a read-only computed property; flip it ON by setting the
    four credential fields it derives from."""
    for field in ("mpesa_consumer_key", "mpesa_consumer_secret",
                  "mpesa_shortcode", "mpesa_passkey"):
        monkeypatch.setattr(bt.settings, field, "x", raising=False)


@pytest.fixture()
def setup(monkeypatch, engine_sessionmaker):
    """Point the task's SessionLocal at our in-memory DB and force billing ON."""
    monkeypatch.setattr(bt, "SessionLocal", engine_sessionmaker)
    _enable_billing(monkeypatch)
    return engine_sessionmaker


def _user(db):
    u = User(name="t", email=f"{uuid.uuid4()}@e.com", phone="0712345678",
             hashed_password="x", role=UserRole.USER)
    db.add(u); db.commit(); db.refresh(u)
    return u


def _intent(db, user, *, crid, age_seconds, status=INTENT_PENDING):
    created = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    intent = PaymentIntent(
        user_id=user.id, phone=user.phone, tier="T1", amount_kes=20,
        status=status, checkout_request_id=crid, created_at=created,
    )
    db.add(intent); db.commit(); db.refresh(intent)
    return intent


def test_noop_when_billing_disabled(monkeypatch, engine_sessionmaker):
    monkeypatch.setattr(bt, "SessionLocal", engine_sessionmaker)
    # creds are empty in the test env ⇒ is_billing_enabled is False (we do NOT call
    # _enable_billing here). Even with an in-window intent present, it does nothing.
    db = engine_sessionmaker()
    u = _user(db)
    _intent(db, u, crid="CR-x", age_seconds=300)
    db.close()
    assert bt.reconcile_pending_intents() == 0


def test_only_in_window_pending_intents_are_reconciled(setup, monkeypatch):
    sm = setup
    db = sm()
    u = _user(db)
    too_young = _intent(db, u, crid="CR-young", age_seconds=10)     # < 90s → skip
    in_window = _intent(db, u, crid="CR-good", age_seconds=300)     # in [90s,1h] → check
    too_old = _intent(db, u, crid="CR-old", age_seconds=7200)       # > 1h → skip
    already = _intent(db, u, crid="CR-paid", age_seconds=300, status=INTENT_PAID)  # not pending
    db.close()

    seen: list[str] = []

    def fake_reconcile(_db, intent):
        seen.append(intent.checkout_request_id)
        return "granted"

    monkeypatch.setattr(billing_service, "reconcile_intent", fake_reconcile)

    granted = bt.reconcile_pending_intents()
    assert seen == ["CR-good"]           # exactly the one in-window pending intent
    assert granted == 1


def test_one_failure_does_not_abort_the_sweep(setup, monkeypatch):
    sm = setup
    db = sm()
    u = _user(db)
    _intent(db, u, crid="CR-a", age_seconds=200)
    _intent(db, u, crid="CR-b", age_seconds=300)
    db.close()

    def flaky(_db, intent):
        if intent.checkout_request_id == "CR-a":
            raise RuntimeError("boom")
        return "granted"

    monkeypatch.setattr(billing_service, "reconcile_intent", flaky)
    # the exception on CR-a is swallowed; CR-b still settles
    assert bt.reconcile_pending_intents() == 1
