"""Feed response schemas.

``property_uuid`` is deliberately surfaced so the frontend can fire a CONCURRENT request to
InSAR for the building's Confirmed safety badge and stitch it client-side (architecture doc
§3) — commerce never cross-DB-joins. No PII is returned (S6): only the opaque seller id.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from PE.commerce.services.ranking import decode_media_urls


class FeedItem(BaseModel):
    id: str
    shop_id: str
    seller_id: str
    # The owning shop's display name + profile picture (§8 social header). The card shows the SHOP's
    # identity (name + avatar), not an initial derived from the listing title. ``shop_avatar_url`` is
    # a media URL (absolute or /uploads/... relative) resolved client-side; None ⇒ initials fallback.
    # Display-only — never a ranking signal.
    shop_name: str | None = None
    shop_avatar_url: str | None = None
    # The owning shop's trade category slug (§8) — the client maps it to a color for the card /
    # trending rail. None ⇒ un-categorised. Display-only, never a ranking signal.
    shop_category: str | None = None
    property_uuid: str | None = None
    title: str
    # Free-text product description (paragraphs preserved). The client shows a ~150-char preview
    # with a "read more" expander — display-only, never a ranking signal. None ⇒ no description.
    description: str | None = None
    price_cents: int
    currency: str
    media_urls: list[str] = []
    distance_m: float
    score: float
    # Display-only social proof (architecture §8): how many users saved this listing. Does NOT
    # feed the ranking — score stays the pure proximity×freshness×intent function so the feed
    # can't be gamed by self-saves and keeps its anti-cold-start property.
    save_count: int = 0
    # Whether THIS caller has already saved the listing — so the card's heart reflects prior saves
    # on a fresh mount (was always false before, since the caller's own save-state wasn't returned).
    # Batch-resolved per page (one membership query, no N+1); display-only, never a ranking signal.
    saved_by_me: bool = False
    # Display-only count of public comments on this post (§8 social thread). Like save_count it is
    # NOT a ranking signal — engagement must never let a noisy post bury a closer quieter one.
    comment_count: int = 0
    # Seller's declared post kind: true ⇒ this is a dedicated short-video post (the feed's
    # Listings|Videos toggle filters on it). An ordinary listing can still carry video media.
    is_short_video: bool = False
    # §8 timeline kind: 'product' (sellable — price/POS chrome) or 'post' (plain social content;
    # the client suppresses price/Ask and renders the description as the post body). Default
    # 'product' keeps every existing listing rendering exactly as before.
    post_kind: str = "product"
    # The listing's SELLER's proof-of-purchase rating (increment 6) — also display-only, NOT a
    # ranking signal (a high rating must never let an established seller bury a closer newcomer;
    # that would reintroduce the cold-start the proximity feed exists to kill). ``seller_rating``
    # is None when the seller has no reviews yet (unrated, distinct from a low score).
    seller_rating: float | None = None
    seller_review_count: int = 0
    # §8 ephemerality: true while this listing has a live "selling now" promotion window. Lets
    # the client badge it ("Selling now / fresh today"); display-only, the boost is already in
    # ``score``.
    is_promoted: bool = False
    # §8.3 Boost: true when this item occupies a SPONSORED slot (paid reach in the sponsored lane,
    # NOT a higher organic score). The client MUST label a sponsored item ("Boosted") — that label
    # is the honesty contract of the two-lane feed. ``boost_tier`` names the reach tier
    # (mtaa|hustle|sovereign) for display; None for an ordinary organic item.
    is_sponsored: bool = False
    boost_tier: str | None = None
    created_at: datetime


class FeedResponse(BaseModel):
    items: list[FeedItem]
    next_cursor: str | None = None
    # Auto-widen honesty signals (services/feed.py). ``widened`` is True when the buyer's immediate
    # radius was thin (fewer than one page of local content) and the feed fell back once to the
    # server max radius to surface MORE of the nearest content; ``nearest_distance_m`` is the closest
    # returned listing's distance in metres (None when there are no listings at all).
    # ``immediate_count`` is how many listings the IMMEDIATE (un-widened) radius held — the client
    # uses it to phrase the note honestly: 0 ⇒ "nothing selling in your immediate area", >0 ⇒ "only a
    # few nearby, also showing shops within X km" (it must NOT claim the area is empty when it isn't).
    # The client shows an honest "closest shops are within X km" note instead of a near-empty surface.
    # Defaults keep the response backward-compatible for a populated feed.
    widened: bool = False
    nearest_distance_m: float | None = None
    immediate_count: int = 0


def to_feed_item(scored, save_count: int = 0,
                 seller_rating: tuple[float, int] | None = None,
                 comment_count: int = 0,
                 shop: tuple[str, str | None, str | None] | None = None,
                 saved_by_me: bool = False) -> FeedItem:
    """Map a services.feed.ScoredListing → FeedItem. media_urls is stored as a JSON string
    (or None); decode defensively to a list. ``save_count``/``comment_count`` are display-only
    social-proof counts, supplied by the router from single batch aggregates (default 0).
    ``seller_rating`` is the ``(average, count)`` for this listing's seller (None ⇒ unrated),
    also from a single batch aggregate — all are display-only and never touch ranking.
    ``shop`` is the owning shop's ``(name, avatar_url, category)`` from a single batch lookup
    (None ⇒ omit). ``saved_by_me`` is whether the calling buyer has saved this listing (from the
    page's single membership query — display-only)."""
    listing = scored.listing
    # Decode via the shared helper so display + the ranking media nudge agree on the media set.
    media = decode_media_urls(listing.media_urls)
    avg, count = (seller_rating if seller_rating is not None else (None, 0))
    shop_name, shop_avatar, shop_category = (shop if shop is not None else (None, None, None))
    return FeedItem(
        id=str(listing.id),
        shop_id=str(listing.shop_id),
        seller_id=str(listing.seller_id),
        shop_name=shop_name,
        shop_avatar_url=shop_avatar,
        shop_category=shop_category,
        property_uuid=listing.property_uuid,
        title=listing.title,
        description=listing.description,
        price_cents=listing.price_cents,
        currency=listing.currency,
        media_urls=media,
        distance_m=round(scored.distance_m, 2),
        score=round(scored.score, 6),
        save_count=save_count,
        saved_by_me=saved_by_me,
        comment_count=comment_count,
        is_short_video=bool(listing.is_short_video),
        post_kind=listing.post_kind,
        seller_rating=round(avg, 2) if avg is not None else None,
        seller_review_count=count,
        is_promoted=scored.is_promoted,
        is_sponsored=scored.is_sponsored,
        boost_tier=scored.boost_tier,
        created_at=listing.created_at,
    )
