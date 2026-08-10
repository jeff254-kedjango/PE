"""Settlement/order schemas — request bodies and the order + event-chain response.

Money fields are integer cents (S9). Responses expose only opaque ids + the buyer's/seller's
own numbers (no PII, S6). The event list is the tamper-evident §7 ledger, surfaced so a party
can audit exactly how the price was reached.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# ----------------------------- requests -----------------------------

class OrderOpen(BaseModel):
    listing_id: str = Field(min_length=1)
    # Required for a bargain listing (the opening offer); ignored for a fixed listing (which
    # locks at the sticker price). Bounds are enforced server-side against the reference price.
    offer_cents: int | None = Field(default=None, ge=1)


class CounterBody(BaseModel):
    amount_cents: int = Field(ge=1)


# ----------------------------- responses -----------------------------

class OrderEventOut(BaseModel):
    seq: int
    event_type: str
    actor: str
    amount_cents: int | None = None
    prev_hash: str | None = None
    row_hash: str
    created_at: datetime


class OrderOut(BaseModel):
    id: str
    listing_id: str
    seller_id: str
    buyer_uuid: str
    pricing_mode: str
    status: str
    reference_price_cents: int
    locked_price_cents: int | None = None
    commission_cents: int | None = None
    current_offer_cents: int | None = None
    current_offer_by: str | None = None
    round_count: int
    rail_ref: str | None = None
    created_at: datetime


class OrderDetail(BaseModel):
    """An order plus its full hash-chained event log (parties only)."""
    order: OrderOut
    events: list[OrderEventOut]


class OrderPage(BaseModel):
    items: list[OrderOut]
    next_cursor: str | None = None


# ----------------------------- mappers -----------------------------

def to_order_out(order) -> OrderOut:
    return OrderOut(
        id=str(order.id),
        listing_id=str(order.listing_id),
        seller_id=str(order.seller_id),
        buyer_uuid=order.buyer_uuid,
        pricing_mode=order.pricing_mode,
        status=order.status,
        reference_price_cents=order.reference_price_cents,
        locked_price_cents=order.locked_price_cents,
        commission_cents=order.commission_cents,
        current_offer_cents=order.current_offer_cents,
        current_offer_by=order.current_offer_by,
        round_count=order.round_count,
        rail_ref=order.rail_ref,
        created_at=order.created_at,
    )


def to_event_out(event) -> OrderEventOut:
    return OrderEventOut(
        seq=event.seq,
        event_type=event.event_type,
        actor=event.actor,
        amount_cents=event.amount_cents,
        prev_hash=event.prev_hash,
        row_hash=event.row_hash,
        created_at=event.created_at,
    )
