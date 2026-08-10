"""Billing tables for the listing-location access model (PE/billing_architecture.md §3).

Two tables, deliberately separate:

  * payment_intent  — MUTABLE checkout state (pending → paid / failed / expired).
    One row per STK Push attempt; the join key to the M-Pesa callback is
    `checkout_request_id`.

  * payment_ledger  — APPEND-ONLY record of SETTLED money. One row per successful
    payment, keyed UNIQUE on the M-Pesa receipt — that uniqueness is the
    idempotency anchor: a replayed/duplicate callback can never mint a second
    window or a second ledger row. Same discipline as notification_audit (P4a).

No money is held (no wallet/balance) — a window is granted in Redis the moment a
payment settles; the ledger is the durable audit of what was paid and granted.
"""
from __future__ import annotations

import uuid

from sqlalchemy import (
    Column, String, Integer, BigInteger, DateTime, ForeignKey, Index,
)
from sqlalchemy.sql import func

from PE.weespas.core.database import Base


# Intent lifecycle states.
INTENT_PENDING = "pending"
INTENT_PAID = "paid"
INTENT_FAILED = "failed"
INTENT_EXPIRED = "expired"


class PaymentIntent(Base):
    """One checkout attempt. Created at STK initiate; updated by the callback or the
    reconciliation sweep. `checkout_request_id` is Daraja's id we match the callback on."""
    __tablename__ = "payment_intent"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    phone = Column(String(20), nullable=False)        # MSISDN the STK went to
    tier = Column(String(8), nullable=False)          # T1 / T2 / T3
    amount_kes = Column(Integer, nullable=False)      # price at purchase time (audit)

    merchant_request_id = Column(String(64), nullable=True, index=True)
    checkout_request_id = Column(String(64), nullable=True, unique=True, index=True)

    status = Column(String(16), nullable=False, default=INTENT_PENDING, index=True)
    mpesa_receipt = Column(String(32), nullable=True, unique=True)
    result_code = Column(Integer, nullable=True)      # Daraja ResultCode (0 = success)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now())

    __table_args__ = (
        Index("idx_payment_intent_user_status", "user_id", "status"),
    )


class PaymentLedger(Base):
    """Append-only record of a settled payment + what it granted. UNIQUE mpesa_receipt
    is the idempotency key. Never UPDATE/DELETE (optionally enforced by a trigger, like
    notification_audit)."""
    __tablename__ = "payment_ledger"

    # BigInteger on Postgres (the real target); SQLite only autoincrements a plain
    # INTEGER PRIMARY KEY, so fall back to Integer there (tests / dev).
    id = Column(BigInteger().with_variant(Integer, "sqlite"),
                primary_key=True, autoincrement=True)
    intent_id = Column(String, ForeignKey("payment_intent.id", ondelete="RESTRICT"),
                       nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    mpesa_receipt = Column(String(32), nullable=False, unique=True)  # idempotency anchor
    amount_kes = Column(Integer, nullable=False)
    tier = Column(String(8), nullable=False)
    quota = Column(Integer, nullable=False)           # what was granted (audit)
    window_seconds = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
