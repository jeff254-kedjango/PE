"""Quick Buys grid response schemas (§8 Trade right-rail discovery grid).

The wire contract for the 3×3 "Quick Buys" grid. Each item is the LEAN buyer view of a listing —
opaque ids + seller-PUBLISHED fields (title, price, category) + one thumbnail + the buyer-relative
distance + the ``pricing_mode`` (so the client knows whether a one-tap "buy now" is possible or the
tap must open a negotiation) + a ``bucket`` tag for display/telemetry. It deliberately carries NO
POS internals (no stock_qty / intent_weight / is_active) and NO PII (no buyer uuid, no seller
contact) — same minimal-contract discipline as the trending + storefront DTOs (S6).
"""
from __future__ import annotations

from pydantic import BaseModel

# Which bucket an item came from — display/telemetry only (the client may badge "Near you" vs a
# discovery item; it never drives behaviour). "near" = within the near radius; "interest" = an
# outer item matched to the buyer's category affinity; "trending" = an outer backfill item.
BUCKET_NEAR = "near"
BUCKET_INTEREST = "interest"
BUCKET_TRENDING = "trending"


class QuickBuyItem(BaseModel):
    id: str
    shop_id: str
    seller_id: str
    # Seller-published shop identity (not PII): the shop's display name + optional category slug.
    shop_name: str | None = None
    shop_category: str | None = None
    title: str
    # Integer minor units (cents) — never a float (S9); the client formats with the currency.
    price_cents: int
    currency: str
    # First non-video media URL (an image to show as the card thumbnail), else None → the client
    # falls back to an initials tile. A video-only listing has no still here (the grid is not a
    # video surface — that is the ShopVideoStrip).
    thumbnail_url: str | None = None
    distance_m: float
    # "fixed" ⇒ the client's cart button opens+locks an order in one tap; "bargain" ⇒ the button
    # instead opens the storefront to negotiate (a bargain order needs an opening offer).
    pricing_mode: str
    # Provenance of this item — one of BUCKET_* above. Display/telemetry only.
    bucket: str


class QuickBuysResponse(BaseModel):
    # The composed, de-duplicated item list (up to quick_buys_max). The client pages over these in
    # fixed page_size windows — no per-page refetch.
    items: list[QuickBuyItem]
    # The near/outer boundary actually used (the caller's radius filter overrides the default but is
    # clamped) — surfaced so the client can label the split honestly.
    near_radius_m: float
    # How many items make one page (3×3) — the client's grid page size.
    page_size: int
