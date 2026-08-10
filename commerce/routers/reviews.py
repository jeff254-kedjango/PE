"""Review / rating endpoints (§8 local social proof).

Writing a review is a BUYER action gated on a SETTLED order (not a money movement, so it uses
``get_current_principal`` — a valid commerce_trade token — not the settlement denylist). The
proof-of-purchase gate (buyer-only, settled-only, one-per-order) lives in services.reviews;
typed errors map to their status code, and a non-party order is a uniform 404 (no existence
leak, S6). Reads require a token too (commerce fails closed — no public endpoints).
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from PE.commerce.core.auth import CommercePrincipal, get_current_principal
from PE.commerce.core.config import settings
from PE.commerce.core.database import get_db
from PE.commerce.schemas import review as schemas
from PE.commerce.services import reviews

router = APIRouter(tags=["reviews"])


@router.post("/orders/{order_id}/review", response_model=schemas.ReviewOut, status_code=201)
def create_review(
    order_id: str,
    body: schemas.ReviewCreate,
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(get_current_principal),
) -> schemas.ReviewOut:
    try:
        review = reviews.create_review(
            db, principal.sub, order_id, rating=body.rating, body=body.body,
        )
    except reviews.ReviewError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return schemas.to_review_out(review)


@router.get("/sellers/{seller_id}/reviews", response_model=schemas.SellerReviewsPage)
def seller_reviews(
    seller_id: str,
    cursor: str | None = Query(default=None),
    limit: int | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(get_current_principal),
) -> schemas.SellerReviewsPage:
    """A seller's reviews newest-first, with an aggregate rating summary. An unknown/unrated
    seller is a valid empty page (summary count 0, average None) — never a 404, so the caller
    needn't special-case a brand-new seller."""
    page_size = min(limit or settings.feed_page_size, settings.feed_max_page_size)
    avg, count = reviews.seller_rating(db, seller_id)
    rows, next_cursor = reviews.list_seller_reviews(
        db, seller_id, cursor=cursor, limit=page_size,
    )
    return schemas.SellerReviewsPage(
        summary=schemas.RatingSummary(average=avg, count=count),
        items=[schemas.to_review_out(r) for r in rows],
        next_cursor=next_cursor,
    )
