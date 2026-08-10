"""Trending rail response schemas (§8).

The wire contract for the queue of boosted PRODUCTS. Each card carries only opaque ids +
seller-PUBLISHED fields (title, price, category) + the ``property_uuid`` stitch key for the client's
InSAR Confirmed-badge fetch — NO PII (S6). ``visible_slots`` / ``slot_seconds`` / ``poll_seconds``
drive the client: it renders ``visible_slots`` cards, decays each over ``slot_seconds`` pulling the
next queued product into a freed slot, and re-polls every ``poll_seconds`` to refresh the queue.
"""
from __future__ import annotations

from pydantic import BaseModel

from PE.commerce.services.trending import Slate, TrendingCard


class TrendingProductCard(BaseModel):
    listing_id: str
    seller_id: str
    title: str
    # Integer minor units (cents) — never a float (S9); the client formats with the currency.
    price_cents: int
    currency: str
    # Trade category slug — the client maps it to the card color + icon; None ⇒ neutral.
    category: str | None = None
    # Stitch key for the InSAR Confirmed-shield badge (fetched concurrently client-side).
    property_uuid: str | None = None
    distance_m: float
    # Reach tier (mtaa|hustle|sovereign) — display only (the rail always labels these "Boosted").
    boost_tier: str
    # The PRODUCT's own lead image URL — the promoted card shows the item for sale when present;
    # None ⇒ the client falls back to the category tint/icon.
    image_url: str | None = None


class TrendingSlate(BaseModel):
    # The full ordered queue of boosted product cards in this locality.
    cards: list[TrendingProductCard]
    # How many cards the client shows at once (bounds the animated slots).
    visible_slots: int
    # Per-card lifetime (the client's per-slot decay timer). Always > 5 s; shrinks under contention.
    slot_seconds: int
    # The client's re-poll cadence AND the server cache TTL.
    poll_seconds: int
    # Opaque locality bucket key (the cache key; surfaced for debugging/telemetry, not for display).
    bucket: str
    # Total boosted (servable) products in this locality (≥ visible_slots under contention).
    active_count: int


def _to_card(card: TrendingCard) -> TrendingProductCard:
    return TrendingProductCard(
        listing_id=card.listing_id,
        seller_id=card.seller_id,
        title=card.title,
        price_cents=card.price_cents,
        currency=card.currency,
        category=card.category,
        property_uuid=card.property_uuid,
        distance_m=card.distance_m,
        boost_tier=card.boost_tier,
        image_url=card.image_url,
    )


def to_trending_slate(slate: Slate) -> TrendingSlate:
    return TrendingSlate(
        cards=[_to_card(c) for c in slate.cards],
        visible_slots=slate.visible_slots,
        slot_seconds=slate.slot_seconds,
        poll_seconds=slate.poll_seconds,
        bucket=slate.bucket,
        active_count=slate.active_count,
    )
