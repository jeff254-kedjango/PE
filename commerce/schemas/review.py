"""Review / rating schemas (§8).

A review exposes only opaque ids + the reviewer's own ``sub`` and their rating/note (no PII,
S6). The public seller view pairs a paginated list of reviews with an aggregate summary so a
buyer sees both the score and the evidence behind it.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# ----------------------------- requests -----------------------------

class ReviewCreate(BaseModel):
    rating: int = Field(ge=1, le=5)                       # bounded at the boundary + in the DB
    body: str | None = Field(default=None, max_length=1000)


# ----------------------------- responses -----------------------------

class ReviewOut(BaseModel):
    id: str
    order_id: str
    seller_id: str
    listing_id: str
    reviewer_uuid: str
    rating: int
    body: str | None = None
    created_at: datetime


class RatingSummary(BaseModel):
    """Aggregate for a seller. ``average`` is None when ``count`` is 0 (unrated, not zero-star)."""
    average: float | None = None
    count: int = 0


class SellerReviewsPage(BaseModel):
    summary: RatingSummary
    items: list[ReviewOut]
    next_cursor: str | None = None


# ----------------------------- mappers -----------------------------

def to_review_out(review) -> ReviewOut:
    return ReviewOut(
        id=str(review.id),
        order_id=str(review.order_id),
        seller_id=str(review.seller_id),
        listing_id=str(review.listing_id),
        reviewer_uuid=review.reviewer_uuid,
        rating=review.rating,
        body=review.body,
        created_at=review.created_at,
    )
