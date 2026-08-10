"""§8.3 per-shop sponsored-cap OVERRIDE service (item 1).

A shop may APPLY for an absolute per-shop sponsored cap that STAFF approve, overriding the global
``settings.feed_sponsored_max_per_shop`` for that shop only. The pieces:

  * ``resolve_caps`` — hot-path read: for the bounded set of shops present in the sponsored lane,
    return {shop_id: approved_cap} for the ones with an APPROVED override (positive cap). One
    indexed ``IN`` query, O(k) in the number of distinct sponsored shops — no N+1.
  * ``apply_for_override`` — a seller applies for their OWN shop (cross-owner ⇒ None ⇒ router 404,
    no existence leak). Idempotent UPSERT on the one-row-per-shop unique constraint: re-applying
    updates the request and resets it to ``pending`` (a fresh ask must be re-approved).
  * ``decide_override`` / ``list_pending`` — staff-only (the router gates the role via
    ``_require_staff``; these accept an already-authorised principal).

Semantics are deliberately narrow so the feed can never be surprised: an override affects ranking
ONLY when ``status == 'approved'`` AND ``approved_cap`` is a positive int. Every other state is
inert and the shop falls back to the global default.
"""
from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from PE.commerce.core.config import settings
from PE.commerce.core.database import utcnow
from PE.commerce.models.seller import Seller, Shop, ShopSponsoredCapOverride


def _owned_shop_id(db: Session, shop_id: str, user_uuid: str) -> str | None:
    """The shop id, only if ``shop_id`` is owned by ``user_uuid``; else None (router → 404, no
    existence leak). Single indexed join on the caller's seller — O(log n)."""
    return (
        db.query(Shop.id)
        .join(Seller, Shop.seller_id == Seller.id)
        .filter(Shop.id == shop_id, Seller.user_uuid == user_uuid)
        .scalar()
    )


def get_override(
    db: Session, user_uuid: str, shop_id: str
) -> tuple[bool, ShopSponsoredCapOverride | None]:
    """Non-destructive read of a shop's override, for the seller-facing status control. Returns
    ``(owned, row)``:

      * ``(False, None)`` — the shop isn't owned by the caller (router → 404, no existence leak).
      * ``(True, None)`` — owned, but the seller has never applied (no row yet).
      * ``(True, <row>)`` — owned, returns the current request/decision.

    Purely a read — CRITICALLY it must NEVER write, so opening the control can't knock an already
    ``approved`` override back to ``pending`` (which is exactly what ``apply_for_override`` does).
    One indexed ownership check + one indexed point read — O(log n)."""
    if _owned_shop_id(db, shop_id, user_uuid) is None:
        return (False, None)
    row = (
        db.query(ShopSponsoredCapOverride)
        .filter(ShopSponsoredCapOverride.shop_id == shop_id)
        .one_or_none()
    )
    return (True, row)


def resolve_caps(db: Session, shop_ids: set[str]) -> dict[str, int]:
    """Return {shop_id: approved_cap} for the subset of ``shop_ids`` that carry an APPROVED
    override with a positive cap. Shops without such an override are simply absent (the caller
    falls back to the global default). One bounded ``IN`` query — O(k), no N+1.

    Empty input short-circuits so the sponsored-lane fast path issues no query when there are no
    sponsored shops (keeps a boost-free feed byte-identical to before this feature)."""
    if not shop_ids:
        return {}
    rows = (
        db.query(ShopSponsoredCapOverride.shop_id, ShopSponsoredCapOverride.approved_cap)
        .filter(
            ShopSponsoredCapOverride.shop_id.in_(shop_ids),
            ShopSponsoredCapOverride.status == "approved",
            ShopSponsoredCapOverride.approved_cap.isnot(None),
            ShopSponsoredCapOverride.approved_cap > 0,
        )
        .all()
    )
    return {shop_id: cap for shop_id, cap in rows}


def apply_for_override(
    db: Session, user_uuid: str, shop_id: str, requested_cap: int
) -> ShopSponsoredCapOverride | None:
    """A seller applies for a per-shop absolute sponsored cap on their OWN shop. Returns the row,
    or None if the shop isn't owned by the caller (router → 404, no existence leak).

    ``requested_cap`` is clamped to [1, settings.boost_cap_override_max] — the service is the
    authority on the ceiling even though the schema also edge-bounds it. Idempotent UPSERT on the
    one-row-per-shop unique constraint: an existing record is re-set to ``pending`` with the new
    requested cap and its prior decision cleared (a new ask must be re-approved)."""
    if _owned_shop_id(db, shop_id, user_uuid) is None:
        return None

    capped = max(1, min(int(requested_cap), settings.boost_cap_override_max))

    row = (
        db.query(ShopSponsoredCapOverride)
        .filter(ShopSponsoredCapOverride.shop_id == shop_id)
        .one_or_none()
    )
    if row is None:
        row = ShopSponsoredCapOverride(shop_id=shop_id, requested_cap=capped, status="pending")
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            # A concurrent apply for the same shop won the unique constraint — reload and update
            # that row instead (idempotent), never surface a 500 for a benign race.
            db.rollback()
            row = (
                db.query(ShopSponsoredCapOverride)
                .filter(ShopSponsoredCapOverride.shop_id == shop_id)
                .one()
            )
            _reset_pending(row, capped)
            db.commit()
    else:
        _reset_pending(row, capped)
        db.commit()
    db.refresh(row)
    return row


def _reset_pending(row: ShopSponsoredCapOverride, requested_cap: int) -> None:
    """Re-open an existing override as a fresh pending request — clears any prior decision so a
    re-application cannot silently ride on a stale approval."""
    row.requested_cap = requested_cap
    row.status = "pending"
    row.approved_cap = None
    row.decided_by = None
    row.decided_at = None
    row.updated_at = utcnow()


def decide_override(
    db: Session,
    staff_sub: str,
    override_id: str,
    approve: bool,
    approved_cap: int | None = None,
) -> ShopSponsoredCapOverride | None:
    """Staff decision on a pending override (the router has already gated the role). Returns the
    updated row, or None if the id doesn't exist.

    On approve: ``approved_cap`` (defaulting to the requested cap) is clamped to
    [1, settings.boost_cap_override_max] and status → 'approved'. On reject: status → 'rejected'
    and any approved_cap is cleared. ``staff_sub`` + timestamp are snapshotted for audit."""
    row = (
        db.query(ShopSponsoredCapOverride)
        .filter(ShopSponsoredCapOverride.id == override_id)
        .one_or_none()
    )
    if row is None:
        return None

    if approve:
        cap = approved_cap if approved_cap is not None else row.requested_cap
        row.approved_cap = max(1, min(int(cap), settings.boost_cap_override_max))
        row.status = "approved"
    else:
        row.approved_cap = None
        row.status = "rejected"
    row.decided_by = staff_sub
    row.decided_at = utcnow()
    row.updated_at = utcnow()
    db.commit()
    db.refresh(row)
    return row


def list_pending(db: Session, limit: int = 100) -> list[ShopSponsoredCapOverride]:
    """Bounded read of pending override applications for the staff review queue, oldest first
    (FIFO fairness). ``limit`` is clamped to a sane page so a bad caller can't request the world."""
    limit = max(1, min(int(limit), 500))
    return (
        db.query(ShopSponsoredCapOverride)
        .filter(ShopSponsoredCapOverride.status == "pending")
        .order_by(ShopSponsoredCapOverride.created_at.asc())
        .limit(limit)
        .all()
    )
