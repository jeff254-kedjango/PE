"""Settlement models — the order/negotiation aggregate, its append-only hash-chained event
log, and the idempotency-key store (architecture §6/§7).

THE LEDGER, NOT THE RAIL. These tables record the obligation atomically in OUR database — the
3% commission is computed from a *sacred* locked price and nothing downstream re-opens it. No
real M-Pesa money moves here (the doc forbids live rail code before Daraja research — §6 ⚠);
``services.payment_rail.StubRail`` is the only rail this increment.

Design pillars (all enforced in services.settlement):
  * **Server is the sole price authority** — the client sends intents; the server owns every
    transition and the final number.
  * **Compare-and-swap transitions** — ``Order.version`` is bumped under
    ``UPDATE ... WHERE id=? AND version=?`` so two racing accepts resolve to one winner + one
    clean 409 (same discipline as the shipped first-wins-seen flag-review row).
  * **Append-only, tamper-evident log** — ``OrderEvent`` carries ``prev_hash`` + ``row_hash``
    (SHA-256), mirroring weespas ``NotificationAudit``. Disputes resolve from the chain.
  * **Idempotency** — every state-changing request carries a key; ``IdempotencyKey`` stores the
    first outcome so a retry replays it instead of re-running the transition.
"""
import uuid

from sqlalchemy import (
    BigInteger,
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


# ---- Order status constants (the §7 machine, commerce subset — no ride/dispatch states) ----
STATUS_REQUESTED = "REQUESTED"        # transient: opening
STATUS_OFFERED = "OFFERED"            # bargain: buyer's offer stands, awaiting seller
STATUS_COUNTERED = "COUNTERED"        # bargain: seller's counter stands, awaiting buyer
STATUS_PRICE_LOCKED = "PRICE_LOCKED"  # the sacred price is set; ready to settle
STATUS_SETTLING = "SETTLING"          # commission recorded, rail in progress
STATUS_SETTLED = "SETTLED"            # done
STATUS_SETTLEMENT_FAILED = "SETTLEMENT_FAILED"  # rail failed; retry/dispute
STATUS_EXPIRED = "EXPIRED"            # a pending negotiation timed out
STATUS_CANCELLED = "CANCELLED"        # a party cancelled a pending negotiation

# Statuses in which a negotiation is "open" — used to enforce one-open-per-(buyer, listing).
OPEN_STATUSES = (STATUS_REQUESTED, STATUS_OFFERED, STATUS_COUNTERED)
# Terminal statuses (no further transitions).
TERMINAL_STATUSES = (
    STATUS_SETTLED,
    STATUS_EXPIRED,
    STATUS_CANCELLED,
)


class Order(Base):
    """One buyer's attempt to transact a listing — the negotiation + settlement aggregate."""
    __tablename__ = "orders"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    listing_id = Column(String, ForeignKey("listings.id"), nullable=False, index=True)
    # Denormalized recipient so seller-side queries never join listings.
    seller_id = Column(String, ForeignKey("sellers.id"), nullable=False, index=True)
    # The buyer (weespas user id / token sub). NOT a cross-DB FK (doc §3).
    buyer_uuid = Column(String, nullable=False, index=True)

    pricing_mode = Column(String(16), nullable=False)   # "fixed" | "bargain" (from the listing)
    status = Column(String(24), nullable=False, index=True)

    # Money — all integer cents (S9). reference_price = the listing price at open (the metered
    # reference offers are bounded around). locked_price = the sacred settlement input, written
    # ONLY by accept/lock. commission = 3% of locked, written ONLY at settle.
    reference_price_cents = Column(Integer, nullable=False)
    locked_price_cents = Column(Integer, nullable=True)
    commission_cents = Column(Integer, nullable=True)

    # Bargain bookkeeping. current_offer_* = the number on the table + who put it there; the
    # OTHER party accepts it. round_count = counters used (capped — §7 "≤ 3 rounds").
    current_offer_cents = Column(Integer, nullable=True)
    current_offer_by = Column(String(8), nullable=True)   # "buyer" | "seller"
    round_count = Column(Integer, nullable=False, default=0, server_default="0")

    # Optimistic-concurrency version — bumped on every transition under a CAS WHERE clause.
    version = Column(Integer, nullable=False, default=0, server_default="0")

    # A synthetic rail reference returned by the (stub) payment rail at settle — proves where a
    # real Daraja ref will live without moving money.
    rail_ref = Column(String(64), nullable=True)

    # Python-side default (microsecond, tz-aware) so list_my_orders' keyset cursor round-trips
    # on SQLite; server_default is the DB-side fallback. See core.database.utcnow.
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_orders_buyer_status", "buyer_uuid", "status"),
        Index("ix_orders_seller_status", "seller_id", "status"),
        # One OPEN negotiation per (buyer, listing): a Postgres PARTIAL unique index (only over
        # open statuses). Created via raw DDL in the additive migration because SQLAlchemy's
        # portable Index can't express the WHERE on SQLite; the service also guards it so the
        # invariant holds on the SQLite test path. See core/database._ADDITIVE_DDL.
    )


class OrderEvent(Base):
    """Immutable, hash-chained record of one transition in an order's life (§7 append-only
    ledger). The service NEVER updates or deletes a row here; a DB-level append-only trigger is
    a documented follow-up (a real Alembic migration), as with weespas NotificationAudit."""
    __tablename__ = "order_events"

    # BIGINT on Postgres (the real target — an append-only log can grow large); plain INTEGER on
    # SQLite, because SQLite only auto-increments a rowid-aliased INTEGER PRIMARY KEY, not BIGINT
    # (a BIGINT pk stays NULL on insert). with_variant gives each dialect the right type.
    id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    order_id = Column(String, ForeignKey("orders.id"), nullable=False, index=True)
    seq = Column(Integer, nullable=False)  # per-order monotonic sequence (0,1,2,…)

    # open | offer | counter | accept | lock | settle_record | settle_ok | settle_fail |
    # cancel | expire
    event_type = Column(String(16), nullable=False)
    actor = Column(String(8), nullable=False)         # buyer | seller | system
    actor_uuid = Column(String, nullable=True)        # who acted (null for system)
    amount_cents = Column(Integer, nullable=True)     # the number this event carries, if any

    prev_hash = Column(String(64), nullable=True)     # SHA-256 of the previous event's row_hash
    row_hash = Column(String(64), nullable=False)     # SHA-256 over this event's canonical content
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("order_id", "seq", name="uq_order_event_seq"),
        UniqueConstraint("row_hash", name="uq_order_event_row_hash"),
        Index("ix_order_events_order_seq", "order_id", "seq"),
    )


class IdempotencyKey(Base):
    """Stores the outcome of a state-changing request so a retried request (same key) replays
    the first result rather than re-running the transition. A double-tap on a flaky KE mobile
    network can never create two orders or double-accept."""
    __tablename__ = "idempotency_keys"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    # scope namespaces keys by action+actor+order so one user's key can't collide with another's.
    # MUST hold the longest builder, "settle:{user_uuid}:{order_id}" — with real UUID-length ids
    # that is 7 + 36 + 1 + 36 = 80 chars, so String(64) overflowed on Postgres (caught by the
    # live e2e; SQLite ignores VARCHAR length so unit tests never saw it). 255 leaves headroom.
    scope = Column(String(255), nullable=False)
    idem_key = Column(String(128), nullable=False)
    order_id = Column(String, nullable=True)          # the order the first call produced
    status_code = Column(Integer, nullable=False)     # the HTTP status the first call returned
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("scope", "idem_key", name="uq_idempotency_scope_key"),
    )
