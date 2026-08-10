"""Social-engagement models — saves and "is this available?" inquiries (architecture §8).

Lightweight social proof that builds *local* trust without a follower graph. Two tables:

  * ``SavedListing`` — a user bookmarking a listing. A UNIQUE (user_uuid, listing_id) makes the
    save idempotent (a double-save is a no-op, never a duplicate row) and is the index that
    powers both the toggle and the "my saved items" view.
  * ``ListingInquiry`` — a buyer's question on a listing ("Is this still available?"). Append-
    only (no edit/delete this increment — same ledger discipline as §7). ``seller_id`` is
    DENORMALIZED from the listing so a seller reads their inbox without joining listings.

No cross-DB FKs: ``user_uuid`` / ``from_user_uuid`` are synchronized weespas user ids (the
token ``sub``), indexed string columns, never SQL-joined to the weespas DB (doc §3).
"""
import uuid

from sqlalchemy import (
    Boolean,
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


class SavedListing(Base):
    __tablename__ = "saved_listings"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    # The weespas user id (token sub). NOT a FK — separate database (doc §3).
    user_uuid = Column(String, nullable=False, index=True)
    listing_id = Column(String, ForeignKey("listings.id"), nullable=False, index=True)
    # Per-user monotonic sequence (0,1,2,…), assigned in the service at save time (mirrors
    # OrderEvent.seq). This — not created_at — is the newest-first keyset for "my saves", because
    # created_at can TIE at the microsecond under rapid programmatic saves and its tie-break was a
    # random uuid4 id → non-deterministic order. seq is strictly monotonic within a user, so
    # ORDER BY seq DESC is a total, insertion-faithful order with no tie. See uq_saved_user_seq.
    seq = Column(Integer, nullable=False)
    # Kept for display (the "saved at" timestamp) + audit, no longer the sort key. Python-side
    # default preserved so any created_at read is microsecond-precise (see core.database.utcnow).
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now())

    __table_args__ = (
        # One save per (user, listing): makes toggle_save idempotent — no duplicate rows, no
        # count drift.
        UniqueConstraint("user_uuid", "listing_id", name="uq_saved_user_listing"),
        # Newest-first "my saves" keyset, scoped to the caller, over the monotonic seq. The
        # UNIQUE both guarantees the total order (no two saves by one user share a seq — a
        # concurrent-seq race is caught and retried in the service) and is the covering index the
        # keyset range-scans. Replaces the old (user_uuid, created_at) index.
        UniqueConstraint("user_uuid", "seq", name="uq_saved_user_seq"),
    )


class ListingInquiry(Base):
    __tablename__ = "listing_inquiries"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    listing_id = Column(String, ForeignKey("listings.id"), nullable=False, index=True)
    # Denormalized recipient: the seller who owns the listing, so the inbox query reads this
    # table alone (no join to listings/shops). Written from the listing at create time.
    seller_id = Column(String, ForeignKey("sellers.id"), nullable=False, index=True)
    # The asker (weespas user id). NOT a FK — separate database.
    from_user_uuid = Column(String, nullable=False, index=True)
    # Display-name SNAPSHOT from the token's name claim at ask time (commerce owns no identity;
    # this lets the seller inbox show a name, not a raw id). Nullable: pre-existing rows + tokens
    # without the claim have none, and the UI falls back to a neutral label.
    from_user_name = Column(String(255), nullable=True)
    message = Column(Text, nullable=False)
    is_read = Column(Boolean, nullable=False, default=False, server_default="false")
    # Per-seller monotonic sequence (0,1,2,…), assigned in the service at ask time (mirrors
    # OrderEvent.seq). This — not created_at — is the seller-inbox newest-first keyset: created_at
    # ties at the microsecond under rapid programmatic asks and its old tie-break was a random
    # uuid4 id → non-deterministic order. seq is strictly monotonic within a seller. See
    # uq_inquiry_seller_seq.
    seq = Column(Integer, nullable=False)
    # Kept for display + audit, no longer the sort key. Python-side default preserved for
    # microsecond-precise reads.
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now())

    __table_args__ = (
        # Seller inbox newest-first keyset over the monotonic seq. UNIQUE guarantees the total
        # order (concurrent-seq race caught + retried in the service) and covers the range-scan.
        # Replaces the old (seller_id, created_at) index.
        UniqueConstraint("seller_id", "seq", name="uq_inquiry_seller_seq"),
    )


