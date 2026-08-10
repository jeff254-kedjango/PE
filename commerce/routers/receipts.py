"""Digital-receipt endpoints (§8) — read-only.

Receipts are ISSUED by the settlement service when an order settles (never by a client call),
so this router exposes only reads. Both are gated by ``require_settlement_principal`` (valid
token + O(1) Redis denylist, fail-closed). A receipt is visible only to the sale's buyer or
seller; any other caller — or a not-yet-settled order — gets 404 (no existence leak, S6).
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from PE.commerce.core.auth import CommercePrincipal, require_settlement_principal
from PE.commerce.core.config import settings
from PE.commerce.core.database import get_db
from PE.commerce.schemas import receipt as schemas
from PE.commerce.services import receipts, settlement

router = APIRouter(tags=["receipts"])


@router.get("/orders/{order_id}/receipt", response_model=schemas.ReceiptOut)
def get_order_receipt(
    order_id: str,
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(require_settlement_principal),
) -> schemas.ReceiptOut:
    # Resolve through the order first so a non-party (or an unknown / unsettled order) is a
    # uniform 404 — we never reveal that an order exists, nor that it hasn't settled yet.
    order = settlement.get_order(db, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Receipt not found")
    seller = settlement._seller_for(db, principal.sub)
    if settlement._party_role(order, principal.sub, seller) is None:
        raise HTTPException(status_code=404, detail="Receipt not found")

    rec = receipts.get_by_order(db, order_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Receipt not found")
    return schemas.to_receipt_out(rec)


@router.get("/me/receipts", response_model=schemas.ReceiptPage)
def my_receipts(
    role: str = Query(default="buyer", pattern="^(buyer|seller)$"),
    cursor: str | None = Query(default=None),
    limit: int | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
    principal: CommercePrincipal = Depends(require_settlement_principal),
) -> schemas.ReceiptPage:
    page_size = min(limit or settings.feed_page_size, settings.feed_max_page_size)
    rows, next_cursor = receipts.list_my_receipts(
        db, principal.sub, as_seller=(role == "seller"), cursor=cursor, limit=page_size,
    )
    return schemas.ReceiptPage(
        items=[schemas.to_receipt_out(r) for r in rows], next_cursor=next_cursor,
    )
