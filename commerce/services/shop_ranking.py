"""Shop ranking service (§8) — where does the caller's shop sit against local peers?

The signal set is a HYBRID:
  * Sales/revenue in a recent window (weighted 0.6) — the dominant term. A shop that actually
    transacts outranks one with impressive follower/save numbers but zero settled orders.
  * A composite of "soft" quality signals — rating, follower count, saves-across-listings,
    freshness (weighted 0.4). These handle the cold-start case: a brand-new shop with no
    settled orders still gets a rank rather than always tying at the bottom.

Every signal is **normalized WITHIN THE PEER SET** (dividing by the max value in the same
radius) before being weighted. That means the score is a *relative* number in [0, 1] against
the shop's neighborhood — not a global one. This is deliberate: the ranking is the seller
asking "how am I doing in MY area", not "am I the best shop in Kenya".

The service is a PURE function of stored rows + a `now` timestamp — deterministic and
explainable. The endpoint layer (routers/sellers.py or a new routers/ranking.py) is
responsible for authentication, the >200 km paywall, and the 5-minute cache; this file
computes the numbers.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from PE.commerce.models.engagement import SavedListing
from PE.commerce.models.listing import Listing
from PE.commerce.models.order import Order, STATUS_SETTLED
from PE.commerce.models.review import Review
from PE.commerce.models.seller import Shop, ShopSubscription

# ─────────────────────────── weights ───────────────────────────

# Sales dominates (user directive: "more weight on 2" = sales/revenue). The composite is the
# remainder. Constants live here so tests can pin the exact weights + a future re-tune touches
# ONE file. Sum to 1.0 so the final score stays in [0, 1] within-peer-set.
W_SALES = 0.6
W_COMPOSITE = 0.4

# Composite is itself a weighted sum. Each sub-weight is over the composite's 1.0, so they sum
# to 1.0 too. Order (roughly): saves > rating > followers > recency. Saves is a stronger
# quality proxy than "somebody clicked follow" because it costs a specific interaction on a
# specific listing.
W_C_SAVES = 0.35
W_C_RATING = 0.30
W_C_FOLLOWERS = 0.20
W_C_RECENCY = 0.15

# Sales window — last 30 days of settled orders. A tunable, kept short so a shop that's
# actively transacting scores higher than one that had a big month a year ago.
SALES_WINDOW_DAYS = 30

# Recency half-life for the composite recency term: how long since the shop last got activity
# (a review or an inquiry or a settled order). Uses shop.created_at as the FALLBACK "last
# activity" when the shop has no other signal — a brand-new shop still gets a non-zero recency.
# Long half-life (30 days) because we want a slow decay: newer shops get a nudge, but a shop
# that has been around for months isn't punished for stability.
RECENCY_HALFLIFE_DAYS = 30.0


# ─────────────────────────── DTOs ───────────────────────────

@dataclass(frozen=True)
class ShopSignals:
    """Raw signals for one shop, before normalization. All non-negative."""
    shop_id: str
    seller_id: str
    revenue_cents: int
    rating: float             # 0.0 when unrated (distinct from a low score → treated as 0 signal)
    rating_count: int
    follower_count: int
    saves_total: int          # sum of saves across all this shop's listings
    days_since_activity: float

    def composite_norm_terms(self) -> tuple[float, float, float, float]:
        """Return the four unweighted, un-normalized composite sub-signals (rating, followers,
        saves, recency) as raw numbers that the caller normalizes against the peer set. The
        rating is scaled to [0, 5]; the others are raw counts / days."""
        # Rating comes in as [0.0, 5.0]; leave as-is here and normalize against peer max below.
        # An unrated shop (rating_count == 0) contributes 0 rather than a misleading 0-star score.
        rating_signal = self.rating if self.rating_count > 0 else 0.0
        return rating_signal, float(self.follower_count), float(self.saves_total), self.days_since_activity


@dataclass(frozen=True)
class ShopScore:
    """One shop's final rank input: peer-normalized score + a breakdown for explainability.
    The breakdown carries the WEIGHTED sub-scores so a caller can render "why am I here?"
    without recomputing anything."""
    shop_id: str
    seller_id: str
    score: float                      # in [0, 1]
    sales_score: float                # weighted (already × W_SALES)
    composite_score: float            # weighted (already × W_COMPOSITE)
    signals: ShopSignals              # raw signals, useful for UI (e.g. the actual revenue in cents)


@dataclass(frozen=True)
class RankResult:
    """The endpoint's return shape (via a schemas mapper)."""
    rank: int
    peer_count: int
    own_score: float
    sales_score: float
    composite_score: float
    signals: ShopSignals