class ListingComment(Base):
    """A public comment on a listing post (the social feed's comment thread, §8).

    Append-only this increment (no edit/delete — same ledger discipline as inquiries), so the
    thread is a stable audit of what was said. Distinct from ListingInquiry: an inquiry is a
    PRIVATE buyer→seller question landing in the seller inbox; a comment is PUBLIC, shown inline
    under the post to everyone. ``author_uuid`` is the weespas user id (token sub), never a FK.

    A ``hidden`` flag is the moderation seam: a future staff/seller-moderation path can soft-hide
    an abusive comment without breaking the append-only history (the row stays; reads filter it
    out). It ships INERT (default false) — no moderation endpoint yet, but the column means adding
    one later is non-destructive."""
    __tablename__ = "listing_comments"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    listing_id = Column(String, ForeignKey("listings.id"), nullable=False, index=True)
    # The commenter (weespas user id). NOT a FK — separate database (doc §3).
    author_uuid = Column(String, nullable=False, index=True)
    # Display-name SNAPSHOT from the token's name claim at comment time. Nullable: pre-existing
    # rows + tokens without the claim have none; the UI falls back to a neutral label, never the id.
    author_name = Column(String(255), nullable=True)
    body = Column(Text, nullable=False)
    # Moderation seam — soft-hide without deleting the append-only row. Inert (no endpoint yet).
    hidden = Column(Boolean, nullable=False, default=False, server_default="false")
    # Per-listing monotonic sequence (0,1,2,…), assigned in the service at comment time (mirrors
    # OrderEvent.seq). This — not created_at — is the thread's newest-first keyset: two comments
    # posted back-to-back tie at the microsecond and the old tie-break was a random uuid4 id →
    # non-deterministic order (this flaked test_comment_post_and_list_newest_first). seq is
    # strictly monotonic within a listing. See uq_comment_listing_seq.
    seq = Column(Integer, nullable=False)
    # Kept for display + audit, no longer the sort key. Python-side default preserved for
    # microsecond-precise reads.
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now())

    __table_args__ = (
        # Per-listing thread newest-first keyset over the monotonic seq. UNIQUE guarantees the
        # total order (concurrent-seq race caught + retried in the service) and covers the
        # range-scan. Replaces the old (listing_id, created_at) index.
        UniqueConstraint("listing_id", "seq", name="uq_comment_listing_seq"),
    )


class CommentLike(Base):
    """A user "loving" a public comment (§8 timeline). Mirrors SavedListing exactly: a UNIQUE
    (user_uuid, comment_id) makes the like idempotent (a double-like is a no-op, never a duplicate)
    and a concurrent like-race resolves to "already liked" (the caught IntegrityError, no 500). The
    like COUNT for a comment thread page is a single grouped aggregate (no N+1), and which of the
    page's comments the caller already liked is one batched membership query. ``user_uuid`` is the
    weespas user id (token sub) — NOT a FK (separate database, doc §3)."""
    __tablename__ = "comment_likes"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    comment_id = Column(String, ForeignKey("listing_comments.id"), nullable=False, index=True)
    user_uuid = Column(String, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now())

    __table_args__ = (
        # Idempotent toggle: one like per (user, comment).
        UniqueConstraint("user_uuid", "comment_id", name="uq_comment_like_user"),
        # "my likes" keyset, newest-first.
        Index("ix_comment_like_user_created", "user_uuid", "created_at"),
    )
