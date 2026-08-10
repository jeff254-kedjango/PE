"""Settlement service — the server-authoritative order/bargain state machine + 3% ledger.

This is the trust core (§7). The CLIENT sends intents (open, offer, counter, accept, settle);
the SERVER owns every transition and the final number. The client never computes a price.

Guarantees enforced here:
  * **Compare-and-swap** every transition: ``UPDATE orders SET ... WHERE id=? AND version=?``.
    Two racing accepts (or accept-vs-expire) → exactly one bumps the version and wins; the
    loser's UPDATE matches 0 rows → ``ConflictError`` (router → 409).
  * **accept is the SOLE writer of locked_price_cents** — the sacred settlement input.
  * **3% is integer-floored** from the locked price only: ``locked * bps // 10_000``.
  * **Append-only hash chain**: every transition appends an ``OrderEvent`` whose ``row_hash``
    covers the prior ``prev_hash`` — tamper-evident, dispute-resolving.
  * **Bounds**: offers ∈ [1, max_multiple × reference]; counters capped at ``bargain_max_rounds``;
    one open negotiation per (buyer, listing).

Return convention: services return the Order (or raise a typed error); the router maps errors
to status codes. ``None`` is never used for "conflict"/"forbidden" — those are explicit raises
so a money path can't silently mis-handle them.
"""
from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import tuple_, update
from sqlalchemy.orm import Session

from PE.commerce.core.config import settings
from PE.commerce.models.listing import Listing
from PE.commerce.models.order import (
    OPEN_STATUSES,
    STATUS_CANCELLED,
    STATUS_COUNTERED,
    STATUS_EXPIRED,
    STATUS_OFFERED,
    STATUS_PRICE_LOCKED,
    STATUS_SETTLED,
    STATUS_SETTLEMENT_FAILED,
    STATUS_SETTLING,
    IdempotencyKey,
    Order,
    OrderEvent,
)
from PE.commerce.models.seller import Seller
from PE.commerce.services import flash_sales, payment_rail, receipts


# ----------------------------- typed errors (router maps to status codes) -----------------------------

class SettlementError(Exception):
    """Base for settlement errors carrying an HTTP status + message."""
    status_code = 400

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class NotFoundError(SettlementError):
    status_code = 404


class ConflictError(SettlementError):
    """A CAS transition lost the race, or an invariant (one-open-per-pair) is violated."""
    status_code = 409


class ForbiddenError(SettlementError):
    status_code = 403


class ValidationError(SettlementError):
    status_code = 422


# ----------------------------- pure money math -----------------------------

def commission_cents(locked_price_cents: int) -> int:
    """Our commission on a locked price: ``locked * bps // 10_000`` — integer floor, never a
    float (S9). Deterministic: 300 bps of 100 → 3; of 999 → 29; of 1 → 0."""
    return locked_price_cents * settings.commission_bps // 10_000


# ----------------------------- hash-chained event log -----------------------------

def _event_hash(order_id: str, seq: int, event_type: str, actor_uuid: str | None,
                amount_cents: int | None, prev_hash: str | None) -> str:
    """SHA-256 over the event's canonical content + the previous row's hash. Any edit/delete of
    a past event breaks every subsequent hash (tamper-evident, §7)."""
    canonical = "|".join([
        order_id,
        str(seq),
        event_type,
        actor_uuid or "",
        "" if amount_cents is None else str(amount_cents),
        prev_hash or "",
    ])
    return hashlib.sha256(canonical.encode()).hexdigest()


def _append_event(db: Session, order: Order, *, event_type: str, actor: str,
                  actor_uuid: str | None, amount_cents: int | None) -> OrderEvent:
    """Append one immutable event to the order's chain. seq is the next per-order index; the
    prev_hash is the last event's row_hash (None for the genesis event)."""
    last = (
        db.query(OrderEvent)
        .filter(OrderEvent.order_id == order.id)
        .order_by(OrderEvent.seq.desc())
        .first()
    )
    seq = 0 if last is None else last.seq + 1
    prev_hash = None if last is None else last.row_hash
    row_hash = _event_hash(order.id, seq, event_type, actor_uuid, amount_cents, prev_hash)
    event = OrderEvent(
        order_id=order.id,
        seq=seq,
        event_type=event_type,
        actor=actor,
        actor_uuid=actor_uuid,
        amount_cents=amount_cents,
        prev_hash=prev_hash,
        row_hash=row_hash,
    )
    db.add(event)
    # Flush immediately so a second append in the SAME transaction (e.g. open+lock, or
    # settle_record+settle_ok) sees this row when computing the next seq and prev_hash. Without
    # this, SQLAlchemy 2.0 batches both inserts into one insertmanyvalues statement on Postgres
    # and both rows compute seq=0 → unique-violation on (order_id, seq). Each link of the
    # tamper-evident chain must be persisted before the next is built.
    db.flush()
    return event


