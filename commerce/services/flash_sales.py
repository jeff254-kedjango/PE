"""Flash Sales — the §8 nationwide "crazy offer" grid under Quick Buys.

A seller launches a one-hour-max crazy offer ("Bread for 10 KES", "Jordans for 100 KES"). Unlike
every other Trade surface this is NATIONWIDE: a Kisumu flash sale shows in Nairobi. Offers are
ranked by "craziness" = a MARGIN score: how far the offer undercuts the average price of comparable
shops. Two properties make this cheap and honest:

  * PERF (the competitive edge). The comparable pull happens ONCE, at launch, and is BOUNDED
    (LIMIT k on an indexed same-category / radius query — never a table scan). The resulting margin
    is stored on the listing as ``flash_score``; the nationwide READ is then a pure indexed
    ``ORDER BY flash_score DESC LIMIT N`` over the tiny active-window set → O(log n + N).

  * NO SWEEP. The flash price is a TEMPORARY OVERRIDE evaluated purely from the stored window vs
    now() — exactly like ranking.promo_boost / proximity._not_expired_story. ``price_cents`` is
    NEVER mutated, so the normal price reverts by itself the instant the window closes (auto-restore
    for free), and an expired sale simply stops matching the window predicate (expire-and-vanish for
    free). No job, no materialization.

The comparable basis (the seller's locked design): same-category listings NEAR the seller's own
shop, refined by product-title keyword overlap; if too few nearby, fall back to nationwide
same-category. All money is integer cents (S9).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import Float, and_, cast
from sqlalchemy.orm import Session

from PE.commerce.core.config import settings
from PE.commerce.models.listing import Listing
from PE.commerce.models.seller import Seller, Shop
from PE.commerce.services import proximity, quick_buys, ranking

# Tokens shorter than this are dropped from the title-overlap refine (articles, units like "kg"
# would over-match). Kept small so real product words ("jordan", "bread", "sugar") survive.
_MIN_TOKEN_LEN = 3


class FlashSaleError(ValueError):
    """Bad flash-sale input (out-of-bounds duration, non-positive price, a price that isn't a
    discount, or a bargain listing). Router → 422."""


@dataclass(frozen=True)
class FlashSaleRow:
    """One composed flash-sale item: the listing ORM row + the buyer-relative distance (display
    only) + its craziness score. The router maps this to the lean FlashSaleItem DTO (no POS/PII)."""
    listing: object            # Listing ORM row
    distance_m: float | None   # display-only; None when the caller gave no location
    score: float


def _tokens(title: str) -> set[str]:
    """Lowercased word tokens of a title, dropping very short ones — for the keyword-overlap refine.
    Pure string work over one title (O(len))."""
    return {t for t in re.split(r"[^a-z0-9]+", (title or "").lower()) if len(t) >= _MIN_TOKEN_LEN}


def _owned_listing(db: Session, listing_id: str, user_uuid: str) -> Listing | None:
    """The listing, only if owned by user_uuid; else None (router → 404, no existence leak, S6).
    Mirrors catalog._owned_listing — single indexed join on the caller's seller."""
    return (
        db.query(Listing)
        .join(Seller, Listing.seller_id == Seller.id)
        .filter(Listing.id == listing_id, Seller.user_uuid == user_uuid)
        .one_or_none()
    )


def _shop_category(db: Session, shop_id: str) -> str | None:
    """The category slug of a shop (None when un-categorised). One indexed PK lookup."""
    return db.query(Shop.category).filter(Shop.id == shop_id).scalar()


