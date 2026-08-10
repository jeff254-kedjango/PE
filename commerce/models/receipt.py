"""Digital receipt — the commerce analogue of a locked price (architecture §8).

WHAT THIS IS. When an order reaches ``SETTLED`` the service issues exactly one immutable
``Receipt``: an append-only, tamper-evident record of a completed sale, generated *between*
the buyer and seller. It is the receipt a normal e-commerce flow emails you — here it is a
first-class, auditable row, written once and never updated.

WHY A SEPARATE TABLE (not just the OrderEvent chain). The §7 ``OrderEvent`` log records *how
the price was reached*; a receipt is the *settled fact of the sale*, with a frozen snapshot
of the parties + the item + the money split. Two reasons it stands alone:
  * **Snapshot, not a live join.** ``listing_title``/``currency`` are copied at issue time so a
    later listing edit or delete can never mutate a historical receipt — the receipt is what
    was true at sale.
  * **Money is split here once.** ``gross`` = the sacred locked price, ``commission`` = the 3%
    already recorded by settle, ``net_to_seller`` = gross − commission. Integer cents only (S9).

TAMPER-EVIDENCE (same discipline as OrderEvent / weespas NotificationAudit).
  * ``chain_tip_hash`` binds the receipt to the order's hash chain — it is the ``row_hash`` of
    the ``settle_ok`` event that authorized issuance. A receipt can therefore be verified
    against the (independently tamper-evident) event chain it claims to settle.
  * ``receipt_hash`` is a SHA-256 over the receipt's own canonical content + that chain tip, so
    any edit to the snapshot is detectable. The service NEVER updates or deletes a Receipt row;
    a DB-level append-only trigger is the same documented follow-up as for OrderEvent.

One receipt per order is enforced by a UNIQUE constraint on ``order_id`` — the hard backstop
behind the service's idempotent issuance (a replayed settle never issues a second receipt).
"""
import uuid

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from PE.commerce.core.database import Base, utcnow


class Receipt(Base):
    """One immutable record of a completed (SETTLED) sale, issued once per order."""
    __tablename__ = "receipts"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    # Exactly one receipt per order (UNIQUE) — the backstop behind idempotent issuance.
    order_id = Column(String, ForeignKey("orders.id"), nullable=False, index=True)

    # Parties (synchronized UUIDs / opaque ids — never cross-DB FKs, doc §3).
    buyer_uuid = Column(String, nullable=False, index=True)
    seller_id = Column(String, ForeignKey("sellers.id"), nullable=False, index=True)
    listing_id = Column(String, ForeignKey("listings.id"), nullable=False, index=True)

    # Frozen snapshot of the item at sale time — survives later listing edits/deletes.
    listing_title = Column(String(200), nullable=False)
    currency = Column(String(3), nullable=False)

    # Money — integer cents (S9). gross = sacred locked price; commission = 3% already recorded
    # by settle; net_to_seller = gross - commission (party-direct: the seller's share).
    gross_cents = Column(Integer, nullable=False)
    commission_cents = Column(Integer, nullable=False)
    net_to_seller_cents = Column(Integer, nullable=False)

    # The (stub) rail reference from settle — where the real Daraja ref will live later.
    rail_ref = Column(String(64), nullable=True)

    # Tamper-evidence: the order's settle_ok event row_hash (chain binding) + this receipt's hash.
    chain_tip_hash = Column(String(64), nullable=False)
    receipt_hash = Column(String(64), nullable=False)

    # Python-side default (microsecond, tz-aware) so the keyset cursor round-trips on SQLite;
    # server_default is the DB-side fallback. See core.database.utcnow.
    issued_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now(), index=True)

    __table_args__ = (
        UniqueConstraint("order_id", name="uq_receipt_order"),
        UniqueConstraint("receipt_hash", name="uq_receipt_hash"),
        # Keyset read paths: a party lists their own receipts newest-first (issued_at, id).
        Index("ix_receipts_buyer_issued", "buyer_uuid", "issued_at"),
        Index("ix_receipts_seller_issued", "seller_id", "issued_at"),
    )
