"""Proximity feed assembly: radius search → pure ranking → cursor pagination.

Flow: pull a bounded candidate set within the radius (index-backed, ordered by distance),
score each in Python with the pure ranking function, sort by score, and page with an
opaque keyset cursor over ``(score, listing_id)``.

Why a bounded candidate window: capping at ``feed_max_candidates`` keeps scoring+sort O(k)
on top of the O(log n + k) index lookup — never an O(n) table scan. The honest tradeoff
(deferred): a true streaming keyset over a *computed, caller-relative* score needs a
materialized score; until then the bounded window is correct because the radius cap bounds
k. The cursor is stable across pages because the candidate query is deterministic for a
fixed (lat, lng, radius).
"""
from __future__ import annotations

import base64
import logging
import math
import random
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from PE.commerce.core.config import settings
from PE.commerce.models.boost import TARGET_LISTING, TARGET_SHOP, TIER_WEIGHT
from PE.commerce.services import boost, boost_cap, proximity, ranking

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScoredListing:
    listing: object  # Listing ORM row
    distance_m: float
    score: float
    is_promoted: bool = False  # a live §8 "selling now" window (expiry still in the future)
    is_sponsored: bool = False  # occupies a §8.3 sponsored slot (paid reach, not organic score)
    boost_tier: str | None = None  # the reach tier when sponsored (mtaa|hustle|sovereign)


# Precision at which scores are compared for ordering — the score sort tiebreaks on the
# stable listing id, so equal-to-9dp scores still order deterministically.
_SCORE_DP = 9


def _sort_key(score: float, listing_id: str) -> tuple[float, str]:
    """The descending ordering used for the feed sort: highest score first, with the stable
    listing id as a deterministic tiebreak."""
    return (round(score, _SCORE_DP), listing_id)


def _encode_cursor(listing_id: str) -> str:
    """The cursor anchors on the last item's STABLE id, not its score. The score is
    time-dependent (freshness decays each request), so a score-keyed cursor would drift and
    re-emit the boundary item; an id anchor resumes exactly after the last-seen listing
    regardless of score drift."""
    return base64.urlsafe_b64encode(listing_id.encode()).decode()


def _decode_cursor(cursor: str) -> str | None:
    """Parse an opaque cursor → listing_id. Returns None on malformed input (a bad cursor
    restarts from the top rather than erroring — the feed is best-effort)."""
    try:
        return base64.urlsafe_b64decode(cursor.encode()).decode()
    except (ValueError, TypeError):
        return None


