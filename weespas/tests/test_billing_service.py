"""Billing state machine: settle idempotency + window grant + reconciliation.

The one invariant that must never break (PE/billing_architecture.md §4): a settled
payment grants EXACTLY one window and writes EXACTLY one ledger row, no matter how
many times the callback is delivered or whether the reconciliation sweep races it.

These use a throwaway SQLite session (only the billing + users tables created) plus
the in-memory FakeRedis from test_entitlement_service, so no Postgres / no Redis /
no Daraja HTTP. mpesa_client's network calls are monkeypatched.
"""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from PE.weespas.core.database import Base
from PE.weespas.models.user import User, UserRole
from PE.weespas.models.billing import (
    PaymentIntent, PaymentLedger,
    INTENT_PENDING, INTENT_PAID, INTENT_FAILED,
)
from PE.weespas.services import billing_service as bs
from PE.weespas.services import entitlement_service as ent
from PE.weespas.services import mpesa_client
from PE.weespas.services.billing_tiers import get_tier

from tests.test_entitlement_service import FakeRedis


# --------------------------------------------------------------------------- #
#  fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    # User lazy-loads its user_roles rows on refresh, so build the full schema
    # (create_all is create-only + dialect-correct on SQLite, same as the rest of
    # the suite). The billing tables ride along.
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def user(db):
    u = User(name="t", email=f"{uuid.uuid4()}@e.com", phone="0712345678",
             hashed_password="x", role=UserRole.USER)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture()
def fake(monkeypatch):
    """One FakeRedis shared by billing_service (dedupe NX) AND entitlement_service
    (the window the settle grants), so a settle is observable end-to-end."""
    fr = FakeRedis()
    monkeypatch.setattr(bs, "redis_client", fr)
    monkeypatch.setattr(ent, "redis_client", fr)
    monkeypatch.setattr(ent, "_redis", lambda: fr)
    return fr


def _intent(db, user, *, tier="T1", crid="CR-1", status=INTENT_PENDING):
    t = get_tier(tier)
    intent = PaymentIntent(
        user_id=user.id, phone=user.phone, tier=tier,
        amount_kes=t.price_kes, status=status, checkout_request_id=crid,
    )
    db.add(intent)
    db.commit()
    db.refresh(intent)
    return intent


def _callback(crid, *, result_code=0, receipt="QGH7XYZ123", amount=20):
    """A parsed-callback dict in the shape mpesa_client.parse_callback returns."""
    return {
        "checkout_request_id": crid,
        "merchant_request_id": "MR-1",
        "result_code": result_code,
        "mpesa_receipt": receipt if result_code == 0 else None,
        "amount": amount if result_code == 0 else None,
        "phone": 254712345678,
    }


# --------------------------------------------------------------------------- #
#  settle: the happy path + the window it grants
# --------------------------------------------------------------------------- #
def test_settle_grants_window_and_writes_ledger(db, user, fake):
    intent = _intent(db, user, tier="T1", crid="CR-1")
    out = bs.settle_from_callback(db, _callback("CR-1", receipt="R1", amount=20))
    assert out == "granted"

    db.refresh(intent)
    assert intent.status == INTENT_PAID
    assert intent.mpesa_receipt == "R1"

    rows = db.query(PaymentLedger).filter_by(intent_id=intent.id).all()
    assert len(rows) == 1
    assert rows[0].mpesa_receipt == "R1"
    assert rows[0].quota == get_tier("T1").quota

    # the window is live: the user can now reveal up to the tier quota
    st = ent.entitlement_status(user.id)
    assert st["active"] is True
    assert st["remaining"] == get_tier("T1").quota


# --------------------------------------------------------------------------- #
#  idempotency: duplicate callback never double-grants / double-writes
# --------------------------------------------------------------------------- #
def test_duplicate_callback_is_idempotent(db, user, fake):
    intent = _intent(db, user, tier="T1", crid="CR-1")
    cb = _callback("CR-1", receipt="DUP1", amount=20)

    assert bs.settle_from_callback(db, cb) == "granted"
    # exact same callback redelivered (Daraja at-least-once)
    assert bs.settle_from_callback(db, cb) == "duplicate"

    rows = db.query(PaymentLedger).filter_by(mpesa_receipt="DUP1").all()
    assert len(rows) == 1


