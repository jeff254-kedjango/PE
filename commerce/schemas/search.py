"""Global trade-search response schemas.

The wire contract for the navbar's trade search results. Each result carries only opaque ids +
seller-published display fields + the ``property_uuid`` stitch key for the client's concurrent
InSAR Confirmed-badge fetch — NO PII (S6), mirroring the feed/trending DTOs.
"""
from __future__ import annotations

from pydantic import BaseModel

from PE.commerce.services.ranking import decode_media_urls
from PE.commerce.services.search import SearchHit, to_image_url


class TradeSearchResult(BaseModel):
    listing_id: str
    seller_id: str
    shop_id: str
    # The owning shop's display name (a match field + shown on the result row). None only for a
    # nameless shop (shouldn't happen for a live listing).
    shop_name: str | None = None
    # Trade category slug — the client maps it to a color/icon; None ⇒ neutral.
    shop_category: str | None = None
    title: str
    # Integer minor units (cents) — never a float (S9). A plain social POST has price 0.
    price_cents: int
    currency: str
    # The listing's lead (non-video) image for the result thumbnail; None ⇒ initials/category tint.
    image_url: str | None = None
    # Full media set (images + video) so a result row can render richer media if the client wants.
    media_urls: list[str] = []
    # Stitch key for the InSAR Confirmed-shield badge (fetched concurrently client-side). Optional.
    property_uuid: str | None = None
    # Buyer-relative distance in metres (nearest-first ordering key). Informational for the client.
    distance_m: float


class TradeSearchResponse(BaseModel):
    results: list[TradeSearchResult]
    # Echo the normalised query the server actually searched on (trimmed) so the client can label
    # the results panel honestly.
    query: str


def _to_result(hit: SearchHit) -> TradeSearchResult:
    listing = hit.listing
    return TradeSearchResult(
        listing_id=str(listing.id),
        seller_id=str(listing.seller_id),
        shop_id=str(listing.shop_id),
        shop_name=hit.shop_name,
        shop_category=hit.shop_category,
        title=listing.title,
        price_cents=listing.price_cents,
        currency=listing.currency,
        image_url=to_image_url(listing),
        media_urls=decode_media_urls(listing.media_urls),
        property_uuid=listing.property_uuid,
        distance_m=round(hit.distance_m, 2),
    )


def to_search_response(hits: list[SearchHit], query: str) -> TradeSearchResponse:
    return TradeSearchResponse(
        results=[_to_result(h) for h in hits], query=query.strip(),
    )