def _comparable_prices(db: Session, listing, now: datetime) -> list[int]:
    """Prices of up to ``flash_sales_reference_sample`` comparable listings for the margin, chosen
    from a BOUNDED candidate pull (never a scan):

      1. NEAR + same category: same-category listings within ``comparable_radius_m`` of the seller's
         own shop, active + buyable, nearest first, LIMIT k. (Category lives on the SHOP, so we
         resolve the category → its shops via the same ix_shops_category path Quick Buys uses.)
      2. KEYWORD refine: among those ≤ k rows, prefer the ones whose title shares a word with this
         listing's title (pure Python over the bounded set — O(k), no SQL LIKE).
      3. NATIONWIDE fallback: if fewer than ``min_comparables`` near, widen to same-category
         platform-wide, newest first, LIMIT k.

    Returns the chosen comparables' prices (may be empty — the caller then falls back to the
    listing's own price so a score is always computable)."""
    category = _shop_category(db, str(listing.shop_id))
    k = settings.flash_sales_comparable_limit
    sample = settings.flash_sales_reference_sample

    # A listing whose shop is un-categorised has no meaningful comparable set — skip straight to the
    # empty result (the caller uses the listing's own price as the reference).
    if category is None:
        return []

    category_shop_ids = quick_buys._shop_ids_for_categories(db, (category,))
    if not category_shop_ids:
        return []

    self_id = str(listing.id)
    common = [
        Listing.shop_id.in_(category_shop_ids),
        Listing.is_active.is_(True),
        proximity._buyable_or_post(),
        Listing.id != self_id,
    ]

    # 1) NEAR, same category (bounded by radius + LIMIT k).
    predicate, distance = proximity.within_clause(
        db, listing.lat, listing.lng, settings.flash_sales_comparable_radius_m
    )
    near_rows = (
        db.query(Listing)
        .filter(predicate, *common)
        .order_by(cast(distance, Float).asc())
        .limit(k)
        .all()
    )

    candidates = near_rows
    # 3) NATIONWIDE fallback when the near set is too thin (bounded by LIMIT k).
    if len(candidates) < settings.flash_sales_min_comparables:
        wide_rows = (
            db.query(Listing)
            .filter(*common)
            .order_by(Listing.created_at.desc())
            .limit(k)
            .all()
        )
        # Merge, de-duplicating by id (near rows kept first — they are the truest "around that area"
        # comparables). Bounded: |near| + |wide| ≤ 2k.
        seen = {str(r.id) for r in candidates}
        for r in wide_rows:
            if str(r.id) not in seen:
                seen.add(str(r.id))
                candidates.append(r)

    if not candidates:
        return []

    # 2) KEYWORD refine: prefer comparables sharing a title word with this listing (O(k)).
    want = _tokens(listing.title)
    if want:
        matched = [r for r in candidates if _tokens(r.title) & want]
        if len(matched) >= settings.flash_sales_min_comparables:
            candidates = matched
        elif matched:
            # Some keyword matches but not enough on their own — float them to the front so they
            # dominate the sample, then top up with the rest of the (already-ordered) candidates.
            rest = [r for r in candidates if r not in matched]
            candidates = matched + rest

    return [int(r.price_cents) for r in candidates[:sample]]


def compute_flash_score(db: Session, listing, flash_price_cents: int, now: datetime) -> tuple[float, int]:
    """The craziness margin + the reference (comparable average), computed ONCE at launch. Returns
    ``(score, reference_cents)`` where score ∈ [0, 1] (higher = crazier). With no comparables the
    reference degrades to the listing's own price (so a launch never fails on thin supply — the
    score is then 0 and the non-discount guard in ``launch_flash_sale`` rejects it anyway)."""
    prices = _comparable_prices(db, listing, now)
    if prices:
        reference = sum(prices) // len(prices)
    else:
        reference = int(listing.price_cents)
    if reference <= 0:
        return 0.0, reference
    margin = (reference - flash_price_cents) / reference
    score = max(0.0, min(1.0, margin))
    return score, reference


def launch_flash_sale(
    db: Session, user_uuid: str, listing_id: str, *, flash_price_cents: int,
    duration_seconds: int, now: datetime | None = None,
) -> Listing | None:
    """Open (or replace) a flash-sale window on the caller's listing. Returns the updated Listing,
    None if not owned (router → 404), or raises FlashSaleError (router → 422) on:
      * a duration outside [min, 3600] (the 1-hour hard cap — then it vanishes and the seller
        re-launches; there is no auto-renew);
      * a non-positive price;
      * a BARGAIN listing (a flash sale is a one-tap "buy now"; bargain pricing has its own
        negotiation + reference ceiling, so we keep the two apart and the settlement invariant
        clean — see services.settlement);
      * a price that isn't actually a discount (>= the comparable reference) — a "flash sale" that
        isn't cheaper is noise/abuse.

    The margin is computed here and STORED, so the nationwide read never recomputes it. Re-launching
    overwrites the existing window + recomputes the score (re-launch = extend, like promote_listing).
    ``price_cents`` is deliberately left untouched — the override is purely the stored window."""
    if not (settings.flash_sales_min_duration_seconds <= duration_seconds <= settings.flash_sales_max_duration_seconds):
        raise FlashSaleError(
            f"duration_seconds must be between {settings.flash_sales_min_duration_seconds} and "
            f"{settings.flash_sales_max_duration_seconds}"
        )
    if flash_price_cents <= 0:
        raise FlashSaleError("flash_price_cents must be a positive amount")

    listing = _owned_listing(db, listing_id, user_uuid)
    if listing is None:
        return None
    if listing.pricing_mode == "bargain":
        raise FlashSaleError("a flash sale can only run on a fixed-price listing")

    now = now or datetime.now(timezone.utc)
    score, reference = compute_flash_score(db, listing, flash_price_cents, now)
    if flash_price_cents >= reference:
        # Not a discount vs comparable shops → not a flash sale.
        raise FlashSaleError("flash_price_cents must be below the comparable market price")

    listing.flash_price_cents = flash_price_cents
    listing.flash_started_at = now
    listing.flash_expires_at = now + timedelta(seconds=duration_seconds)
    listing.flash_score = score
    listing.flash_reference_cents = reference
    db.commit()
    db.refresh(listing)
    return listing


