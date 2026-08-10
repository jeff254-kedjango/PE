"""Local review / rating — proof-of-purchase social proof (architecture §8).

THE TRUST MODEL. Normal social media optimizes vanity reach; our moat is *local trust*. A
review is therefore **proof-of-purchase gated**: it can be written only by the BUYER of a
SETTLED order, exactly ONCE per order. No transaction, no review — so a rating reflects real
neighbour-to-neighbour trade, not bought followers or sockpuppets. The gate lives in
services.reviews; this model carries the structural backstop.

  * **UNIQUE(order_id)** — one review per settled order. The order is itself the proof: it
    records the buyer (``buyer_uuid``), the seller, and that the sale reached SETTLED. A
    duplicate review is a clean 409, never a second row.
  * **Denormalized ``seller_id`` + ``listing_id``** (copied from the order at write time) so the
    seller-rating aggregate and the public reviews list read ``reviews`` ALONE — no join, and a
    later listing edit can't retarget a historical review.
  * **Append-only this increment** — no edit/delete endpoint (same discipline as the §7 event
    log and inquiries). A future edit path would be a consented, audited amendment, not a
    silent overwrite.

``rating`` is bounded 1–5 at the API boundary (schema) AND defended in the service; ``body`` is
an optional short free-text note. No PII beyond the reviewer's own ``sub`` (S6).
"""
import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from PE.commerce.core.database import Base, utcnow


class Review(Base):
    """One buyer's rating of a completed (SETTLED) purchase. One per order."""
    __tablename__ = "reviews"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    # The settled order this review attests to — UNIQUE: exactly one review per purchase.
    order_id = Column(String, ForeignKey("orders.id"), nullable=False, index=True)

    # Denormalized targets (from the order) so reads/aggregates never join orders/listings.
    seller_id = Column(String, ForeignKey("sellers.id"), nullable=False, index=True)
    listing_id = Column(String, ForeignKey("listings.id"), nullable=False, index=True)
    # The reviewer == the order's buyer (weespas user id / token sub). NOT a cross-DB FK (§3).
    reviewer_uuid = Column(String, nullable=False, index=True)

    rating = Column(Integer, nullable=False)          # 1..5 (bounded by schema + CHECK)
    body = Column(Text, nullable=True)                # optional short note

    # Python-side default (microsecond, tz-aware) so the keyset cursor round-trips on SQLite;
    # server_default is the DB-side fallback. See core.database.utcnow.
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now(), index=True)

    __table_args__ = (
        UniqueConstraint("order_id", name="uq_review_order"),
        # Defense in depth: the service validates 1..5, and the DB refuses anything else.
        CheckConstraint("rating >= 1 AND rating <= 5", name="ck_review_rating_range"),
        # Public "seller's reviews" page, newest-first — index-backed keyset.
        Index("ix_reviews_seller_created", "seller_id", "created_at"),
    )
