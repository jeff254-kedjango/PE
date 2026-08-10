"""Social-engagement service — saves and inquiries (the only writer for both).

Saves are idempotent (a UNIQUE on (user_uuid, listing_id) + a caught race). Save COUNTS for a
feed page are a SINGLE grouped aggregate (no N+1). Inquiries are append-only and read from a
denormalized recipient (seller_id), so the seller inbox needs no join.

Pagination uses an opaque keyset cursor over a per-scope monotonic ``seq`` — index-backed
(O(log n + k)), unlike the feed's in-memory window (its candidate set is already radius-
bounded). A malformed cursor restarts from the top (best-effort, never an error).

Ordering is by ``seq`` (assigned at write time), NOT ``created_at``: a microsecond-precise
timestamp still TIES under rapid programmatic writes, and the old tie-break was the row's random
uuid4 id → a non-deterministic "newest-first" within a tie. ``seq`` is strictly monotonic within
a scope (user / seller / listing), so ``ORDER BY seq DESC`` is a total, insertion-faithful order
with no tie. Mirrors the ``OrderEvent.seq`` per-order chain (services.settlement).
"""
from __future__ import annotations

import base64

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from PE.commerce.models.engagement import CommentLike, ListingComment, ListingInquiry, SavedListing
from PE.commerce.models.listing import Listing
from PE.commerce.models.seller import Seller


# ----------------------------- keyset cursor (monotonic seq) -----------------------------

def _encode_cursor(seq: int) -> str:
    """Opaque cursor over the per-scope monotonic seq. Because seq is a total order WITHIN a
    scope, a single integer is a complete keyset anchor — no tie-break column needed."""
    return base64.urlsafe_b64encode(str(seq).encode()).decode()


def _decode_cursor(cursor: str) -> int | None:
    """Parse cursor → seq, or None on malformed input (restart from top)."""
    try:
        return int(base64.urlsafe_b64decode(cursor.encode()).decode())
    except (ValueError, TypeError):
        return None


# ----------------------------- per-scope monotonic seq -----------------------------

# Bounded retries on a seq-collision race: two concurrent writes in the SAME scope can read the
# same max(seq) and both try seq+1; the UNIQUE(scope, seq) turns the loser into an IntegrityError,
# and we recompute + retry. Under READ COMMITTED the retry sees the winner's committed row, so seq
# strictly advances and the loop converges in one extra attempt per colliding writer. The ceiling
# guards a pathological hot scope rather than looping unbounded (never hit on the SQLite test path,
# which serialises writes on one connection).
_SEQ_MAX_RETRIES = 8


def _next_seq(db: Session, model, scope_field, scope_value) -> int:
    """The next per-scope sequence number: ``max(seq)+1`` within the scope, 0 for the first row.
    Mirrors settlement._append_event's per-order seq."""
    last = (
        db.query(model.seq)
        .filter(scope_field == scope_value)
        .order_by(model.seq.desc())
        .first()
    )
    return 0 if last is None else last[0] + 1


def _commit_appended(db: Session, obj, model, scope_field, scope_value) -> None:
    """Assign the next per-scope seq and commit an APPEND-ONLY row (comment / inquiry), retrying
    on a seq-collision race (a concurrent same-scope writer took the seq first). The append tables
    have no other UNIQUE, so any IntegrityError here IS a seq collision → recompute + retry. Raises
    if retries are exhausted (a hot scope far beyond any real thread — surfaced, never silent)."""
    for _ in range(_SEQ_MAX_RETRIES):
        obj.seq = _next_seq(db, model, scope_field, scope_value)
        db.add(obj)
        try:
            db.commit()
            return
        except IntegrityError:
            db.rollback()  # rollback makes obj transient again; the next loop re-adds it
    raise RuntimeError(f"{model.__name__}: could not assign a unique seq after retries")


# ----------------------------- saves -----------------------------