def _sponsored_listings(db, lat, lng, *, exclude_ids: set[str], now, kind=None) -> list[ScoredListing]:
    """Resolve the §8.3 sponsored lane: the bounded set of Boost-eligible, still-buyable listings
    near (or nationally reaching) the buyer, ordered by reach tier then distance — the candidates
    that fill the labelled sponsored slots.

    Steps (all O(bounded), no scan):
      1. pull eligible live grants whose scope contains the buyer (capped at K, GiST-indexed);
      2. map each grant to its target listing id(s) — a listing grant is itself; a shop grant
         expands to that shop's listings — tracking the WIDEST tier per listing;
      3. resolve those ids to active+in-stock listings with distance (a sold-out target drops);
      4. drop any listing already in the organic page (no double-show), order by tier weight then
         nearest, and return them tagged ``is_sponsored``.

    The organic comparison is never touched — these are a separate lane the caller interleaves."""
    grants = boost.eligible_grants(db, lat, lng, now=now)
    if not grants:
        return []

    # Best (widest) tier per candidate listing id, plus the shop targets to expand.
    tier_by_listing: dict[str, str] = {}
    shop_targets: dict[str, str] = {}  # shop_id -> widest tier

    def _keep_widest(mapping: dict[str, str], key: str, tier: str) -> None:
        if key not in mapping or TIER_WEIGHT[tier] > TIER_WEIGHT[mapping[key]]:
            mapping[key] = tier

    for g in grants:
        if g.target_type == TARGET_LISTING:
            _keep_widest(tier_by_listing, g.target_id, g.tier)
        elif g.target_type == TARGET_SHOP:
            _keep_widest(shop_targets, g.target_id, g.tier)

    # Expand shop grants → their listing ids (bounded: shop_targets is ≤ K; cap the fan-out too).
    if shop_targets:
        from PE.commerce.models.listing import Listing as _L
        rows = (
            db.query(_L.id, _L.shop_id)
            .filter(_L.shop_id.in_(list(shop_targets.keys())))
            .limit(settings.feed_sponsored_max_candidates)
            .all()
        )
        for lid, shop_id in rows:
            _keep_widest(tier_by_listing, lid, shop_targets[shop_id])

    # Never show a listing in BOTH lanes — the organic placement wins (it's the more relevant one).
    candidate_ids = [lid for lid in tier_by_listing if lid not in exclude_ids]
    if not candidate_ids:
        return []

    # Honour the §8 toggle IN SQL (visible_listings_by_ids applies _kind_predicate): a Videos feed
    # must not surface a boosted ordinary-listing post, and vice-versa — the DB filters, so the
    # sponsored lane can't leak the wrong post kind into the view.
    resolved = proximity.visible_listings_by_ids(db, candidate_ids, lat, lng, kind=kind)
    sponsored = [
        ScoredListing(
            listing=listing,
            distance_m=distance_m,
            score=0.0,  # sponsored items are NOT organically scored — the lane is separate
            is_promoted=False,
            is_sponsored=True,
            boost_tier=tier_by_listing[str(listing.id)],
        )
        for listing, distance_m in resolved
    ]
    # Deterministic base order of the bounded set: widest reach first, then nearest, then stable id.
    # The optional §8.3 lottery below re-orders this (still reproducibly); the cap then trims it.
    sponsored.sort(key=lambda s: (-TIER_WEIGHT[s.boost_tier], s.distance_m, str(s.listing.id)))
    # §8.3 fill-rate LOTTERY (OFF by default): when enabled, tier-weighted weighted-shuffle the
    # bounded lane so slots aren't monopolised by the same widest/nearest few every request, while
    # wider tiers still win slots more often. Applied BEFORE the per-shop cap so the cap trims the
    # lottery outcome. Deterministic per (buyer-cell, business-date) → cursor-safe & reproducible.
    if settings.feed_sponsored_lottery_enabled and len(sponsored) > 1:
        sponsored = _lottery_order(sponsored, lat, lng, now)
    # Per-shop cap: the global default, minus any STAFF-APPROVED per-shop override (§8.3 item 1).
    # Resolve overrides only for the distinct shops actually present in this bounded sponsored set —
    # one indexed IN query, O(k), and skipped entirely when the lane is empty (a boost-free feed is
    # byte-identical to before this feature).
    shop_ids = {str(s.listing.shop_id) for s in sponsored}
    overrides = boost_cap.resolve_caps(db, shop_ids)
    return _cap_per_shop(sponsored, settings.feed_sponsored_max_per_shop, overrides)


def _lottery_seed(lat: float, lng: float, now: datetime) -> int:
    """A STABLE, reproducible seed for the sponsored lottery — never wall-clock/PRNG-global entropy
    (that would break cursor stability: the same buyer paging must see the same lane order). Derived
    from the buyer's ~1 km cell (2-dp lat/lng) and the business date, so a given buyer sees one fixed
    order for the day. ``settings.feed_sponsored_lottery_seed``, when set, pins it globally (tests /
    deterministic demos)."""
    if settings.feed_sponsored_lottery_seed is not None:
        return int(settings.feed_sponsored_lottery_seed)
    bday = boost.business_date(now)
    key = f"{round(lat, 2)}:{round(lng, 2)}:{bday.isoformat()}"
    return zlib.crc32(key.encode("utf-8"))


def _lottery_order(sponsored: list[ScoredListing], lat: float, lng: float, now: datetime) -> list[ScoredListing]:
    """Tier-weighted weighted-shuffle of the (already tier→distance→id-sorted) bounded lane. Uses
    the Efraimidis–Spirakis key ``u**(1/w)`` (u∈(0,1), w = tier weight): larger weights get larger
    keys in expectation, so wider tiers sort earlier MORE OFTEN without ever fully locking out the
    narrower ones — that's the fill-rate the lottery buys. A seeded local RNG keeps it deterministic
    (no global-state mutation, no reliance on process-wide random). Ties broken by the stable base
    index so the result is a total, reproducible order."""
    rng = random.Random(_lottery_seed(lat, lng, now))

    def _key(idx_item):
        idx, s = idx_item
        w = float(TIER_WEIGHT[s.boost_tier]) or 1.0
        u = rng.random() or 1e-12  # guard against log(0) / 0**x edge
        # Rank by descending ES key; tie-break by the stable base order (idx).
        return (-(math.log(u) / w), idx)

    ordered = sorted(enumerate(sponsored), key=_key)
    return [s for _, s in ordered]