# ----------------------------- compare-and-swap helper -----------------------------

def _cas(db: Session, order: Order, *, expected_status: str, expected_version: int,
         changes: dict) -> None:
    """Apply ``changes`` to the order ONLY IF its (status, version) still match what we read —
    an optimistic UPDATE ... WHERE. Bumps version. Raises ConflictError if 0 rows matched (a
    concurrent transition won). Refreshes the in-memory object on success."""
    changes = {**changes, "version": expected_version + 1}
    result = db.execute(
        update(Order)
        .where(
            Order.id == order.id,
            Order.status == expected_status,
            Order.version == expected_version,
        )
        .values(**changes)
    )
    if result.rowcount != 1:
        db.rollback()
        raise ConflictError("Order changed concurrently; reload and retry")
    # Reflect the change on the in-memory instance for event-append + return.
    for k, v in changes.items():
        setattr(order, k, v)


# ----------------------------- idempotency -----------------------------

def _replay(db: Session, scope: str, idem_key: str) -> str | None:
    """If this (scope, key) was already processed, return the order_id it produced (replay).
    Else None (first time)."""
    row = (
        db.query(IdempotencyKey)
        .filter(IdempotencyKey.scope == scope, IdempotencyKey.idem_key == idem_key)
        .one_or_none()
    )
    return row.order_id if row else None


def _record_idem(db: Session, scope: str, idem_key: str, order_id: str, status_code: int) -> None:
    """Persist the outcome of a state-changing request so a retry replays it. Caller commits."""
    db.add(IdempotencyKey(
        scope=scope, idem_key=idem_key, order_id=order_id, status_code=status_code,
    ))


# ----------------------------- helpers -----------------------------

def _active_listing(db: Session, listing_id: str) -> Listing | None:
    return (
        db.query(Listing)
        .filter(Listing.id == listing_id, Listing.is_active.is_(True))
        .one_or_none()
    )


def _seller_for(db: Session, user_uuid: str) -> Seller | None:
    return db.query(Seller).filter(Seller.user_uuid == user_uuid).one_or_none()


def get_order(db: Session, order_id: str) -> Order | None:
    return db.query(Order).filter(Order.id == order_id).one_or_none()


def order_events(db: Session, order_id: str) -> list[OrderEvent]:
    return (
        db.query(OrderEvent)
        .filter(OrderEvent.order_id == order_id)
        .order_by(OrderEvent.seq.asc())
        .all()
    )


def _encode_cursor(created_at: datetime, row_id: str) -> str:
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{row_id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, str] | None:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts_str, row_id = raw.split("|", 1)
        return datetime.fromisoformat(ts_str), row_id
    except (ValueError, TypeError):
        return None