def _active_listing(db: Session, listing_id: str) -> Listing | None:
    """The listing iff it exists and is active; else None (router → 404). You cannot save or
    ask about an inactive/nonexistent listing."""
    return (
        db.query(Listing)
        .filter(Listing.id == listing_id, Listing.is_active.is_(True))
        .one_or_none()
    )


def save_count(db: Session, listing_id: str) -> int:
    """Number of users who have saved one listing — a single COUNT."""
    return db.query(func.count(SavedListing.id)).filter(
        SavedListing.listing_id == listing_id
    ).scalar() or 0


def save_counts(db: Session, listing_ids: list[str]) -> dict[str, int]:
    """Batch save-counts for a whole feed page in ONE GROUP BY query (no N+1). Listings with
    zero saves are simply absent from the dict (caller defaults them to 0)."""
    if not listing_ids:
        return {}
    rows = (
        db.query(SavedListing.listing_id, func.count(SavedListing.id))
        .filter(SavedListing.listing_id.in_(listing_ids))
        .group_by(SavedListing.listing_id)
        .all()
    )
    return {lid: cnt for lid, cnt in rows}


def saved_listing_ids(db: Session, user_uuid: str, listing_ids: list[str]) -> set[str]:
    """Which of ``listing_ids`` the caller has already saved — ONE indexed membership query (no
    N+1), so the feed can render each card's save state for THIS viewer instead of defaulting every
    heart to un-saved on a fresh mount. Empty set if none. Mirrors ``liked_comment_ids`` exactly."""
    if not listing_ids:
        return set()
    rows = (
        db.query(SavedListing.listing_id)
        .filter(SavedListing.user_uuid == user_uuid, SavedListing.listing_id.in_(listing_ids))
        .all()
    )
    return {lid for (lid,) in rows}


def toggle_save(db: Session, user_uuid: str, listing_id: str) -> tuple[bool, int] | None:
    """Toggle a save for (user, listing). Returns ``(saved, save_count)`` or None if the
    listing doesn't exist / isn't active (router → 404).

    Idempotent under a save→save race, AND robust to a seq-collision race. ``saved_listings`` now
    carries TWO unique constraints, so an insert IntegrityError is ambiguous:
      * uq_saved_user_listing — a concurrent DUPLICATE save (same user+listing) → idempotent, done;
      * uq_saved_user_seq     — a concurrent save of a DIFFERENT listing grabbed our seq → retry.
    We disambiguate by re-reading the (user, listing) row after each rollback: present ⇒ duplicate
    (stop, saved), absent ⇒ seq race (recompute seq + retry). Bounded so a pathological hot user
    can't loop unbounded."""
    if _active_listing(db, listing_id) is None:
        return None

    existing = (
        db.query(SavedListing)
        .filter(SavedListing.user_uuid == user_uuid, SavedListing.listing_id == listing_id)
        .one_or_none()
    )
    if existing is not None:
        db.delete(existing)
        db.commit()
        return False, save_count(db, listing_id)

    for _ in range(_SEQ_MAX_RETRIES):
        row = SavedListing(
            user_uuid=user_uuid,
            listing_id=listing_id,
            seq=_next_seq(db, SavedListing, SavedListing.user_uuid, user_uuid),
        )
        db.add(row)
        try:
            db.commit()
            break
        except IntegrityError:
            db.rollback()
            # Duplicate (user, listing)? Then a concurrent save already recorded it → idempotent.
            dup = (
                db.query(SavedListing.id)
                .filter(SavedListing.user_uuid == user_uuid, SavedListing.listing_id == listing_id)
                .one_or_none()
            )
            if dup is not None:
                break
            # Otherwise the collision was on (user, seq) — a different listing took our seq. Retry.
    else:
        raise RuntimeError("toggle_save: could not assign a unique seq after retries")
    return True, save_count(db, listing_id)


