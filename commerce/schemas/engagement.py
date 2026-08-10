"""Social-engagement schemas — save toggle, saved-items view, inquiries.

The saved-items view reuses ``ListingOut`` from schemas.catalog (a saved listing IS a listing
— no parallel DTO). Inquiry responses surface only opaque ids + the buyer's chosen message
(no PII, S6).
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from PE.commerce.schemas.catalog import ListingOut

_DEFAULT_INQUIRY = "Is this still available?"


# ----------------------------- saves -----------------------------

class SaveToggleOut(BaseModel):
    """The new save state after a toggle — lets the client flip its UI without a refetch."""
    listing_id: str
    saved: bool
    save_count: int


class SavedListingOut(BaseModel):
    """A saved listing: when it was saved + the full listing (reused ListingOut shape)."""
    saved_at: datetime
    listing: ListingOut


class SavedListingPage(BaseModel):
    items: list[SavedListingOut]
    next_cursor: str | None = None


# ----------------------------- inquiries -----------------------------

class InquiryCreate(BaseModel):
    # Defaults to the canonical "is this available?" but accepts any short buyer message.
    message: str = Field(default=_DEFAULT_INQUIRY, min_length=1, max_length=500)


class InquiryOut(BaseModel):
    id: str
    listing_id: str
    listing_title: str
    seller_id: str
    from_user_uuid: str
    # Display-name snapshot taken at ask time (None for pre-existing rows / claim-less tokens; the
    # UI falls back to a neutral label, never the raw uuid).
    from_user_name: str | None = None
    message: str
    is_read: bool
    created_at: datetime


class InquiryPage(BaseModel):
    items: list[InquiryOut]
    next_cursor: str | None = None


# ----------------------------- comments (public thread) -----------------------------

class CommentCreate(BaseModel):
    # The public-thread comment. min_length 1 rejects whitespace-only at the boundary; the
    # service strips + re-validates and caps length (COMMENT_MAX_LEN) as the authoritative guard.
    body: str = Field(min_length=1, max_length=2000)


class CommentOut(BaseModel):
    id: str
    listing_id: str
    author_uuid: str
    # Display-name snapshot taken at comment time (None for pre-existing rows / claim-less tokens;
    # the UI falls back to a neutral label, never the raw uuid).
    author_name: str | None = None
    body: str
    # §8 like ("love") social proof — display-only count + whether THIS viewer has liked it (so the
    # client can render the filled/empty heart without a per-comment request). Both default to the
    # un-liked zero state; the router fills them from batch aggregates over the page (no N+1).
    like_count: int = 0
    liked_by_me: bool = False
    created_at: datetime


class CommentPage(BaseModel):
    items: list[CommentOut]
    next_cursor: str | None = None


class CommentLikeToggleOut(BaseModel):
    """The new like state after a toggle — lets the client flip the heart + count without a refetch.
    Mirrors SaveToggleOut."""
    comment_id: str
    liked: bool
    like_count: int


class CommentModerate(BaseModel):
    """Staff/seller moderation of a public comment: set its hidden flag. ``hidden=true`` soft-hides
    it (removed from the thread + counts, kept in the table); ``hidden=false`` un-hides."""
    hidden: bool
