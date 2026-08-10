"""Shop-view API schemas (§8, Chunk C)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ------------------------- heartbeat -------------------------


class HeartbeatIn(BaseModel):
    """Body of POST /shops/{shop_id}/heartbeat."""
    session_id: str = Field(min_length=1, max_length=64)
    # §8 Chunk C+: the listing the viewer is CURRENTLY on. Optional — a bare storefront visit
    # sends null (or omits the field). Latest wins: every heartbeat overwrites this on the
    # existing session row, including with null (leaving a PDP → null → seller stops seeing
    # 'viewing X').
    viewing_listing_id: str | None = Field(default=None, max_length=64)
    # §8 Chunk C+: the viewer's coarse coord at heartbeat time (browser Geolocation grant).
    # Optional — a viewer who denied the permission sends null. Both must be present or both
    # null; the service drops a half-coord anyway.
    last_lat: float | None = Field(default=None, ge=-90.0, le=90.0)
    last_lng: float | None = Field(default=None, ge=-180.0, le=180.0)


class HeartbeatOut(BaseModel):
    """The server reports whether this was the first ping of a visit (a NEW visit joined) or a
    same-session refresh. The client mostly ignores this — it's diagnostic for the seller-console
    Viewing Card, so a future "someone just walked in" pulse can be triggered on `was_new_visit`."""
    ok: Literal[True] = True
    was_new_visit: bool
    last_heartbeat_at: datetime


# ------------------------- live count -------------------------


class LiveCountOut(BaseModel):
    shop_id: str
    live_count: int = Field(ge=0)
    window_seconds: int = Field(gt=0)  # LIVE_WINDOW_SECONDS from the service — echoed back so the FE knows the freshness definition without hard-coding it.


# ------------------------- history -------------------------


class ViewHistoryItem(BaseModel):
    id: str
    viewer_uuid: Optional[str] = None
    session_id: str
    viewed_at: datetime
    last_heartbeat_at: datetime


class ViewHistoryOut(BaseModel):
    items: list[ViewHistoryItem]
    next_cursor: Optional[str] = None


# ------------------------- promote-all -------------------------


# ------------------------- live viewers (hydrated) -------------------------


class LiveViewerOut(BaseModel):
    """One hydrated live viewer for the Viewing Card. §8 Chunk C+.

    Every field except session_id + last_heartbeat_at is optional at the display layer:
    an anonymous viewer has no uuid/avatar/phone; a viewer who denied geolocation has no
    area_label; a viewer not currently on a PDP has no viewing_listing_*; and phone is
    withheld unless the viewer follows this shop (implicit consent via follow).
    """
    session_id: str                              # stable list-key; opaque to the client
    viewer_uuid: Optional[str] = None
    display_name: str                            # 'Guest' when identity unknown — never the bare uuid
    avatar_url: Optional[str] = None
    phone: Optional[str] = None                  # followers-only
    area_label: Optional[str] = None             # e.g. 'Kilimani'; None outside Nairobi metro
    viewing_listing_id: Optional[str] = None
    viewing_listing_title: Optional[str] = None
    last_heartbeat_at: datetime


class LiveViewersOut(BaseModel):
    """Response for GET /shops/{id}/live-viewers — the hydrated list PLUS the count.

    The card renders ``items`` as face+name rows AND shows ``count`` in small type next to
    the 'Viewing' header. Sending both in one payload avoids the FE juggling two queries
    that could disagree by one heartbeat interval.
    """
    shop_id: str
    count: int = Field(ge=0)
    window_seconds: int = Field(gt=0)
    items: list[LiveViewerOut]


# ------------------------- promote-all -------------------------


class PromoteAllOut(BaseModel):
    """The Promote button on the Viewing Card boosts EVERY active listing in the shop back into
    the feed for a caller-chosen window. Returns the summary: how many succeeded, which
    listings failed (if any)."""
    shop_id: str
    promoted_count: int = Field(ge=0)
    skipped_ids: list[str]
    duration_seconds: int = Field(gt=0)
    expires_at: datetime