def clear_flash_sale(db: Session, user_uuid: str, listing_id: str) -> Listing | None:
    """Remove any flash sale from the caller's listing (back to its ordinary price). Returns the
    updated Listing, or None if not owned (router → 404). Idempotent: clearing a listing with no
    flash sale is a clean no-op."""
    listing = _owned_listing(db, listing_id, user_uuid)
    if listing is None:
        return None
    listing.flash_price_cents = None
    listing.flash_started_at = None
    listing.flash_expires_at = None
    listing.flash_score = None
    listing.flash_reference_cents = None
    db.commit()
    db.refresh(listing)
    return listing


def active_flash_price(listing, now: datetime | None = None) -> int | None:
    """The flash price a buyer pays RIGHT NOW, or None when no window is open. Pure O(1) — the
    temporary override evaluated from the stored window vs now(), so it reverts by itself the moment
    the window closes (no write). Timestamps are normalized through ranking._as_utc for the naive
    (SQLite) vs aware (Postgres) mismatch, exactly like promo_boost."""
    if listing.flash_price_cents is None or listing.flash_expires_at is None or listing.flash_started_at is None:
        return None
    now = ranking._as_utc(now or datetime.now(timezone.utc))
    started = ranking._as_utc(listing.flash_started_at)
    expires = ranking._as_utc(listing.flash_expires_at)
    if started <= now < expires:
        return int(listing.flash_price_cents)
    return None


def _active_flash(now: datetime):
    """SQL predicate: the flash window is currently open. Rides ix_listings_flash_expires /
    ix_listings_flash_active. Mirrors proximity._not_expired_story's time-filter shape."""
    return and_(
        Listing.flash_expires_at.isnot(None),
        Listing.flash_started_at <= now,
        Listing.flash_expires_at > now,
    )


def build_flash_sales(
    db: Session, now: datetime | None = None, *, limit: int | None = None,
    lat: float | None = None, lng: float | None = None,
) -> list[FlashSaleRow]:
    """The NATIONWIDE flash-sale slate: every active-window, buyable flash sale on the platform,
    ranked by ``flash_score`` (craziness) descending, capped at ``flash_sales_max``. No geo filter —
    a far sale is meant to appear. The read is a pure indexed ORDER BY over the small active set →
    O(log n + N), never a comparison scan.

    ``lat``/``lng`` (optional) add a DISPLAY-ONLY buyer-relative distance; they never filter or
    re-rank (that would defeat the nationwide contract)."""
    now = now or datetime.now(timezone.utc)
    limit = limit if limit is not None else settings.flash_sales_max

    if lat is not None and lng is not None:
        _, distance = proximity.within_clause(db, lat, lng, 1.0)  # radius unused; take the distance expr
        rows = (
            db.query(Listing, distance.label("distance_m"))
            .filter(
                _active_flash(now),
                Listing.is_active.is_(True),
                proximity._buyable_or_post(),
            )
            .order_by(Listing.flash_score.desc(), Listing.id.asc())
            .limit(limit)
            .all()
        )
        return [FlashSaleRow(listing=r[0], distance_m=float(r[1]), score=float(r[0].flash_score or 0.0))
                for r in rows]

    listings = (
        db.query(Listing)
        .filter(
            _active_flash(now),
            Listing.is_active.is_(True),
            proximity._buyable_or_post(),
        )
        .order_by(Listing.flash_score.desc(), Listing.id.asc())
        .limit(limit)
        .all()
    )
    return [FlashSaleRow(listing=li, distance_m=None, score=float(li.flash_score or 0.0)) for li in listings]
