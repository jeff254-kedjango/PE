"""Digital-receipt schemas (§8).

A receipt exposes only opaque ids + the parties' own numbers and the frozen item snapshot —
no PII (S6). ``net_to_seller_cents`` makes the party-direct split explicit: the buyer pays
``gross_cents`` directly to the seller, of which the seller keeps ``net_to_seller_cents`` and
our ``commission_cents`` is the 3% on the second rail. ``chain_tip_hash`` + ``receipt_hash``
let a client verify the receipt against the order's tamper-evident event chain.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ReceiptOut(BaseModel):
    id: str
    order_id: str
    buyer_uuid: str
    seller_id: str
    listing_id: str
    listing_title: str
    currency: str
    gross_cents: int
    commission_cents: int
    net_to_seller_cents: int
    rail_ref: str | None = None
    chain_tip_hash: str
    receipt_hash: str
    issued_at: datetime


class ReceiptPage(BaseModel):
    items: list[ReceiptOut]
    next_cursor: str | None = None


def to_receipt_out(receipt) -> ReceiptOut:
    return ReceiptOut(
        id=str(receipt.id),
        order_id=str(receipt.order_id),
        buyer_uuid=receipt.buyer_uuid,
        seller_id=str(receipt.seller_id),
        listing_id=str(receipt.listing_id),
        listing_title=receipt.listing_title,
        currency=receipt.currency,
        gross_cents=receipt.gross_cents,
        commission_cents=receipt.commission_cents,
        net_to_seller_cents=receipt.net_to_seller_cents,
        rail_ref=receipt.rail_ref,
        chain_tip_hash=receipt.chain_tip_hash,
        receipt_hash=receipt.receipt_hash,
        issued_at=receipt.issued_at,
    )