# ─────────────────────────── math helpers ───────────────────────────

def _normalize(values: list[float]) -> list[float]:
    """Peer-set normalization: divide by the max. All-zero peers → all-zero output (avoids the
    misleading "top of nothing" score where every shop has zero of a signal and one gets a
    1.0 anyway). Negative values would break the assumption → clamped to 0 defensively (they
    can't occur under the current signal set, but the guard makes the invariant explicit)."""
    if not values:
        return []
    clamped = [max(0.0, v) for v in values]
    m = max(clamped)
    if m <= 0.0:
        return [0.0] * len(clamped)
    return [v / m for v in clamped]


def _recency_score(days_since: float, halflife_days: float) -> float:
    """Exponential decay: 1.0 today, 0.5 at one half-life, etc. Guards a bad halflife → 0."""
    if halflife_days <= 0:
        return 0.0
    return 0.5 ** (max(0.0, days_since) / halflife_days)


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in kilometres. Same formula the proximity service uses in metres —
    duplicated here to keep this service DB-dialect-agnostic (we work off already-loaded lat/lng
    floats, not a SQL expression). O(1)."""
    import math
    R_KM = 6371.0
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlng / 2) ** 2
    return R_KM * 2 * math.asin(min(1.0, math.sqrt(a)))


# ─────────────────────────── the two entrypoints ───────────────────────────

def _load_signals_for_shops(
    db: Session,
    shops: list[Shop],
    now: datetime,
) -> list[ShopSignals]:
    """Batch-load every signal for a list of shops. ONE SQL round-trip per signal (revenue,
    rating, followers, saves, activity) — never one query per shop. All aggregates are grouped
    by seller_id/shop_id so the cost is O(peer_count) rows returned, not O(peer_count) queries."""
    if not shops:
        return []
    shop_ids = [s.id for s in shops]
    seller_ids = list({s.seller_id for s in shops})

    # Revenue: settled orders per seller in the last SALES_WINDOW_DAYS.
    window_start = now - timedelta(days=SALES_WINDOW_DAYS)
    revenue_rows = (
        db.query(Order.seller_id, func.coalesce(func.sum(Order.locked_price_cents), 0))
        .filter(
            Order.seller_id.in_(seller_ids),
            Order.status == STATUS_SETTLED,
            Order.created_at >= window_start,
        )
        .group_by(Order.seller_id)
        .all()
    )
    revenue_by_seller: dict[str, int] = {sid: int(rev or 0) for sid, rev in revenue_rows}

    # Rating aggregate per seller. AVG + COUNT.
    rating_rows = (
        db.query(Review.seller_id, func.avg(Review.rating), func.count(Review.id))
        .filter(Review.seller_id.in_(seller_ids))
        .group_by(Review.seller_id)
        .all()
    )
    rating_by_seller: dict[str, tuple[float, int]] = {
        sid: (float(avg or 0.0), int(cnt or 0)) for sid, avg, cnt in rating_rows
    }

    # Followers per shop.
    follower_rows = (
        db.query(ShopSubscription.shop_id, func.count(ShopSubscription.id))
        .filter(ShopSubscription.shop_id.in_(shop_ids))
        .group_by(ShopSubscription.shop_id)
        .all()
    )
    followers_by_shop: dict[str, int] = {sid: int(cnt or 0) for sid, cnt in follower_rows}

    # Saves across the shop's listings. Join listing→saves so we get one row per shop.
    saves_rows = (
        db.query(Listing.shop_id, func.count(SavedListing.id))
        .join(SavedListing, SavedListing.listing_id == Listing.id)
        .filter(Listing.shop_id.in_(shop_ids))
        .group_by(Listing.shop_id)
        .all()
    )
    saves_by_shop: dict[str, int] = {sid: int(cnt or 0) for sid, cnt in saves_rows}

    # Activity recency: for each shop, the most recent of {last review, last settled order,
    # shop.created_at}. Reviews and orders are per-seller, so we resolve via seller_id per shop.
    last_review_rows = (
        db.query(Review.seller_id, func.max(Review.created_at))
        .filter(Review.seller_id.in_(seller_ids))
        .group_by(Review.seller_id)
        .all()
    )
    last_review_by_seller: dict[str, datetime] = {
        sid: dt for sid, dt in last_review_rows if dt is not None
    }
    last_order_rows = (
        db.query(Order.seller_id, func.max(Order.created_at))
        .filter(Order.seller_id.in_(seller_ids), Order.status == STATUS_SETTLED)
        .group_by(Order.seller_id)
        .all()
    )
    last_order_by_seller: dict[str, datetime] = {
        sid: dt for sid, dt in last_order_rows if dt is not None
    }

    def _days_since(dt: datetime | None) -> float:
        if dt is None:
            return float("inf")  # never had this signal → treated as infinitely old
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        n = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
        return max(0.0, (n - dt).total_seconds() / 86400.0)

    signals: list[ShopSignals] = []
    for shop in shops:
        rating_avg, rating_cnt = rating_by_seller.get(shop.seller_id, (0.0, 0))
        # Recency: the freshest of the three, or the shop's own created_at as the last resort.
        last_review = last_review_by_seller.get(shop.seller_id)
        last_order = last_order_by_seller.get(shop.seller_id)
        candidates = [c for c in (last_review, last_order, shop.created_at) if c is not None]
        last_activity = max(candidates) if candidates else None
        signals.append(ShopSignals(
            shop_id=str(shop.id),
            seller_id=str(shop.seller_id),
            revenue_cents=revenue_by_seller.get(shop.seller_id, 0),
            rating=rating_avg,
            rating_count=rating_cnt,
            follower_count=followers_by_shop.get(str(shop.id), 0),
            saves_total=saves_by_shop.get(str(shop.id), 0),
            days_since_activity=_days_since(last_activity),
        ))
    return signals


def compute_shop_scores(
    db: Session,
    *,
    center_lat: float,
    center_lng: float,
    radius_km: float,
    now: datetime,
) -> list[ShopScore]:
    """Return every shop within ``radius_km`` of ``(center_lat, center_lng)``, scored + ranked
    against the peer set. Order is score DESC (highest first). Ties break by shop_id for
    determinism (a stable id, unlike created_at which can tie at the microsecond).

    O(peer_count) SQL rows + O(peer_count) in-memory math. Cheap enough to run per request; the
    endpoint layer caches by (bucket, radius) for 5 min anyway.
    """
    if radius_km <= 0:
        return []

    # Load all shops in-radius. Uses a bounding-box prefilter on lat/lng (cheap on any dialect)
    # then the exact haversine in Python. For very small radii the bbox is O(<peer_count>) and
    # the Python filter is a rounding step. For the paywalled >200km case the peer set is
    # bounded by the data (Kenya-scale demo pool ≈ hundreds of shops), still trivial.
    lat_span = radius_km / 111.0                                 # degrees
    import math
    lng_span = radius_km / (111.0 * max(0.1, math.cos(math.radians(center_lat))))
    # A shop with lat/lng NULL cannot be in-radius by definition (we require both to place
    # a pin on the map). Filter is defensive; the Shop model already has both non-null.
    candidate_shops: list[Shop] = (
        db.query(Shop)
        .filter(
            Shop.lat.between(center_lat - lat_span, center_lat + lat_span),
            Shop.lng.between(center_lng - lng_span, center_lng + lng_span),
        )
        .all()
    )
    shops_in_radius = [
        s for s in candidate_shops
        if _haversine_km(center_lat, center_lng, s.lat, s.lng) <= radius_km
    ]
    if not shops_in_radius:
        return []

    signals = _load_signals_for_shops(db, shops_in_radius, now)

    # Peer-set normalization for each raw signal, then weighted combine.
    revenues = _normalize([float(s.revenue_cents) for s in signals])
    ratings = _normalize([s.rating if s.rating_count > 0 else 0.0 for s in signals])
    followers = _normalize([float(s.follower_count) for s in signals])
    saves = _normalize([float(s.saves_total) for s in signals])
    recencies = [_recency_score(s.days_since_activity, RECENCY_HALFLIFE_DAYS) for s in signals]
    # Recencies are already in [0,1] (an exponential decay) — no peer-normalization: a shop
    # yesterday is a shop yesterday, regardless of who else is around.

    scores: list[ShopScore] = []
    for i, s in enumerate(signals):
        composite = (
            W_C_RATING * ratings[i]
            + W_C_FOLLOWERS * followers[i]
            + W_C_SAVES * saves[i]
            + W_C_RECENCY * recencies[i]
        )
        sales_term = W_SALES * revenues[i]
        composite_term = W_COMPOSITE * composite
        scores.append(ShopScore(
            shop_id=s.shop_id,
            seller_id=s.seller_id,
            score=sales_term + composite_term,
            sales_score=sales_term,
            composite_score=composite_term,
            signals=s,
        ))
    # Sort by score DESC, tie-break by shop_id ASC for determinism.
    scores.sort(key=lambda x: (-x.score, x.shop_id))
    return scores


def compute_shop_rank(
    db: Session,
    *,
    seller_uuid: str,
    radius_km: float,
    now: datetime,
) -> RankResult | None:
    """Compute where the caller's shop ranks in a `radius_km` circle around it, against every
    other shop in the same circle. Returns ``None`` when the caller has no shop (they can't be
    ranked). The center is the CALLER's shop location, so a seller in Kilimani asking about a
    10 km radius gets the ranking for shops within 10 km of Kilimani — not of the buyer's
    location.

    ``seller_uuid`` here is the token ``sub`` on the seller side; commerce's Seller row is keyed
    by that value (see models/seller.py Seller.user_uuid). We resolve seller → their first Shop
    (the model is one shop per seller today; if that changes, pick the FIRST alphabetically for
    determinism until the multi-shop story is designed).
    """
    from PE.commerce.models.seller import Seller
    seller = db.query(Seller).filter(Seller.user_uuid == seller_uuid).one_or_none()
    if seller is None:
        return None
    shop = (
        db.query(Shop)
        .filter(Shop.seller_id == seller.id)
        .order_by(Shop.id.asc())
        .first()
    )
    if shop is None:
        return None

    scores = compute_shop_scores(
        db,
        center_lat=shop.lat,
        center_lng=shop.lng,
        radius_km=radius_km,
        now=now,
    )
    own_score = next((s for s in scores if s.shop_id == str(shop.id)), None)
    if own_score is None:
        # Defensive: the caller's own shop should ALWAYS be in-radius (distance to itself is 0).
        # If it isn't, something is upstream-wrong; return None rather than a misleading rank.
        return None
    # rank is 1-indexed. `scores` is already sorted DESC by score with a deterministic tie-break.
    rank = 1 + next(i for i, s in enumerate(scores) if s.shop_id == own_score.shop_id)
    return RankResult(
        rank=rank,
        peer_count=len(scores),
        own_score=own_score.score,
        sales_score=own_score.sales_score,
        composite_score=own_score.composite_score,
        signals=own_score.signals,
    )