def list_my_orders(db: Session, user_uuid: str, *, as_seller: bool = False,
                   cursor: str | None = None, limit: int = 20) -> tuple[list[Order], str | None]:
    """The caller's orders newest-first, keyset-paginated. ``as_seller`` lists orders on the
    caller's shops (their sales) instead of orders they placed as a buyer. A seller view with no
    Seller row yields an empty page (never sold)."""
    if as_seller:
        seller = _seller_for(db, user_uuid)
        if seller is None:
            return [], None
        q = db.query(Order).filter(Order.seller_id == seller.id)
    else:
        q = db.query(Order).filter(Order.buyer_uuid == user_uuid)

    anchor = _decode_cursor(cursor) if cursor else None
    if anchor is not None:
        ts, rid = anchor
        q = q.filter(tuple_(Order.created_at, Order.id) < (ts, rid))
    rows = (
        q.order_by(Order.created_at.desc(), Order.id.desc())
        .limit(limit + 1)
        .all()
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = None
    if has_more and page:
        next_cursor = _encode_cursor(page[-1].created_at, page[-1].id)
    return page, next_cursor


def _party_role(order: Order, user_uuid: str, seller: Seller | None) -> str | None:
    """Which side the caller is on for this order: 'buyer', 'seller', or None (not a party)."""
    if order.buyer_uuid == user_uuid:
        return "buyer"
    if seller is not None and order.seller_id == seller.id:
        return "seller"
    return None


def _validate_offer(amount_cents: int, reference_price_cents: int) -> None:
    """Bounds-check a bargain number (§7): ≥ 1 cent and ≤ max_multiple × reference. Rejects
    negative / fat-finger / fraud offers."""
    if amount_cents < 1:
        raise ValidationError("Offer must be at least 1 cent")
    ceiling = int(reference_price_cents * settings.bargain_max_multiple)
    if amount_cents > ceiling:
        raise ValidationError(
            f"Offer {amount_cents} exceeds the maximum {ceiling} "
            f"({settings.bargain_max_multiple}× the reference price)"
        )


# ----------------------------- transitions -----------------------------

def open_order(db: Session, buyer_uuid: str, listing_id: str, *, offer_cents: int | None,
               idem_key: str) -> Order:
    """Open an order on a listing. Fixed-price → locks immediately at the list price. Bargain →
    opens a negotiation at the buyer's first offer (bounds-checked). Idempotent on idem_key."""
    scope = f"open:{buyer_uuid}"
    replayed = _replay(db, scope, idem_key)
    if replayed:
        existing = get_order(db, replayed)
        if existing:
            return existing

    listing = _active_listing(db, listing_id)
    if listing is None:
        raise NotFoundError("Listing not found")

    # One open negotiation per (buyer, listing) — service guard (the prod partial-unique index is
    # the hard backstop; this makes the SQLite test path correct too and yields a clean 409).
    open_existing = (
        db.query(Order)
        .filter(
            Order.buyer_uuid == buyer_uuid,
            Order.listing_id == listing_id,
            Order.status.in_(OPEN_STATUSES),
        )
        .first()
    )
    if open_existing is not None:
        raise ConflictError("You already have an open order on this listing")

    mode = listing.pricing_mode
    order = Order(
        listing_id=listing.id,
        seller_id=listing.seller_id,
        buyer_uuid=buyer_uuid,
        pricing_mode=mode,
        reference_price_cents=listing.price_cents,
        status="REQUESTED",
        version=0,
        round_count=0,
    )

    if mode == "fixed":
        # Fixed: lock immediately at the EFFECTIVE price. If a §8 flash-sale window is open right
        # now, the buyer pays the (lower) flash price; otherwise the list price. active_flash_price
        # is a pure read of the stored window, so a sale that later expires can't un-lock this
        # already-placed order (the amount is captured here), and the listing's normal price is
        # untouched throughout (it reverts by itself once the window closes).
        effective = flash_sales.active_flash_price(listing) or listing.price_cents
        order.status = STATUS_PRICE_LOCKED
        order.locked_price_cents = effective
        db.add(order)
        db.flush()
        _append_event(db, order, event_type="open", actor="buyer",
                      actor_uuid=buyer_uuid, amount_cents=effective)
        _append_event(db, order, event_type="lock", actor="system",
                      actor_uuid=None, amount_cents=effective)
    else:
        # Bargain: a first offer is required and bounds-checked; status OFFERED awaiting seller.
        if offer_cents is None:
            raise ValidationError("A bargain order requires an opening offer_cents")
        _validate_offer(offer_cents, listing.price_cents)
        order.status = STATUS_OFFERED
        order.current_offer_cents = offer_cents
        order.current_offer_by = "buyer"
        db.add(order)
        db.flush()
        _append_event(db, order, event_type="open", actor="buyer",
                      actor_uuid=buyer_uuid, amount_cents=offer_cents)
        _append_event(db, order, event_type="offer", actor="buyer",
                      actor_uuid=buyer_uuid, amount_cents=offer_cents)

    _record_idem(db, scope, idem_key, order.id, 201)
    db.commit()
    db.refresh(order)
    return order


def counter(db: Session, user_uuid: str, order_id: str, amount_cents: int, *,
            idem_key: str) -> Order:
    """A bargain counter-offer. Alternates buyer/seller, bounds-checked, capped at
    ``bargain_max_rounds``. CAS on the current pending status."""
    scope = f"counter:{user_uuid}:{order_id}"
    if _replay(db, scope, idem_key):
        return get_order(db, order_id)

    order = get_order(db, order_id)
    if order is None:
        raise NotFoundError("Order not found")
    seller = _seller_for(db, user_uuid)
    role = _party_role(order, user_uuid, seller)
    if role is None:
        raise NotFoundError("Order not found")  # non-party: no existence leak

    if order.status not in (STATUS_OFFERED, STATUS_COUNTERED):
        raise ConflictError(f"Cannot counter an order in state {order.status}")
    # The party to act is the one who did NOT make the current offer.
    if order.current_offer_by == role:
        raise ForbiddenError("It is the other party's turn")
    if order.round_count >= settings.bargain_max_rounds:
        raise ValidationError(
            f"Bargaining is capped at {settings.bargain_max_rounds} counters — accept or cancel"
        )
    _validate_offer(amount_cents, order.reference_price_cents)

    new_status = STATUS_COUNTERED if role == "seller" else STATUS_OFFERED
    _cas(db, order, expected_status=order.status, expected_version=order.version, changes={
        "status": new_status,
        "current_offer_cents": amount_cents,
        "current_offer_by": role,
        "round_count": order.round_count + 1,
    })
    _append_event(db, order, event_type="counter", actor=role,
                  actor_uuid=user_uuid, amount_cents=amount_cents)
    _record_idem(db, scope, idem_key, order.id, 200)
    db.commit()
    db.refresh(order)
    return order


def accept(db: Session, user_uuid: str, order_id: str, *, idem_key: str) -> Order:
    """Accept the current offer → PRICE_LOCKED. The accepting party locks the OTHER party's
    standing number. This is the ONLY writer of locked_price_cents."""
    scope = f"accept:{user_uuid}:{order_id}"
    if _replay(db, scope, idem_key):
        return get_order(db, order_id)

    order = get_order(db, order_id)
    if order is None:
        raise NotFoundError("Order not found")
    seller = _seller_for(db, user_uuid)
    role = _party_role(order, user_uuid, seller)
    if role is None:
        raise NotFoundError("Order not found")

    if order.status not in (STATUS_OFFERED, STATUS_COUNTERED):
        raise ConflictError(f"Cannot accept an order in state {order.status}")
    # You can only accept the OTHER party's offer, not your own standing one.
    if order.current_offer_by == role:
        raise ForbiddenError("You cannot accept your own offer; await the other party")

    locked = order.current_offer_cents
    _cas(db, order, expected_status=order.status, expected_version=order.version, changes={
        "status": STATUS_PRICE_LOCKED,
        "locked_price_cents": locked,
    })
    _append_event(db, order, event_type="accept", actor=role,
                  actor_uuid=user_uuid, amount_cents=locked)
    _append_event(db, order, event_type="lock", actor="system",
                  actor_uuid=None, amount_cents=locked)
    _record_idem(db, scope, idem_key, order.id, 200)
    db.commit()
    db.refresh(order)
    return order


def cancel(db: Session, user_uuid: str, order_id: str) -> Order:
    """Cancel a pending negotiation (either party). CAS pending→CANCELLED."""
    order = get_order(db, order_id)
    if order is None:
        raise NotFoundError("Order not found")
    seller = _seller_for(db, user_uuid)
    role = _party_role(order, user_uuid, seller)
    if role is None:
        raise NotFoundError("Order not found")
    if order.status not in OPEN_STATUSES:
        raise ConflictError(f"Cannot cancel an order in state {order.status}")

    _cas(db, order, expected_status=order.status, expected_version=order.version, changes={
        "status": STATUS_CANCELLED,
    })
    _append_event(db, order, event_type="cancel", actor=role,
                  actor_uuid=user_uuid, amount_cents=None)
    db.commit()
    db.refresh(order)
    return order


def settle(db: Session, user_uuid: str, order_id: str, *, idem_key: str) -> Order:
    """Record the 3% obligation and run the (stub) rail. CAS PRICE_LOCKED→SETTLING, compute
    commission from the SACRED locked price, call the rail, then →SETTLED or →SETTLEMENT_FAILED.
    Either party may trigger settlement of their locked order."""
    scope = f"settle:{user_uuid}:{order_id}"
    if _replay(db, scope, idem_key):
        return get_order(db, order_id)

    order = get_order(db, order_id)
    if order is None:
        raise NotFoundError("Order not found")
    seller = _seller_for(db, user_uuid)
    if _party_role(order, user_uuid, seller) is None:
        raise NotFoundError("Order not found")
    if order.status != STATUS_PRICE_LOCKED:
        raise ConflictError(f"Cannot settle an order in state {order.status}")

    # 3% from the sacred locked price — the ONLY settlement input.
    commission = commission_cents(order.locked_price_cents)
    _cas(db, order, expected_status=STATUS_PRICE_LOCKED, expected_version=order.version, changes={
        "status": STATUS_SETTLING,
        "commission_cents": commission,
    })
    _append_event(db, order, event_type="settle_record", actor="system",
                  actor_uuid=None, amount_cents=commission)

    rail = payment_rail.get_rail()
    obligation = rail.record_obligation(
        order_id=order.id, locked_price_cents=order.locked_price_cents, seller_id=order.seller_id,
    )
    commission_res = rail.collect_commission(order_id=order.id, commission_cents=commission)

    if obligation.ok and commission_res.ok:
        _cas(db, order, expected_status=STATUS_SETTLING, expected_version=order.version, changes={
            "status": STATUS_SETTLED,
            "rail_ref": obligation.ref,
        })
        settle_ok = _append_event(db, order, event_type="settle_ok", actor="system",
                                  actor_uuid=None, amount_cents=commission)
        # Issue the immutable digital receipt (§8) in THIS transaction — a receipt exists iff
        # the sale settled. Bound to the settle_ok event's row_hash (the order's chain tip) so
        # it is verifiable against the tamper-evident event log. Idempotent on re-settle.
        receipts.issue_receipt(db, order, chain_tip_hash=settle_ok.row_hash)
    else:
        _cas(db, order, expected_status=STATUS_SETTLING, expected_version=order.version, changes={
            "status": STATUS_SETTLEMENT_FAILED,
        })
        _append_event(db, order, event_type="settle_fail", actor="system",
                      actor_uuid=None, amount_cents=commission)

    _record_idem(db, scope, idem_key, order.id, 200)
    db.commit()
    db.refresh(order)
    return order


# ----------------------------- TTL sweep (run by the expiry_sweeper process) -----------------------------

def expire_stale(db: Session, *, now: datetime | None = None) -> int:
    """Expire pending negotiations older than the TTL. Returns the count actually expired.

    Run periodically by ``services.expiry_sweeper`` (a standalone process). Designed for safe
    concurrency with live user transitions:

      * **Each expiry commits independently.** A pending order a user accepts/cancels in the
        same instant loses the CAS race here — and ``_cas`` calls ``db.rollback()`` on a lost
        race, which rolls back the WHOLE session transaction. If the loop committed only once at
        the end, that single conflict would silently discard EVERY prior (successful) expiry in
        the batch. So we commit per order: each expiry is durable before the next is attempted,
        and a conflict rolls back only that one order's (empty) change. ``count`` is then the
        true number persisted.
      * **Conflicts are expected, not errors.** A lost CAS (concurrent accept/cancel) or a row
        already expired by an overlapping sweep is skipped — the user/other-sweep handled it.

    Re-reads the candidate ``(status, version)`` per row inside the loop is unnecessary: the CAS
    WHERE clause already guards on the version we read, so a stale read just yields a clean skip.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=settings.pending_ttl_seconds)
    stale = (
        db.query(Order)
        .filter(Order.status.in_(OPEN_STATUSES), Order.created_at < cutoff)
        .all()
    )
    count = 0
    for order in stale:
        try:
            _cas(db, order, expected_status=order.status, expected_version=order.version,
                 changes={"status": STATUS_EXPIRED})
        except ConflictError:
            # A concurrent transition (user accept/cancel, or an overlapping sweep) won. _cas
            # already rolled back this order's change; nothing of ours is pending. Skip it.
            continue
        _append_event(db, order, event_type="expire", actor="system",
                      actor_uuid=None, amount_cents=None)
        # Commit THIS expiry before moving on, so a conflict on a later order can never roll it
        # back. Per-order durability is the whole point of the sweep being concurrency-safe.
        db.commit()
        count += 1
    return count
