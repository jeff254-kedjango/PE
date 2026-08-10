"""Reviews service — the proof-of-purchase rating path (§8 local social proof).

The only writer of ``Review``. The gate is the product: a review is accepted ONLY when
  1. the order exists,
  2. the caller is that order's BUYER (never the seller — you can't rate your own sale), and
  3. the order is SETTLED (a completed purchase — not a pending/failed/cancelled negotiation),
and at most ONCE per order (UNIQUE(order_id) is the hard backstop behind the service check).
Anything else is a typed error the router maps to a status code; a non-party order is reported
as 404 so the API never confirms another user's order exists (S6).

Reads are cheap and join-free (seller_id/listing_id are denormalized onto the review):
  * ``seller_rating`` — one AVG+COUNT aggregate, O(1) at the index.
  * ``seller_ratings`` — a single GROUP BY for a whole page of sellers (no N+1), ready for the
    feed/storefront badge.
  * ``list_seller_reviews`` — keyset-paginated newest-first, O(log n + k).
"""
from __future__ import annotations

import base64
from datetime import datetime

from sqlalchemy import func, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from PE.commerce.models.order import STATUS_SETTLED, Order
from PE.commerce.models.review import Review
from PE.commerce.models.seller import Seller


# ----------------------------- typed errors (router maps to status codes) -----------------------------

class ReviewError(Exception):
    status_code = 400

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class NotFoundError(ReviewError):
    status_code = 404


class ConflictError(ReviewError):
    status_code = 409


class ForbiddenError(ReviewError):
    status_code = 403


class ValidationError(ReviewError):
    status_code = 422


# ----------------------------- keyset cursor (created_at, id) -----------------------------

def _encode_cursor(created_at: datetime, row_id: str) -> str:
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{row_id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, str] | None:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts_str, row_id = raw.split("|", 1)
        return datetime.fromisoformat(ts_str), row_id
    except (ValueError, TypeError):
        return None


# ----------------------------- write -----------------------------

def create_review(db: Session, reviewer_uuid: str, order_id: str, *, rating: int,
                  body: str | None) -> Review:
    """Create the buyer's review for a SETTLED order. Proof-of-purchase gated (see module
    docstring). Raises a typed ReviewError on any gate failure; returns the persisted Review."""
    if rating < 1 or rating > 5:
        raise ValidationError("rating must be between 1 and 5")

    order = db.query(Order).filter(Order.id == order_id).one_or_none()
    if order is None:
        raise NotFoundError("Order not found")
    # Only the buyer may review, and we must not leak that a non-party's order exists: a caller
    # who is neither buyer nor (would-be) seller gets the same 404 as a missing order. A seller
    # trying to review their own sale gets an explicit 403 (their identity IS on the order).
    if order.buyer_uuid != reviewer_uuid:
        seller = db.query(Seller).filter(Seller.user_uuid == reviewer_uuid).one_or_none()
        if seller is not None and order.seller_id == seller.id:
            raise ForbiddenError("A seller cannot review their own sale")
        raise NotFoundError("Order not found")
    if order.status != STATUS_SETTLED:
        # Reviewable only after a completed purchase — not a pending/failed/cancelled order.
        raise ConflictError("Only a settled order can be reviewed")

    review = Review(
        order_id=order.id,
        seller_id=order.seller_id,
        listing_id=order.listing_id,
        reviewer_uuid=reviewer_uuid,
        rating=rating,
        body=body,
    )
    db.add(review)
    try:
        db.commit()
    except IntegrityError:
        # UNIQUE(order_id) lost the race — this order is already reviewed. Clean 409.
        db.rollback()
        raise ConflictError("This order has already been reviewed")
    db.refresh(review)
    return review


# ----------------------------- reads -----------------------------

def seller_rating(db: Session, seller_id: str) -> tuple[float | None, int]:
    """(average_rating, count) for one seller — a single AVG+COUNT aggregate. average is None
    when the seller has no reviews yet (count 0), so the caller can distinguish "unrated" from
    a genuine low score."""
    avg, count = (
        db.query(func.avg(Review.rating), func.count(Review.id))
        .filter(Review.seller_id == seller_id)
        .one()
    )
    return (float(avg) if avg is not None else None, int(count or 0))


def seller_ratings(db: Session, seller_ids: list[str]) -> dict[str, tuple[float, int]]:
    """Batch (avg, count) for many sellers in ONE GROUP BY (no N+1) — for a feed/storefront
    page's rating badge. Sellers with no reviews are absent (caller defaults to unrated)."""
    if not seller_ids:
        return {}
    rows = (
        db.query(Review.seller_id, func.avg(Review.rating), func.count(Review.id))
        .filter(Review.seller_id.in_(seller_ids))
        .group_by(Review.seller_id)
        .all()
    )
    return {sid: (float(avg), int(count)) for sid, avg, count in rows}


def list_seller_reviews(db: Session, seller_id: str, *, cursor: str | None = None,
                        limit: int = 20) -> tuple[list[Review], str | None]:
    """A seller's reviews newest-first, keyset-paginated (created_at, id)."""
    q = db.query(Review).filter(Review.seller_id == seller_id)
    anchor = _decode_cursor(cursor) if cursor else None
    if anchor is not None:
        ts, rid = anchor
        q = q.filter(tuple_(Review.created_at, Review.id) < (ts, rid))
    rows = (
        q.order_by(Review.created_at.desc(), Review.id.desc())
        .limit(limit + 1)
        .all()
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = None
    if has_more and page:
        next_cursor = _encode_cursor(page[-1].created_at, page[-1].id)
    return page, next_cursor
