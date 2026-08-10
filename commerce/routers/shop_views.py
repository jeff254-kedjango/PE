"""Shop-view + promote-all router (§8, Chunk C) — the Viewing Card's endpoints.

Four endpoints, three trust levels:

  * ``POST /shops/{shop_id}/heartbeat``    — ANONYMOUS callable. Any visitor pings this every
    30s while watching the storefront. A signed-in viewer's ``sub`` is captured for the
    history list; an anonymous viewer's row keeps ``viewer_uuid = NULL`` (see the service).

  * ``GET  /shops/{shop_id}/live-count``   — owner-only (``create:trades``). Counts distinct
    heartbeats fresh within the last 60s. Response includes the freshness window so the FE
    doesn't need to hard-code the definition.

  * ``GET  /shops/{shop_id}/view-history`` — owner-only. Keyset-paginated by (viewed_at, id)
    DESC. Optional ``since`` / ``until`` query params drive the calendar filter.

  * ``POST /shops/{shop_id}/promote-all``  — owner-only. Boosts every active, in-stock listing
    in the shop for the requested duration (evergreen mode). Returns a summary. Payment
    integration is DEFERRED — the endpoint is free today so the FE can wire the button.

The heartbeat is the only endpoint that fails-OPEN to anon callers. Owner endpoints follow
the same shape as every other seller-owner route: `create:trades` scope + a join-based
ownership check so no cross-owner existence leak.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt

from PE.commerce.core.auth import CommercePrincipal, get_current_principal, require_scope
from PE.commerce.core.config import settings
from PE.commerce.core.database import get_db
from PE.commerce.models.seller import Seller, Shop
from PE.commerce.schemas import shop_views as shop_view_schemas
from PE.commerce.services import live_viewers as live_svc
from PE.commerce.services import shop_views as svc

# Anon-tolerant bearer — the heartbeat endpoint fails-OPEN (a signed-out viewer must be able
# to ping). Signed-in tokens are still verified with the FULL discipline of the shared
# `get_current_principal`; we just don't demand one exists.
_anon_bearer = HTTPBearer(auto_error=False)


def _optional_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_anon_bearer),
) -> Optional[CommercePrincipal]:
    """Optional-auth dependency for the heartbeat endpoint. Returns None for an anonymous
    caller; a signed-in caller's principal for a valid commerce token; and — importantly —
    also returns None for a MALFORMED token rather than 401ing. Rationale: a heartbeat that
    can't be attributed to a user is still a heartbeat; erroring out because the client's
    token happened to expire would drop live-count visibility for no gain to the seller.

    A signed-in caller whose token was minted by the wrong service (weespas access token,
    say) also folds into anon — same reasoning."""
    if not settings.auth_enabled or credentials is None or not credentials.credentials:
        return None
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.commerce_jwt_public_key,
            algorithms=[settings.commerce_jwt_algorithm],
        )
    except jwt.PyJWTError:
        return None
    sub = payload.get("sub")
    if not sub:
        return None
    # We DO NOT check the commerce scope here — a token that fails the audience guard collapses
    # to anon rather than raising. The heartbeat cares only about a stable sub, and this is a
    # strictly-additive attribution, never a permission grant.
    scopes: list[str] = []
    for k in ("scopes", "scope"):
        v = payload.get(k)
        if isinstance(v, (list, tuple)):
            scopes.extend(str(s) for s in v)
        elif v:
            scopes.append(str(v))
    return CommercePrincipal(
        sub=str(sub),
        role=str(payload.get("role", "user")),
        scopes=tuple(scopes),
        name=str(payload.get("name", "")),
    )

router = APIRouter(prefix="/shops", tags=["shop-views"])

_require_write = require_scope("create:trades")


def _owned_shop_or_404(db: Session, shop_id: str, user_uuid: str) -> Shop:
    """Uniform ownership check with a uniform 404 for both "shop doesn't exist" AND "shop
    isn't yours" — no cross-owner existence leak (S6)."""
    shop = (
        db.query(Shop)
        .join(Seller, Shop.seller_id == Seller.id)
        .filter(Shop.id == shop_id, Seller.user_uuid == user_uuid)
        .one_or_none()
    )
    if shop is None:
        raise HTTPException(status_code=404, detail="Shop not found")
    return shop


def _shop_exists_or_404(db: Session, shop_id: str) -> Shop:
    """Anon-callable check for the heartbeat endpoint — the shop must exist, but the caller
    doesn't need to be the owner (or signed in at all)."""
    shop = db.query(Shop).filter(Shop.id == shop_id).one_or_none()
    if shop is None:
        raise HTTPException(status_code=404, detail="Shop not found")
    return shop


# ---------------------------------------------------------------------------
# Heartbeat — anonymous callable
# ---------------------------------------------------------------------------

@router.post(
    "/{shop_id}/heartbeat",
    response_model=shop_view_schemas.HeartbeatOut,
    status_code=200,
)
def record_heartbeat(
    body: shop_view_schemas.HeartbeatIn,
    shop_id: str = Path(..., min_length=1),
    db: Session = Depends(get_db),
    # Optional bearer — if the caller IS signed in we capture their sub on the first ping.
    # We don't gate anonymous heartbeats; anon coverage was one of the user's explicit asks.
    principal: Optional[CommercePrincipal] = Depends(_optional_principal),
) -> shop_view_schemas.HeartbeatOut:
    _shop_exists_or_404(db, shop_id)
    try:
        outcome = svc.record_heartbeat(
            db,
            shop_id=shop_id,
            session_id=body.session_id,
            viewer_uuid=principal.sub if principal else None,
            now=datetime.now(timezone.utc),
            viewing_listing_id=body.viewing_listing_id,
            last_lat=body.last_lat,
            last_lng=body.last_lng,
        )
    except svc.HeartbeatError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return shop_view_schemas.HeartbeatOut(
        was_new_visit=outcome.was_new_visit,
        last_heartbeat_at=outcome.event.last_heartbeat_at,
    )


