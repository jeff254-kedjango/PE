"""Social-engagement endpoints — saves + "is this available?" inquiries (architecture §8).

All require a valid commerce_trade token (``get_current_principal``, fails closed) but NOT the
``create:trades`` seller scope: saving and asking are BUYER actions, open to any authenticated
user. Ownership is enforced in the service off the token ``sub``; cross-owner targets return
404 (never 403) so the API never confirms another user's rows exist (S6).
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from PE.commerce.core.auth import CommercePrincipal, get_current_principal
from PE.commerce.core.config import settings
from PE.commerce.core.database import get_db
from PE.commerce.schemas import engagement as schemas
from PE.commerce.schemas.catalog import to_listing_out
from PE.commerce.services import engagement

router = APIRouter(tags=["engagement"])

_NOT_FOUND = "Not found"  # uniform — never reveals cross-owner / inactive existence


def _page_size(limit: int | None) -> int:
    """Clamp a caller-supplied limit to the server page bounds (anti-O(n), same as the feed)."""
    return min(limit or settings.feed_page_size, settings.feed_max_page_size)


# ----------------------------- saves -----------------------------

@router.post("/listings/{listing_id}/save", response_model=schemas.SaveToggleOut)
def toggle_save(
    listing_id: str,
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(get_current_principal),
) -> schemas.SaveToggleOut:
    """Toggle the caller's save on a listing. Idempotent — double-save stays saved."""
    result = engagement.toggle_save(db, principal.sub, listing_id)
    if result is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    saved, count = result
    return schemas.SaveToggleOut(listing_id=listing_id, saved=saved, save_count=count)


@router.get("/me/saves", response_model=schemas.SavedListingPage)
def my_saves(
    cursor: str | None = Query(default=None),
    limit: int | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(get_current_principal),
) -> schemas.SavedListingPage:
    """The caller's saved listings, newest-first, cursor-paginated."""
    rows, next_cursor = engagement.list_my_saves(
        db, principal.sub, cursor=cursor, limit=_page_size(limit)
    )
    items = [
        schemas.SavedListingOut(saved_at=saved.created_at, listing=to_listing_out(listing))
        for saved, listing in rows
    ]
    return schemas.SavedListingPage(items=items, next_cursor=next_cursor)


# ----------------------------- inquiries -----------------------------

@router.post("/listings/{listing_id}/inquiries", response_model=schemas.InquiryOut, status_code=201)
def create_inquiry(
    listing_id: str,
    body: schemas.InquiryCreate,
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(get_current_principal),
) -> schemas.InquiryOut:
    """Ask the seller "is this still available?" (or a short custom message)."""
    inquiry = engagement.create_inquiry(
        db, principal.sub, listing_id, body.message, from_user_name=principal.name
    )
    if inquiry is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return _to_inquiry_out(inquiry, db)


@router.get("/me/inquiries", response_model=schemas.InquiryPage)
def my_inquiries(
    cursor: str | None = Query(default=None),
    limit: int | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(get_current_principal),
) -> schemas.InquiryPage:
    """The caller's seller inbox — inquiries addressed to their shops, newest-first."""
    rows, next_cursor = engagement.list_my_inquiries(
        db, principal.sub, cursor=cursor, limit=_page_size(limit)
    )
    items = [
        schemas.InquiryOut(
            id=str(inq.id),
            listing_id=str(inq.listing_id),
            listing_title=title,
            seller_id=str(inq.seller_id),
            from_user_uuid=inq.from_user_uuid,
            from_user_name=inq.from_user_name,
            message=inq.message,
            is_read=inq.is_read,
            created_at=inq.created_at,
        )
        for inq, title in rows
    ]
    return schemas.InquiryPage(items=items, next_cursor=next_cursor)


@router.patch("/inquiries/{inquiry_id}/read", status_code=204)
def mark_read(
    inquiry_id: str,
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(get_current_principal),
) -> None:
    """Mark one inquiry read — recipient only (404 otherwise). Idempotent."""
    if not engagement.mark_inquiry_read(db, principal.sub, inquiry_id):
        raise HTTPException(status_code=404, detail=_NOT_FOUND)