def list_my_saves(
    db: Session, user_uuid: str, *, cursor: str | None = None, limit: int = 20
) -> tuple[list[tuple[SavedListing, Listing]], str | None]:
    """The caller's saved listings, newest-first, keyset-paginated. Joins listing so the view
    can render the full item. Returns ``([(SavedListing, Listing)...], next_cursor)``."""
    q = (
        db.query(SavedListing, Listing)
        .join(Listing, SavedListing.listing_id == Listing.id)
        .filter(SavedListing.user_uuid == user_uuid)
    )
    anchor = _decode_cursor(cursor) if cursor else None
    if anchor is not None:
        # Strictly after the last-seen row in seq-DESC order (seq is a total order per user).
        q = q.filter(SavedListing.seq < anchor)
    rows = (
        q.order_by(SavedListing.seq.desc())
        .limit(limit + 1)  # fetch one extra to know if there's a next page
        .all()
    )
    return _paginate(rows, limit, lambda r: r[0].seq)


# ----------------------------- inquiries -----------------------------

def create_inquiry(
    db: Session, from_user_uuid: str, listing_id: str, message: str,
    from_user_name: str | None = None,
) -> ListingInquiry | None:
    """Record a buyer's inquiry on a listing. Returns None if the listing is missing/inactive
    (router → 404). ``seller_id`` is denormalized from the listing so the seller inbox reads
    listing_inquiries alone. ``from_user_name`` is the asker's display-name snapshot (from the
    token's name claim) — stored for the inbox display, never used for auth."""
    listing = _active_listing(db, listing_id)
    if listing is None:
        return None
    inquiry = ListingInquiry(
        listing_id=listing.id,
        seller_id=listing.seller_id,
        from_user_uuid=from_user_uuid,
        from_user_name=(from_user_name or None),
        message=message,
    )
    # Append with a per-seller monotonic seq (retried on a concurrent same-seller seq race).
    _commit_appended(db, inquiry, ListingInquiry, ListingInquiry.seller_id, listing.seller_id)
    db.refresh(inquiry)
    return inquiry


def list_my_inquiries(
    db: Session, user_uuid: str, *, cursor: str | None = None, limit: int = 20
) -> tuple[list[tuple[ListingInquiry, str]], str | None]:
    """The caller's seller inbox: inquiries addressed to their Seller, newest-first, keyset-
    paginated. Returns ``([(ListingInquiry, listing_title)...], next_cursor)``. An empty list
    when the caller has no Seller row (never sold) — not an error."""
    seller = db.query(Seller).filter(Seller.user_uuid == user_uuid).one_or_none()
    if seller is None:
        return [], None

    q = (
        db.query(ListingInquiry, Listing.title)
        .join(Listing, ListingInquiry.listing_id == Listing.id)
        .filter(ListingInquiry.seller_id == seller.id)
    )
    anchor = _decode_cursor(cursor) if cursor else None
    if anchor is not None:
        # Strictly after the last-seen row in seq-DESC order (seq is a total order per seller).
        q = q.filter(ListingInquiry.seq < anchor)
    rows = (
        q.order_by(ListingInquiry.seq.desc())
        .limit(limit + 1)
        .all()
    )
    return _paginate(rows, limit, lambda r: r[0].seq)


def mark_inquiry_read(db: Session, user_uuid: str, inquiry_id: str) -> bool:
    """Mark one inquiry read — recipient only. Returns False if the inquiry doesn't exist or
    isn't addressed to the caller's Seller (router → 404, no existence leak). Idempotent: a
    re-mark is a no-op success."""
    seller = db.query(Seller).filter(Seller.user_uuid == user_uuid).one_or_none()
    if seller is None:
        return False
    inquiry = (
        db.query(ListingInquiry)
        .filter(ListingInquiry.id == inquiry_id, ListingInquiry.seller_id == seller.id)
        .one_or_none()
    )
    if inquiry is None:
        return False
    if not inquiry.is_read:
        inquiry.is_read = True
        db.commit()
    return True


# ----------------------------- comments (public thread) -----------------------------

