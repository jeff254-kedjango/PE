"""Flash Sales grid response schemas (§8 nationwide "crazy offer" grid).

The wire contract for the 3×2 "Flash Sales" grid under Quick Buys. Each item is the LEAN buyer view
of a flash-sale listing — opaque ids + seller-published fields + the flash price + the comparable
reference (for a "was X / now Y" strikethrough) + the derived discount percent + the window's expiry
+ one thumbnail. It carries NO POS internals (no stock_qty / intent_weight / is_active) and NO PII
(no buyer uuid, no seller contact) — the same minimal-contract discipline as the Quick Buys +
trending DTOs (S6). Money is integer minor units (cents), never a float (S9).
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class FlashSaleItem(BaseModel):
    id: str
    shop_id: str
    seller_id: str
    # Seller-published shop identity (not PII): display name + optional category slug.
    shop_name: str | None = None
    shop_category: str | None = None
    title: str
    # The crazy price the buyer pays while the window is open (integer cents, S9).
    flash_price_cents: int
    # The comparable-shop average captured at launch — the "normal" price the offer undercuts. The
    # client shows it struck through beside the flash price.
    reference_cents: int
    # Whole-percent discount vs the reference (derived, display-only): e.g. 90 for "10 KES vs 100".
    discount_percent: int
    currency: str
    # First non-video media URL for the card thumbnail, else None → the client shows an initials tile.
    thumbnail_url: str | None = None
    # When the window closes (ISO). The client shows the "expires in less than an hour" urgency.
    expires_at: datetime
    # Buyer-relative distance in metres — DISPLAY ONLY (flash sales are nationwide; distance never
    # filters or ranks). None when the caller supplied no location.
    distance_m: float | None = None
    # Always "fixed" for a flash sale (a bargain listing can't run one) — carried so the card reuses
    # the shared one-tap "buy now" path.
    pricing_mode: str


class FlashSalesResponse(BaseModel):
    # The nationwide slate, ranked by craziness (up to flash_sales_max). The client pages over these
    # in fixed page_size windows — no per-page refetch.
    items: list[FlashSaleItem]
    # How many items make one page (3×2 = 6) — the client's grid page size.
    page_size: int