# ---------------------------------------------------------------------------
# Live count — owner-only
# ---------------------------------------------------------------------------

@router.get(
    "/{shop_id}/live-count",
    response_model=shop_view_schemas.LiveCountOut,
)
def live_count(
    shop_id: str = Path(..., min_length=1),
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(_require_write),
) -> shop_view_schemas.LiveCountOut:
    _owned_shop_or_404(db, shop_id, principal.sub)
    now = datetime.now(timezone.utc)
    count = svc.count_live_viewers(db, shop_id=shop_id, now=now)
    return shop_view_schemas.LiveCountOut(
        shop_id=shop_id,
        live_count=count,
        window_seconds=svc.LIVE_WINDOW_SECONDS,
    )


# ---------------------------------------------------------------------------
# Live viewers (hydrated) — owner-only
# ---------------------------------------------------------------------------

@router.get(
    "/{shop_id}/live-viewers",
    response_model=shop_view_schemas.LiveViewersOut,
)
def live_viewers(
    shop_id: str = Path(..., min_length=1),
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(_require_write),
) -> shop_view_schemas.LiveViewersOut:
    """The Viewing Card's face+name+area+product rows for viewers currently on the shop.

    Owner-only (same ownership check as live-count / history — uniform 404 for
    'no such shop' and 'not your shop'). Returns 200 with an empty ``items`` when the
    shop is quiet — the endpoint never 404s on 'no live viewers'.

    Hydration failures (bridge outage, missing weespas record) DEGRADE — the row for
    that viewer shows 'Guest' but the endpoint still returns. Never 5xx on a bridge glitch.
    """
    _owned_shop_or_404(db, shop_id, principal.sub)
    now = datetime.now(timezone.utc)
    hydrated = live_svc.get_hydrated_live_viewers(db, shop_id=shop_id, now=now)
    return shop_view_schemas.LiveViewersOut(
        shop_id=shop_id,
        count=len(hydrated),
        window_seconds=svc.LIVE_WINDOW_SECONDS,
        items=[
            shop_view_schemas.LiveViewerOut(
                session_id=v.session_id,
                viewer_uuid=v.viewer_uuid,
                display_name=v.display_name,
                avatar_url=v.avatar_url,
                phone=v.phone,
                area_label=v.area_label,
                viewing_listing_id=v.viewing_listing_id,
                viewing_listing_title=v.viewing_listing_title,
                last_heartbeat_at=v.last_heartbeat_at,
            )
            for v in hydrated
        ],
    )


# ---------------------------------------------------------------------------
# View history — owner-only, keyset-paginated
# ---------------------------------------------------------------------------

@router.get(
    "/{shop_id}/view-history",
    response_model=shop_view_schemas.ViewHistoryOut,
)
def view_history(
    shop_id: str = Path(..., min_length=1),
    since: Optional[datetime] = Query(default=None),
    until: Optional[datetime] = Query(default=None),
    cursor: Optional[str] = Query(default=None),
    limit: int = Query(default=50, gt=0, le=200),
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(_require_write),
) -> shop_view_schemas.ViewHistoryOut:
    _owned_shop_or_404(db, shop_id, principal.sub)
    page = svc.list_view_history(
        db, shop_id=shop_id, since=since, until=until, cursor=cursor, limit=limit,
    )
    return shop_view_schemas.ViewHistoryOut(
        items=[
            shop_view_schemas.ViewHistoryItem(
                id=r.event_id,
                viewer_uuid=r.viewer_uuid,
                session_id=r.session_id,
                viewed_at=r.viewed_at,
                last_heartbeat_at=r.last_heartbeat_at,
            )
            for r in page.rows
        ],
        next_cursor=page.next_cursor,
    )


# ---------------------------------------------------------------------------
# Promote-all — owner-only, free stub (payment integration deferred)
# ---------------------------------------------------------------------------

@router.post(
    "/{shop_id}/promote-all",
    response_model=shop_view_schemas.PromoteAllOut,
    status_code=200,
)
def promote_all(
    shop_id: str = Path(..., min_length=1),
    duration_seconds: int = Query(..., gt=0),
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(_require_write),
) -> shop_view_schemas.PromoteAllOut:
    try:
        result = svc.promote_all_active_listings(
            db,
            shop_id=shop_id,
            user_uuid=principal.sub,
            duration_seconds=duration_seconds,
            now=datetime.now(timezone.utc),
        )
    except svc.PromoteAllError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if result is None:
        # Not the caller's shop (or doesn't exist). Uniform 404 — no cross-owner existence leak.
        raise HTTPException(status_code=404, detail="Shop not found")
    return shop_view_schemas.PromoteAllOut(
        shop_id=result.shop_id,
        promoted_count=result.promoted_count,
        skipped_ids=result.skipped_ids,
        duration_seconds=duration_seconds,
        expires_at=result.expires_at,
    )