# A comment body is short by design (a feed thread, not a forum). Bounding length here caps the
# write so a single comment can't be used to dump unbounded text into the row / the feed payload.
COMMENT_MAX_LEN = 2000


def create_comment(
    db: Session, author_uuid: str, listing_id: str, body: str,
    author_name: str | None = None,
) -> ListingComment | None:
    """Append a public comment to a listing's thread. Returns None if the listing is missing/
    inactive (router → 404), or raises ValueError if the body is empty/oversized (router → 422).
    Append-only: no edit/delete this increment. ``author_name`` is the commenter's display-name
    snapshot (from the token's name claim) — stored for the thread display, never used for auth."""
    text_body = (body or "").strip()
    if not text_body:
        raise ValueError("comment body must not be empty")
    if len(text_body) > COMMENT_MAX_LEN:
        raise ValueError(f"comment body exceeds {COMMENT_MAX_LEN} characters")
    if _active_listing(db, listing_id) is None:
        return None
    comment = ListingComment(
        listing_id=listing_id, author_uuid=author_uuid,
        author_name=(author_name or None), body=text_body,
    )
    # Append with a per-listing monotonic seq (retried on a concurrent same-listing seq race).
    _commit_appended(db, comment, ListingComment, ListingComment.listing_id, listing_id)
    db.refresh(comment)
    return comment


def comment_counts(db: Session, listing_ids: list[str]) -> dict[str, int]:
    """Batch visible-comment counts for a whole feed page in ONE GROUP BY (no N+1). Hidden
    (moderated) comments are excluded so the displayed count matches the rendered thread.
    Listings with zero comments are absent (caller defaults to 0)."""
    if not listing_ids:
        return {}
    rows = (
        db.query(ListingComment.listing_id, func.count(ListingComment.id))
        .filter(
            ListingComment.listing_id.in_(listing_ids),
            ListingComment.hidden.is_(False),
        )
        .group_by(ListingComment.listing_id)
        .all()
    )
    return {lid: cnt for lid, cnt in rows}


def list_comments(
    db: Session, listing_id: str, *, cursor: str | None = None, limit: int = 20
) -> tuple[list[ListingComment], str | None] | None:
    """A listing's public comment thread, newest-first, keyset-paginated. Returns
    ``([ListingComment...], next_cursor)`` or None if the listing is missing/inactive (router →
    404). Hidden comments are filtered out (moderation seam)."""
    if _active_listing(db, listing_id) is None:
        return None
    q = db.query(ListingComment).filter(
        ListingComment.listing_id == listing_id,
        ListingComment.hidden.is_(False),
    )
    anchor = _decode_cursor(cursor) if cursor else None
    if anchor is not None:
        # Strictly after the last-seen row in seq-DESC order. Hidden comments leave gaps in the
        # per-listing seq stream, but seq stays strictly monotonic so the order is unaffected.
        q = q.filter(ListingComment.seq < anchor)
    rows = (
        q.order_by(ListingComment.seq.desc())
        .limit(limit + 1)
        .all()
    )
    return _paginate(rows, limit, lambda r: r.seq)


# ----------------------------- comment moderation (soft-hide) -----------------------------

def moderate_comment(
    db: Session, user_uuid: str, role: str, comment_id: str, hidden: bool
) -> bool:
    """Soft-hide (or un-hide) a public comment. Authorized for either:
      * a STAFF principal (platform moderation), or
      * the SELLER who owns the listing the comment is on (own-your-thread moderation).
    Returns True on success, False if the comment doesn't exist OR the caller isn't authorized —
    the router maps both to 404 so the API never reveals a comment's existence to someone who may
    not moderate it (S6, same no-leak discipline as the cross-owner 404 elsewhere).

    Idempotent: setting the flag to its current value is a no-op success. Hidden comments stay in
    the table (append-only audit) but are already filtered from every read path (list_comments,
    comment_counts, like-by-id), so a hide takes effect everywhere at once."""
    comment = db.query(ListingComment).filter(ListingComment.id == comment_id).one_or_none()
    if comment is None:
        return False

    is_staff = role == "staff"
    if not is_staff:
        # Seller-owns-the-thread: the comment's listing must belong to the caller's Seller. One
        # indexed join; a non-owner non-staff caller is unauthorized (→ 404, no existence leak).
        owns = (
            db.query(Listing.id)
            .join(Seller, Listing.seller_id == Seller.id)
            .filter(Listing.id == comment.listing_id, Seller.user_uuid == user_uuid)
            .one_or_none()
        )
        if owns is None:
            return False

    if comment.hidden != hidden:
        comment.hidden = hidden
        db.commit()
    return True


