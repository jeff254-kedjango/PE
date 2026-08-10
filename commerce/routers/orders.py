"""Settlement / order endpoints — the §6/§7 money path (LEDGER only; stub rail, no live M-Pesa).

EVERY endpoint here is gated by ``require_settlement_principal`` — a valid token PLUS an O(1)
Redis denylist check (fail-closed: denied → 403, denylist down → 503). Every state-changing
POST requires an ``Idempotency-Key`` header so a flaky-network double-tap can't create two
orders or double-accept. Service-layer ``SettlementError``s map to their carried status code;
cross-party access is reported as 404 (no existence leak, S6).
"""
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from PE.commerce.core.auth import CommercePrincipal, require_settlement_principal
from PE.commerce.core.config import settings
from PE.commerce.core.database import get_db
from PE.commerce.schemas import order as schemas
from PE.commerce.services import settlement

router = APIRouter(tags=["orders"])


def _idem_key(idempotency_key: str | None) -> str:
    """Require a non-empty Idempotency-Key header on state-changing requests. 422 if absent —
    a money mutation must always be replay-safe."""
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(status_code=422, detail="Idempotency-Key header is required")
    return idempotency_key.strip()


def _guard(fn):
    """Run a service call, translating a typed SettlementError to its HTTP status code."""
    try:
        return fn()
    except settlement.SettlementError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


def _require_party(db: Session, order, principal: CommercePrincipal):
    """404 unless the caller is the order's buyer or seller (no existence leak)."""
    seller = settlement._seller_for(db, principal.sub)
    if settlement._party_role(order, principal.sub, seller) is None:
        raise HTTPException(status_code=404, detail="Order not found")


# ----------------------------- state-changing -----------------------------

@router.post("/orders", response_model=schemas.OrderOut, status_code=201)
def open_order(
    body: schemas.OrderOpen,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(require_settlement_principal),
) -> schemas.OrderOut:
    key = _idem_key(idempotency_key)
    order = _guard(lambda: settlement.open_order(
        db, principal.sub, body.listing_id, offer_cents=body.offer_cents, idem_key=key,
    ))
    return schemas.to_order_out(order)


@router.post("/orders/{order_id}/counter", response_model=schemas.OrderOut)
def counter(
    order_id: str,
    body: schemas.CounterBody,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(require_settlement_principal),
) -> schemas.OrderOut:
    key = _idem_key(idempotency_key)
    order = _guard(lambda: settlement.counter(
        db, principal.sub, order_id, body.amount_cents, idem_key=key,
    ))
    return schemas.to_order_out(order)


@router.post("/orders/{order_id}/accept", response_model=schemas.OrderOut)
def accept(
    order_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(require_settlement_principal),
) -> schemas.OrderOut:
    key = _idem_key(idempotency_key)
    order = _guard(lambda: settlement.accept(db, principal.sub, order_id, idem_key=key))
    return schemas.to_order_out(order)


@router.post("/orders/{order_id}/cancel", response_model=schemas.OrderOut)
def cancel(
    order_id: str,
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(require_settlement_principal),
) -> schemas.OrderOut:
    # Cancel is naturally idempotent (a re-cancel of a CANCELLED order is a clean 409), so it
    # carries no Idempotency-Key requirement.
    order = _guard(lambda: settlement.cancel(db, principal.sub, order_id))
    return schemas.to_order_out(order)


@router.post("/orders/{order_id}/settle", response_model=schemas.OrderOut)
def settle(
    order_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(require_settlement_principal),
) -> schemas.OrderOut:
    key = _idem_key(idempotency_key)
    order = _guard(lambda: settlement.settle(db, principal.sub, order_id, idem_key=key))
    return schemas.to_order_out(order)


# ----------------------------- reads -----------------------------

@router.get("/orders/{order_id}", response_model=schemas.OrderDetail)
def get_order(
    order_id: str,
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(require_settlement_principal),
) -> schemas.OrderDetail:
    order = settlement.get_order(db, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    _require_party(db, order, principal)
    events = settlement.order_events(db, order_id)
    return schemas.OrderDetail(
        order=schemas.to_order_out(order),
        events=[schemas.to_event_out(e) for e in events],
    )


@router.get("/me/orders", response_model=schemas.OrderPage)
def my_orders(
    role: str = Query(default="buyer", pattern="^(buyer|seller)$"),
    cursor: str | None = Query(default=None),
    limit: int | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(require_settlement_principal),
) -> schemas.OrderPage:
    page_size = min(limit or settings.feed_page_size, settings.feed_max_page_size)
    orders, next_cursor = settlement.list_my_orders(
        db, principal.sub, as_seller=(role == "seller"), cursor=cursor, limit=page_size,
    )
    return schemas.OrderPage(
        items=[schemas.to_order_out(o) for o in orders], next_cursor=next_cursor,
    )