# ----------------------------- comments (public thread) -----------------------------

@router.post("/listings/{listing_id}/comments", response_model=schemas.CommentOut, status_code=201)
def create_comment(
    listing_id: str,
    body: schemas.CommentCreate,
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(get_current_principal),
) -> schemas.CommentOut:
    """Post a public comment on a listing's thread. Buyer action (no seller scope). 404 if the
    listing is missing/inactive; 422 if the body is empty/oversized after trimming."""
    try:
        comment = engagement.create_comment(
            db, principal.sub, listing_id, body.body, author_name=principal.name
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if comment is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return _to_comment_out(comment)


@router.get("/listings/{listing_id}/comments", response_model=schemas.CommentPage)
def list_comments(
    listing_id: str,
    cursor: str | None = Query(default=None),
    limit: int | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(get_current_principal),
) -> schemas.CommentPage:
    """A listing's public comment thread, newest-first, cursor-paginated. 404 if missing/inactive.
    Each comment carries its like count + whether THIS caller liked it — both from single batch
    aggregates over the page's ids (no N+1)."""
    result = engagement.list_comments(db, listing_id, cursor=cursor, limit=_page_size(limit))
    if result is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    rows, next_cursor = result
    ids = [str(c.id) for c in rows]
    like_counts = engagement.comment_like_counts(db, ids)
    liked = engagement.liked_comment_ids(db, principal.sub, ids)
    return schemas.CommentPage(
        items=[
            _to_comment_out(c, like_counts.get(str(c.id), 0), str(c.id) in liked)
            for c in rows
        ],
        next_cursor=next_cursor,
    )


@router.patch("/comments/{comment_id}/hidden", status_code=204)
def moderate_comment(
    comment_id: str,
    body: schemas.CommentModerate,
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(get_current_principal),
) -> None:
    """Soft-hide / un-hide a public comment. Authorized for a STAFF principal OR the SELLER who
    owns the comment's listing (own-your-thread moderation). 404 if the comment is missing OR the
    caller isn't authorized — the API never reveals a comment's existence to someone who may not
    moderate it (S6). Idempotent."""
    if not engagement.moderate_comment(db, principal.sub, principal.role, comment_id, body.hidden):
        raise HTTPException(status_code=404, detail=_NOT_FOUND)


@router.post("/comments/{comment_id}/like", response_model=schemas.CommentLikeToggleOut)
def toggle_comment_like(
    comment_id: str,
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(get_current_principal),
) -> schemas.CommentLikeToggleOut:
    """Toggle the caller's like ("love") on a public comment. Buyer action (no seller scope), like
    a save. Idempotent — a double-like stays liked. 404 if the comment is missing/hidden."""
    result = engagement.toggle_comment_like(db, principal.sub, comment_id)
    if result is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    liked, count = result
    return schemas.CommentLikeToggleOut(comment_id=comment_id, liked=liked, like_count=count)


# ----------------------------- helpers -----------------------------

def _to_comment_out(comment, like_count: int = 0, liked_by_me: bool = False) -> schemas.CommentOut:
    return schemas.CommentOut(
        id=str(comment.id),
        listing_id=str(comment.listing_id),
        author_uuid=comment.author_uuid,
        author_name=comment.author_name,
        body=comment.body,
        like_count=like_count,
        liked_by_me=liked_by_me,
        created_at=comment.created_at,
    )

def _to_inquiry_out(inquiry, db: Session) -> schemas.InquiryOut:
    """Build InquiryOut for the just-created inquiry. The listing title comes from the listing
    the inquiry references (one indexed PK lookup)."""
    from PE.commerce.models.listing import Listing

    title = db.query(Listing.title).filter(Listing.id == inquiry.listing_id).scalar() or ""
    return schemas.InquiryOut(
        id=str(inquiry.id),
        listing_id=str(inquiry.listing_id),
        listing_title=title,
        seller_id=str(inquiry.seller_id),
        from_user_uuid=inquiry.from_user_uuid,
        from_user_name=inquiry.from_user_name,
        message=inquiry.message,
        is_read=inquiry.is_read,
        created_at=inquiry.created_at,
    )