def test_duplicate_settles_via_db_backstop_when_redis_misses(db, user, fake):
    """If the Redis NX dedupe is unavailable (key evicted / outage on the 2nd call),
    the UNIQUE(mpesa_receipt) on payment_ledger is the durable backstop."""
    intent = _intent(db, user, tier="T1", crid="CR-1")
    cb = _callback("CR-1", receipt="BACKSTOP1", amount=20)
    assert bs.settle_from_callback(db, cb) == "granted"

    # wipe the Redis dedupe key so the fast path no longer catches the replay;
    # the DB UNIQUE must still make it idempotent.
    fake._evict(bs._receipt_dedupe_key("BACKSTOP1"))
    assert bs.settle_from_callback(db, cb) == "duplicate"
    assert db.query(PaymentLedger).filter_by(mpesa_receipt="BACKSTOP1").count() == 1


# --------------------------------------------------------------------------- #
#  failure / mismatch / unknown paths
# --------------------------------------------------------------------------- #
def test_failed_callback_marks_intent_failed_no_window(db, user, fake):
    intent = _intent(db, user, tier="T1", crid="CR-1")
    # ResultCode 1032 = user cancelled the prompt
    out = bs.settle_from_callback(db, _callback("CR-1", result_code=1032))
    assert out == "failed"
    db.refresh(intent)
    assert intent.status == INTENT_FAILED
    assert db.query(PaymentLedger).count() == 0
    assert ent.entitlement_status(user.id)["active"] is False


def test_amount_mismatch_is_rejected(db, user, fake):
    # intent is T1 (20 KES) but the callback claims 1 KES paid → reject, no grant
    intent = _intent(db, user, tier="T1", crid="CR-1")
    out = bs.settle_from_callback(db, _callback("CR-1", receipt="X", amount=1))
    assert out == "amount_mismatch"
    assert db.query(PaymentLedger).count() == 0
    assert ent.entitlement_status(user.id)["active"] is False


def test_unknown_checkout_request_id_is_ignored(db, user, fake):
    out = bs.settle_from_callback(db, _callback("CR-DOES-NOT-EXIST", receipt="Z"))
    assert out == "unknown"
    assert db.query(PaymentLedger).count() == 0


# --------------------------------------------------------------------------- #
#  reconciliation sweep (lost callback)
# --------------------------------------------------------------------------- #
def test_reconcile_settles_when_query_confirms_payment(db, user, fake, monkeypatch):
    intent = _intent(db, user, tier="T2", crid="CR-Q")
    monkeypatch.setattr(mpesa_client, "stk_query",
                        lambda *, checkout_request_id: {"ResultCode": 0})
    out = bs.reconcile_intent(db, intent)
    assert out == "granted"
    db.refresh(intent)
    assert intent.status == INTENT_PAID
    # synthetic deterministic receipt keyed on the checkout id
    assert db.query(PaymentLedger).filter_by(mpesa_receipt="RC-CR-Q").count() == 1
    assert ent.entitlement_status(user.id)["remaining"] == get_tier("T2").quota


def test_reconcile_then_callback_does_not_double_grant(db, user, fake, monkeypatch):
    """The race that must not double-charge entitlement: reconciliation settles
    (synthetic receipt), then the real callback finally arrives with the real
    receipt. Different receipts, but the intent is already PAID — the second settle
    still produces exactly one live window (option-A replace) and the callback's
    ledger row is a *separate* settled record only if its receipt differs.

    We assert the user never ends up with more than the tier quota of reveals."""
    intent = _intent(db, user, tier="T1", crid="CR-RACE")
    monkeypatch.setattr(mpesa_client, "stk_query",
                        lambda *, checkout_request_id: {"ResultCode": 0})
    assert bs.reconcile_intent(db, intent) == "granted"

    # real callback lands afterwards with the genuine receipt
    bs.settle_from_callback(db, _callback("CR-RACE", receipt="REAL-RCPT", amount=20))

    # entitlement is a single window of the tier quota, never stacked
    st = ent.entitlement_status(user.id)
    assert st["remaining"] == get_tier("T1").quota


def test_reconcile_still_pending_when_query_not_paid(db, user, fake, monkeypatch):
    intent = _intent(db, user, tier="T1", crid="CR-P")
    monkeypatch.setattr(mpesa_client, "stk_query",
                        lambda *, checkout_request_id: {"ResultCode": 1037})
    assert bs.reconcile_intent(db, intent) == "still_pending"
    assert db.query(PaymentLedger).count() == 0


def test_reconcile_query_error_is_swallowed(db, user, fake, monkeypatch):
    intent = _intent(db, user, tier="T1", crid="CR-E")
    def boom(*, checkout_request_id):
        raise mpesa_client.MpesaError("network")
    monkeypatch.setattr(mpesa_client, "stk_query", boom)
    assert bs.reconcile_intent(db, intent) == "query_error"