def _cap_per_shop(
    sponsored: list[ScoredListing],
    default_max: int,
    overrides: dict[str, int] | None = None,
) -> list[ScoredListing]:
    """Fairness cap: allow at most N sponsored slots per shop, so one shop boosting many listings
    can't flood the labelled lane and crowd out other boosted shops (§8.3 = paid REACH, not a
    takeover). N is ``default_max`` for every shop, except a shop with a STAFF-APPROVED override in
    ``overrides`` uses its absolute approved cap instead (item 1). One O(k) pass over the ALREADY
    tier→distance→id-sorted list, so the slots a shop keeps are its widest-reach / nearest ones and
    the overall order is otherwise preserved (still deterministic, still cursor-safe).

    A shop's effective cap <= 0 disables the cap FOR THAT SHOP (unlimited). When ``overrides`` is
    empty this reduces to a single uniform ``default_max`` — byte-identical to before item 1."""
    overrides = overrides or {}
    per_shop: dict[str, int] = {}
    kept: list[ScoredListing] = []
    for s in sponsored:
        shop_id = str(s.listing.shop_id)
        cap = overrides.get(shop_id, default_max)
        if cap > 0 and per_shop.get(shop_id, 0) >= cap:
            continue  # this shop has taken its allotted slots; skip to keep the lane diverse
        per_shop[shop_id] = per_shop.get(shop_id, 0) + 1
        kept.append(s)
    return kept


def _interleave_sponsored(
    organic: list[ScoredListing], sponsored: list[ScoredListing], every_n: int,
    *, max_on_empty: int,
) -> list[ScoredListing]:
    """Inject sponsored items into the organic list at a fixed cadence: one sponsored slot after
    every ``every_n`` organic items. The organic order is preserved exactly (relevance intact);
    sponsored items only ADD labelled slots between them. ``every_n`` <= 0 disables the lane.

    A page with FEWER than ``every_n`` organic items still gets ONE sponsored slot appended (when
    candidates exist): sparse/new areas are exactly who national (Sovereign) reach is meant to
    serve, so a short feed must not silently drop the whole sponsored lane. The cap still holds —
    at most one sponsored per ``every_n`` organic items, plus this single floor on a short page.

    A page with NO organic items at all (a far/empty locality — precisely the buyer Sovereign reach
    exists for) surfaces the sponsored lane ON ITS OWN, bounded by ``max_on_empty``. Without this a
    nationwide-boosted listing was silently dropped for exactly the distant buyers it paid to reach
    (the whole feed came back empty), even though the grant's scope contained them."""
    if every_n <= 0 or not sponsored:
        return organic
    if not organic:
        # No local organic content to interleave into — show the (already tier/distance-sorted,
        # candidate-capped) sponsored lane alone, bounded so an empty area is never an ad wall.
        return list(sponsored[:max_on_empty]) if max_on_empty > 0 else []
    out: list[ScoredListing] = []
    si = 0
    for i, item in enumerate(organic):
        out.append(item)
        # After each block of `every_n` organic items, drop in one sponsored slot (if any left).
        if (i + 1) % every_n == 0 and si < len(sponsored):
            out.append(sponsored[si])
            si += 1
    # Floor: a short page (no full block reached) still surfaces one sponsored slot.
    if si == 0 and sponsored:
        out.append(sponsored[0])
    return out