# ----------------------------- comment likes (§8 timeline) -----------------------------

def _visible_comment(db: Session, comment_id: str) -> ListingComment | None:
    """The comment iff it exists and is not hidden; else None (router → 404). You can't like a
    hidden (moderated) or nonexistent comment."""
    return (
        db.query(ListingComment)
        .filter(ListingComment.id == comment_id, ListingComment.hidden.is_(False))
        .one_or_none()
    )


def comment_like_count(db: Session, comment_id: str) -> int:
    """Number of users who have liked one comment — a single COUNT."""
    return db.query(func.count(CommentLike.id)).filter(
        CommentLike.comment_id == comment_id
    ).scalar() or 0


def comment_like_counts(db: Session, comment_ids: list[str]) -> dict[str, int]:
    """Batch like-counts for a whole comment-thread page in ONE GROUP BY (no N+1). Comments with
    zero likes are absent (caller defaults to 0)."""
    if not comment_ids:
        return {}
    rows = (
        db.query(CommentLike.comment_id, func.count(CommentLike.id))
        .filter(CommentLike.comment_id.in_(comment_ids))
        .group_by(CommentLike.comment_id)
        .all()
    )
    return {cid: cnt for cid, cnt in rows}


def liked_comment_ids(db: Session, user_uuid: str, comment_ids: list[str]) -> set[str]:
    """Which of ``comment_ids`` the caller has liked — ONE indexed membership query (no N+1), so
    the thread can render each comment's like state for this viewer. Empty set if none."""
    if not comment_ids:
        return set()
    rows = (
        db.query(CommentLike.comment_id)
        .filter(CommentLike.user_uuid == user_uuid, CommentLike.comment_id.in_(comment_ids))
        .all()
    )
    return {cid for (cid,) in rows}


def toggle_comment_like(db: Session, user_uuid: str, comment_id: str) -> tuple[bool, int] | None:
    """Toggle the caller's like on a comment. Returns ``(liked, like_count)`` or None if the comment
    is missing/hidden (router → 404). Idempotent under a like→like race: the UNIQUE constraint turns
    a concurrent double-insert into an IntegrityError, treated as 'already liked' (no 500) — mirrors
    toggle_save exactly."""
    if _visible_comment(db, comment_id) is None:
        return None
    existing = (
        db.query(CommentLike)
        .filter(CommentLike.user_uuid == user_uuid, CommentLike.comment_id == comment_id)
        .one_or_none()
    )
    if existing is not None:
        db.delete(existing)
        db.commit()
        return False, comment_like_count(db, comment_id)
    db.add(CommentLike(user_uuid=user_uuid, comment_id=comment_id))
    try:
        db.commit()
    except IntegrityError:
        # Concurrent like won the race — already exists. Treat as liked (idempotent).
        db.rollback()
    return True, comment_like_count(db, comment_id)


# ----------------------------- pagination helper -----------------------------

def _paginate(rows: list, limit: int, key):
    """Split a ``limit + 1`` fetch into (page, next_cursor). ``key(row)`` returns the per-scope
    ``seq`` the cursor encodes. next_cursor is None on the last page."""
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = None
    if has_more and page:
        next_cursor = _encode_cursor(key(page[-1]))
    return page, next_cursor
