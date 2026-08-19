"""WeesStock market service (§WeesStock F4) — the investor-facing discovery/analytics surface.

Discovery/analytics ONLY — nothing here transacts. The product goal is a stock-exchange-like
market for SME estate businesses (the NSE serves only the top of the market); this service is
the honest data layer under it: which consenting shops are trading, how much verified money
moved, and the trend. A future investment action belongs to a separate, clearly-labelled,
regulatory-aware surface (Kenya: Capital Markets (Investment-Based Crowdfunding) Regulations
2022), NOT this file.

CONSENT IS THE BOUNDARY. A seller's credit profile is exposed to investors ONLY after they
set ``Seller.weesstock_listed`` (opt-in, default off). The list returns consenting sellers
only, and the detail view returns a uniform 404 for BOTH an unknown id and an unlisted seller
— the API never confirms that an unlisted seller exists (S6).

MONEY IS AGGREGATES ONLY. Same shapes as the credit profile: net-to-seller cents from settled
receipts, never buyer identities, never per-order lines. This is the shape a financier sees.

COST — measured, not assumed. ``list_markets`` runs ``compute_credit_profile`` once per
consenting seller (6 indexed aggregate queries each — the same queries the seller's own card
pays) plus ONE windowed receipt scan per seller for the weekly series. That is deliberate:
reusing the scorer verbatim makes the market number and the seller's own number THE SAME by
construction (a market that shows a different score than the seller's card would be a lie in
one of two places), and the population is bounded by ``MARKET_LIST_MAX``. At the current dev
scale (7 sellers) that is ~50 indexed queries per poll of a non-hot, investor-facing read.
When the market grows past a few dozen sellers, the aggregates can be batched into grouped
queries behind a parity test (list score must equal the per-seller scorer) — not before.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from PE.commerce.models.receipt import Receipt
from PE.commerce.models.seller import Seller, Shop
from PE.commerce.schemas.weesstock_market import MarketEntryOut, MarketSeller, RevenueSeries
from PE.commerce.services import credit_score

# Hard bound on the market list: an investor poll must be O(1) in the size of the platform.
MARKET_LIST_MAX = 100
# Weekly buckets over the revenue window: 90 / 7 = 12 full weeks + the current partial one.
_SERIES_BUCKET_DAYS = 7


def get_listed(db: Session, user_uuid: str) -> bool | None:
    """Read the caller's own WeesStock market consent. Returns None when the caller has no
    seller row (uniform 404 at the router — identical to the write half)."""
    seller = db.query(Seller).filter(Seller.user_uuid == user_uuid).one_or_none()
    return seller.weesstock_listed if seller is not None else None


def set_listed(db: Session, user_uuid: str, listed: bool) -> bool | None:
    """Flip the caller's own WeesStock market consent. Returns the new state, or None when
    the caller has no seller row (nothing to list — uniform 404 at the router)."""
    seller = db.query(Seller).filter(Seller.user_uuid == user_uuid).one_or_none()
    if seller is None:
        return None
    seller.weesstock_listed = listed
    db.commit()
    return listed


def _revenue_series(db: Session, seller_id: str, now: datetime) -> RevenueSeries:
    """Weekly net-revenue buckets over the 90-day window, oldest→newest.

    One indexed windowed scan of the seller's own receipts; buckets are aligned to ``now``
    (bucket 0 = the current, partial week). Money is net-to-seller cents — the same number
    the scorer uses, so the chart can never disagree with the score's revenue term.
    """
    window_start = now - timedelta(days=credit_score.REVENUE_WINDOW_DAYS)
    rows = (
        db.query(Receipt.issued_at, Receipt.net_to_seller_cents)
        .filter(Receipt.seller_id == seller_id, Receipt.issued_at >= window_start)
        .all()
    )
    bucket_count = -(-credit_score.REVENUE_WINDOW_DAYS // _SERIES_BUCKET_DAYS)  # ceil → 13
    buckets = [0] * bucket_count
    for issued_at, cents in rows:
        if issued_at is None:
            continue
        idx = (now - issued_at).days // _SERIES_BUCKET_DAYS
        if 0 <= idx < bucket_count:
            buckets[bucket_count - 1 - idx] += int(cents or 0)
    return RevenueSeries(
        series_cents=buckets,
        bucket_days=_SERIES_BUCKET_DAYS,
        bucket_count=bucket_count,
        window_days=credit_score.REVENUE_WINDOW_DAYS,
        currency=_currency_of(db, seller_id, window_start),
    )


def _currency_of(db: Session, seller_id: str, window_start: datetime) -> str:
    """Dominant receipt currency in the window (mirrors the scorer's rule)."""
    row = (
        db.query(Receipt.currency, func.count(Receipt.id))
        .filter(Receipt.seller_id == seller_id, Receipt.issued_at >= window_start)
        .group_by(Receipt.currency)
        .order_by(func.count(Receipt.id).desc(), Receipt.currency.asc())
        .first()
    )
    return str(row[0]) if row else "KES"


def _primary_shop(db: Session, seller_id: str) -> Shop | None:
    """The seller's first-created shop — the market's display identity. A seller always has
    at least one shop when they can trade; None is defensive."""
    return (
        db.query(Shop)
        .filter(Shop.seller_id == seller_id)
        .order_by(Shop.created_at, Shop.id)
        .first()
    )


def list_markets(db: Session, *, now: datetime) -> list[MarketEntryOut]:
    """Every consenting seller's market row, strongest-first (scoreable, then by score, then
    name) and capped at MARKET_LIST_MAX. Deterministic ordering — a re-poll reads the same."""
    sellers = (
        db.query(Seller)
        .filter(Seller.weesstock_listed.is_(True))
        .order_by(Seller.created_at, Seller.id)
        .limit(MARKET_LIST_MAX)
        .all()
    )
    entries: list[MarketEntryOut] = []
    for seller in sellers:
        profile = credit_score.compute_credit_profile(db, seller, now=now)
        signals = profile.signals
        shop = _primary_shop(db, str(seller.id))
        entries.append(MarketEntryOut(
            seller_id=str(seller.id),
            seller_name=seller.display_name,
            shop_name=shop.name if shop else seller.display_name,
            category=shop.category if shop else None,
            score=profile.score,
            is_scoreable=profile.is_scoreable,
            currency=signals.currency,
            revenue_cents=signals.revenue_cents,
            revenue_trend=signals.revenue_trend,
            rating=signals.rating,
            rating_count=signals.rating_count,
            series=_revenue_series(db, str(seller.id), now),
        ))
    entries.sort(key=lambda e: (
        0 if e.is_scoreable else 1,
        -(e.score or 0.0),
        e.seller_name.lower(),
    ))
    return entries


def market_detail(db: Session, seller_id: str, *, now: datetime):
    """One seller's full market deep-dive, or None when the seller is unknown OR unlisted.

    The uniform-None collapse is the no-existence-leak property: an investor probing ids must
    not be able to tell "never existed" from "exists but hasn't consented".
    """
    seller = db.query(Seller).filter(Seller.id == seller_id).one_or_none()
    if seller is None or not seller.weesstock_listed:
        return None
    profile = credit_score.compute_credit_profile(db, seller, now=now)
    shop = _primary_shop(db, str(seller.id))
    return (
        MarketSeller(
            seller_id=str(seller.id),
            seller_name=seller.display_name,
            shop_name=shop.name if shop else seller.display_name,
            category=shop.category if shop else None,
        ),
        profile,
        _revenue_series(db, str(seller.id), now),
    )