def _score_candidate(listing, distance_m: float, radius_m: float, now: datetime) -> ScoredListing:
    """Score one organic candidate. The §8 promo boost is evaluated ONCE here (against a single
    ``now``) and then both fed into ``ranking.score`` and used to set ``is_promoted`` — one source
    of truth, no second clock read and no chance of the two derivations drifting apart."""
    promo = ranking.promo_boost(listing.promo_started_at, listing.promo_expires_at, now)
    return ScoredListing(
        listing=listing,
        distance_m=distance_m,
        score=ranking.score(
            distance_m=distance_m,
            created_at=listing.created_at,
            intent_weight=listing.intent_weight,
            now=now,
            w_distance=settings.feed_w_distance,
            w_freshness=settings.feed_w_freshness,
            w_intent=settings.feed_w_intent,
            radius_m=radius_m,
            halflife_h=settings.feed_freshness_halflife_h,
            w_promo=settings.feed_w_promo,
            promo=promo,
            # Soft, additive media nudge — parsed from the stored JSON media_urls (never string
            # truthiness; "[]" ⇒ no media) via the single shared helper so scoring and display agree.
            w_media=settings.feed_w_media,
            has_media=1.0 if ranking.has_media(listing.media_urls) else 0.0,
        ),
        is_promoted=promo > 0.0,  # live promotion iff the boost is non-zero for this `now`
    )


