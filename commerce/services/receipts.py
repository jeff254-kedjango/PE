"""Receipts service — issue + read the immutable digital receipt of a settled sale (§8).

Issuance is driven by settlement, not the client: ``issue_receipt`` is called inside the
``settle()`` success branch, in the SAME transaction as the ``SETTLED`` transition, so a
receipt exists iff the sale settled (atomic — they commit or roll back together).

Guarantees:
  * **Exactly once per order.** Issuance is idempotent: if a receipt already exists for the
    order (a replayed settle, or a belt-and-braces re-call) it is returned unchanged, never
    duplicated. The UNIQUE(order_id) constraint is the hard backstop.
  * **Snapshot, not a join.** The listing title/currency are frozen at issue time; a later
    listing edit/delete cannot rewrite history. If the listing was already removed, we still
    issue (a sale that happened must be receiptable) with a neutral placeholder title.
  * **Money is split here once** from the order's SACRED locked price:
    net_to_seller = gross − commission. Integer cents only (S9); never recomputed elsewhere.
  * **Tamper-evident.** receipt_hash = SHA-256 over the receipt's canonical content + the
    order's settle_ok chain tip, binding the receipt to the (independently verifiable) event
    chain. Reads are keyset-paginated, O(log n + k).
"""
from __future__ import annotations

import base64
import hashlib
from datetime import datetime

from sqlalchemy import tuple_
from sqlalchemy.orm import Session

from PE.commerce.models.listing import Listing
from PE.commerce.models.receipt import Receipt
from PE.commerce.models.seller import Seller


# A sale that settled must always be receiptable; if the listing was removed between sale and
# issuance, snapshot a neutral title rather than fail the receipt.
_REMOVED_LISTING_TITLE = "(item no longer listed)"


def _receipt_hash(*, order_id: str, buyer_uuid: str, seller_id: str, listing_id: str,
                  gross_cents: int, commission_cents: int, net_to_seller_cents: int,
                  rail_ref: str | None, chain_tip_hash: str) -> str:
    """SHA-256 over the receipt's canonical content + the order's settle_ok chain tip. Any edit
    to a snapshot field (or a forged chain tip) changes the hash — tamper-evident (§8)."""
    canonical = "|".join([
        order_id,
        buyer_uuid,
        seller_id,
        listing_id,
        str(gross_cents),
        str(commission_cents),
        str(net_to_seller_cents),
        rail_ref or "",
        chain_tip_hash,
    ])
    return hashlib.sha256(canonical.encode()).hexdigest()


def get_by_order(db: Session, order_id: str) -> Receipt | None:
    return db.query(Receipt).filter(Receipt.order_id == order_id).one_or_none()


def issue_receipt(db: Session, order, *, chain_tip_hash: str) -> Receipt:
    """Issue the receipt for a freshly-SETTLED order. Idempotent: returns the existing receipt
    if one is already on file (replayed settle), never a duplicate. Adds to the session but does
    NOT commit — the caller (settle) owns the transaction so issuance is atomic with SETTLED.

    ``order`` must carry the sacred ``locked_price_cents`` and the ``commission_cents`` recorded
    by settle; ``chain_tip_hash`` is the row_hash of the order's ``settle_ok`` event.
    """
    existing = get_by_order(db, order.id)
    if existing is not None:
        return existing

    gross = order.locked_price_cents
    commission = order.commission_cents
    # Defensive: settle always sets both before issuance. If either were missing we cannot honor
    # the "net = gross − commission" invariant, so refuse rather than write a wrong receipt.
    if gross is None or commission is None:
        raise ValueError("cannot issue a receipt before locked price and commission are set")
    net = gross - commission

    listing = db.query(Listing).filter(Listing.id == order.listing_id).one_or_none()
    title = listing.title if listing is not None else _REMOVED_LISTING_TITLE
    currency = listing.currency if listing is not None else "KES"

    receipt = Receipt(
        order_id=order.id,
        buyer_uuid=order.buyer_uuid,
        seller_id=order.seller_id,
        listing_id=order.listing_id,
        listing_title=title,
        currency=currency,
        gross_cents=gross,
        commission_cents=commission,
        net_to_seller_cents=net,
        rail_ref=order.rail_ref,
        chain_tip_hash=chain_tip_hash,
        receipt_hash=_receipt_hash(
            order_id=order.id,
            buyer_uuid=order.buyer_uuid,
            seller_id=order.seller_id,
            listing_id=order.listing_id,
            gross_cents=gross,
            commission_cents=commission,
            net_to_seller_cents=net,
            rail_ref=order.rail_ref,
            chain_tip_hash=chain_tip_hash,
        ),
    )
    db.add(receipt)
    db.flush()  # surface a UNIQUE(order_id) race as an IntegrityError inside settle's txn
    return receipt


# ----------------------------- reads -----------------------------

def _seller_for(db: Session, user_uuid: str) -> Seller | None:
    return db.query(Seller).filter(Seller.user_uuid == user_uuid).one_or_none()


def _encode_cursor(issued_at: datetime, row_id: str) -> str:
    return base64.urlsafe_b64encode(f"{issued_at.isoformat()}|{row_id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, str] | None:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts_str, row_id = raw.split("|", 1)
        return datetime.fromisoformat(ts_str), row_id
    except (ValueError, TypeError):
        return None


def list_my_receipts(db: Session, user_uuid: str, *, as_seller: bool = False,
                     cursor: str | None = None, limit: int = 20) -> tuple[list[Receipt], str | None]:
    """The caller's receipts newest-first, keyset-paginated (issued_at, id). ``as_seller`` lists
    receipts for the caller's sales; a seller view with no Seller row yields an empty page."""
    if as_seller:
        seller = _seller_for(db, user_uuid)
        if seller is None:
            return [], None
        q = db.query(Receipt).filter(Receipt.seller_id == seller.id)
    else:
        q = db.query(Receipt).filter(Receipt.buyer_uuid == user_uuid)

    anchor = _decode_cursor(cursor) if cursor else None
    if anchor is not None:
        ts, rid = anchor
        q = q.filter(tuple_(Receipt.issued_at, Receipt.id) < (ts, rid))
    rows = (
        q.order_by(Receipt.issued_at.desc(), Receipt.id.desc())
        .limit(limit + 1)
        .all()
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = None
    if has_more and page:
        next_cursor = _encode_cursor(page[-1].issued_at, page[-1].id)
    return page, next_cursor