def build_feed(
    db: Session,
    lat: float,
    lng: float,
    radius_m: float,
    *,
    cursor: str | None = None,
    limit: int = 20,
    now: datetime | None = None,
    kind: str | None = None,
) -> dict:
    """Return ``{"items": [ScoredListing...], "next_cursor": str | None, "widened": bool,
    "nearest_distance_m": float | None, "immediate_count": int}``.

    The organic lane (proximity×freshness×intent, keyset-paginated) is the backbone. On the FIRST
    page only (no cursor), §8.3 sponsored slots are interleaved at ``feed_sponsored_every_n``: this
    keeps the organic keyset cursor — which anchors on the last ORGANIC id — exact across pages, so
    no organic row is ever skipped or duplicated by the sponsored lane.

    ``kind`` is the §8 feed toggle ("listings" | "videos" | None=both); it filters BOTH lanes so the
    organic page and the sponsored slots show the same post kind.

    ``widened`` is True when the immediate radius was thin (< feed_sparse_threshold) and the search
    fell back once to the server max radius to surface MORE nearest content (see the widen block
    below); ``nearest_distance_m`` is the closest candidate's distance over the effective set, so the
    client can honestly say "closest shops are within X". ``immediate_count`` is how many the
    IMMEDIATE radius held before widening — the client keys empty (0 ⇒ "nothing in your area") vs
    sparse (>0 ⇒ "only a few nearby, also showing farther") banner copy on it."""
    now = now or datetime.now(timezone.utc)

    candidates = proximity.search_listings(
        db, lat, lng, radius_m, limit=settings.feed_max_candidates, now=now, kind=kind
    )

    # Snapshot how many the IMMEDIATE radius returned, before any widen re-search overwrites
    # ``candidates``. This is the honest empty-vs-sparse signal the client needs (0 ⇒ "nothing in
    # your area"; >0 ⇒ "only a few nearby, also showing farther"): the response can't infer it from
    # nearest_distance_m alone (it doesn't carry the requested radius, and the client often omits it).
    immediate_count = len(candidates)

    # Auto-widen a THIN immediate radius. The default 2 km radius is the moat ("people next door"),
    # but a sparse locality would otherwise render a near-empty surface even when the nearest sellers
    # are only a few km out. When the immediate radius returns fewer than one page's worth
    # (feed_sparse_threshold — a FIXED server constant, deliberately not the per-request ``limit`` so
    # "sparse" is a property of the locality, not the client's page size), re-search ONCE at the
    # server max radius to top the page up with the nearest content. One extra bounded, index-backed
    # query (O(log n + k), capped at feed_max_candidates — never a scan); it fires only when local
    # content is thin, so a healthy local feed (≥ a page nearby) is byte-identical to before.
    #
    # ``widened`` is set True ONLY if the re-search actually surfaced MORE than the immediate radius
    # had — so we never show a "also showing farther" note when there is nothing farther to show.
    # ``applied_radius`` (what scoring normalises proximity against) differs by case:
    #   * EMPTY immediate radius → normalise against the wide radius: there are no near items to
    #     discriminate, and using the requested radius would clamp every far item's proximity to 0.
    #   * SPARSE (had ≥1 near item) → keep the ORIGINAL radius: near items must keep their proximity
    #     advantage and far top-ups should clamp toward 0 and sit BELOW them (near-first, far-filler).
    #     Normalising a sparse page against 20 km would compress every item toward proximity≈1 and
    #     erase exactly the near-item advantage this feed exists to preserve.
    # The widen decision is a pure function of (lat,lng,radius,db-state) recomputed identically on
    # every page, so the keyset cursor stays exact — no page-to-page widen flip for a static DB.
    applied_radius = radius_m
    widened = False
    if immediate_count < settings.feed_sparse_threshold and settings.feed_max_radius_m > radius_m:
        widened_candidates = proximity.search_listings(
            db, lat, lng, settings.feed_max_radius_m,
            limit=settings.feed_max_candidates, now=now, kind=kind,
        )
        if len(widened_candidates) > immediate_count:
            candidates = widened_candidates
            widened = True
            if immediate_count == 0:
                applied_radius = settings.feed_max_radius_m

    # Scaling tripwire: the candidate pull is capped (anti-O(n), S8), and scoring/sort happens over
    # this bounded window. If the pull SATURATES the cap, there may be in-radius listings beyond it
    # that were never scored — the far tail is silently dropped BEFORE ranking. Today the 2 km radius
    # keeps k well under the cap, but a densifying locality would cross it invisibly. Log it (WARNING,
    # rate-limited by how rarely it should happen) so the ceiling surfaces as an ops signal, not a
    # silent correctness gap. The remedy when this fires regularly is a materialized score + a true
    # streaming keyset (see module docstring), not a bigger cap.
    if len(candidates) >= settings.feed_max_candidates:
        logger.warning(
            "feed candidate pull saturated the cap (%d) at (%.5f, %.5f) r=%.0fm kind=%s — "
            "in-radius listings beyond the cap are dropped before ranking; the far tail may be "
            "incomplete. Consider a materialized score if this recurs.",
            settings.feed_max_candidates, lat, lng, applied_radius, kind or "both",
        )

    # Nearest candidate distance (over the possibly-widened set) — the honest "closest shops are
    # within X" signal the client surfaces when widened. None when there are no candidates at all.
    nearest_distance_m = min((d for _, d in candidates), default=None)

    scored = [
        _score_candidate(listing, distance_m, applied_radius, now)
        for listing, distance_m in candidates
    ]

    # Deterministic order: highest score first, id as a stable tiebreak. Stable across
    # requests for a fixed candidate set even as freshness scores decay slightly, because
    # the id tiebreak pins equal-score ordering.
    scored.sort(key=lambda s: _sort_key(s.score, str(s.listing.id)), reverse=True)

    # Resume strictly AFTER the last-seen id (id anchor, not score — see _encode_cursor).
    # A cursor whose id is no longer in the window (listing went inactive/out of radius)
    # falls through to start-from-top, which is the safe best-effort behaviour.
    anchor_id = _decode_cursor(cursor) if cursor else None
    if anchor_id is not None:
        ids = [str(s.listing.id) for s in scored]
        if anchor_id in ids:
            scored = scored[ids.index(anchor_id) + 1:]

    page = scored[:limit]
    next_cursor = None
    if len(scored) > limit and page:
        next_cursor = _encode_cursor(str(page[-1].listing.id))

    # §8.3 sponsored lane — FIRST PAGE ONLY. The next_cursor is computed above from the last
    # ORGANIC item, so interleaving sponsored slots now cannot disturb pagination (the cursor never
    # anchors on a sponsored row). Excluding the page's organic ids prevents showing a listing in
    # both lanes on the same page.
    if cursor is None and settings.feed_sponsored_every_n > 0:
        organic_ids = {str(s.listing.id) for s in page}
        sponsored = _sponsored_listings(db, lat, lng, exclude_ids=organic_ids, now=now, kind=kind)
        page = _interleave_sponsored(
            page, sponsored, settings.feed_sponsored_every_n,
            max_on_empty=settings.feed_sponsored_max_on_empty,
        )

    return {
        "items": page,
        "next_cursor": next_cursor,
        "widened": widened,
        "nearest_distance_m": nearest_distance_m,
        "immediate_count": immediate_count,
    }
