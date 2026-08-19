"""Seed settlement-grade credit history so the WeesStock card (§WeesStock F2) has something real
to render in a development stack.

WHY THIS EXISTS. ``services/credit_score.py`` underwrites on VERIFIED rows only: revenue is summed
from ``Receipt`` (immutable, hash-chained, issued inside the settle transaction) and never from
``Order.locked_price_cents``. The dev DB had 118 active products and **zero** receipts, so every
seller was thin-file and the card could only ever render its cold-start prompt — the scored path,
which is the whole product, was uninspectable outside unit tests.

LOCAL / DEMO ONLY, and hard-gated: ``seed()`` refuses to run when ``settings.is_production()``.
This writes settled orders and receipts — money-shaped rows — so it must be impossible to point at
production by accident. The gate is code, not a flag anyone can flip from the environment.

**Everything flows through the real service functions.** Orders are opened by
``settlement.open_order``, bargains driven with ``counter``/``accept``/``cancel``, settled by
``settlement.settle`` (which issues the receipt via ``receipts.issue_receipt``), and reviewed by
``reviews.create_review``. Nothing is INSERTed by hand. So seeded history satisfies every invariant
a real sale does: the per-order event chain is intact and hash-linked, each receipt's
``receipt_hash`` covers its own content plus the ``settle_ok`` event's ``row_hash``, and
``net = gross - commission`` holds on every row.

WHAT THE REAL DATA DICTATES (measured 2026-08-14, not assumed):

  * **Sale amounts are NOT ours to choose.** ``open_order`` on a ``fixed`` listing locks at the
    listing's own price. Only the 11 ``bargain`` listings allow a negotiated number. So a shop's
    revenue is a function of its actual catalogue. The revenue term saturates at KES 150k/90d
    (USER DECISION 2026-08-17: KES 50k/month — a working neighbourhood shop, measured against
    Kenyan estate-shop turnover), so Elite Kicks (KES 23.6k avg item) fills the revenue bar in
    ~7 sales while the bakery (KES 422 avg) needs ~355. Volumes stay BELIEVABLE, so the
    strongest catalogues saturate on revenue and the honest differentiation comes from
    fulfilment/repeat/rating — the right picture for shops whose cash flow is small but real.
  * **Failed orders can only come from ``bargain`` listings.** A ``fixed`` order jumps straight to
    ``PRICE_LOCKED``, which is not in ``OPEN_STATUSES``, so ``cancel()`` refuses it — by design.
    Fulfilment therefore has to be degraded through the negotiation path, and a shop's failed count
    is capped by having at least one bargain listing (all seven do).
  * **Sales must not predate the seller.** Real tenures are 35–46 days, well inside the 90-day
    revenue window, so dates are spread across the seller's OWN tenure rather than the full window.
    A receipt older than the shop is not a thing a lender could ever see.
  * **Eva Mokaya is deliberately skipped.** Her only listing is a price-0 ``post`` in the
    auto-provisioned "My timeline" shop — she has never had anything to sell. Per the user's
    decision she stays thin-file rather than having a business invented for her; the two demo
    thin-file sellers below cover the cold-start UI instead.

**Timestamps are the one thing rewritten after the fact,** and only because a 90-day spread cannot
be produced any other way: the services stamp from the clock, so a run-in-place seeder would pile
all history into the current second. Safe here, and verified rather than assumed — neither
``_event_hash`` (settlement.py) nor ``_receipt_hash`` (receipts.py) includes a timestamp in its
canonical string, so backdating leaves both chains verifying exactly as issued. Two columns move,
always together, because the scorer windows them independently:

  * ``Receipt.issued_at``  → ``revenue_cents`` / ``recent_revenue_cents`` / ``settled_orders``
  * ``Order.created_at``   → ``failed_orders`` and the repeat-buyer aggregate

Backdating only one would produce a shop with revenue but no orders (or vice versa) — a profile that
cannot exist in reality, and one that would make the card lie about fulfilment.

IDEMPOTENT / RE-RUNNABLE. Every transition is keyed on a deterministic idempotency key derived from
(seller, sale index), so a second run REPLAYS through ``settlement._replay`` instead of opening new
orders: re-running never doubles a seller's revenue. There is no RNG anywhere — amounts, dates,
ratings and buyer assignment are pure functions of the seller's slot and the sale index, so the same
command always produces the same profile.

FOOTPRINT. Bounded and identifiable. Synthetic buyers use the ``demo-weesstock-buyer-`` prefix and
the thin-file sellers use ``demo-weesstock-thin-``, so every row this script creates can be found —
and removed — without touching real seller data. ``--purge`` does exactly that, scoped to those
prefixes.

Run (live PG):
    PYTHONPATH=/home/jeff /home/jeff/PE/commerce/.venv/bin/python \
        -m PE.commerce.scripts.seed_weesstock_credit [--dry-run] [--purge]
"""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, update

from PE.commerce.core.config import settings
from PE.commerce.core.database import SessionLocal
from PE.commerce.models.listing import POST_KIND_PRODUCT, Listing
from PE.commerce.models.order import (
    STATUS_CANCELLED,
    STATUS_PRICE_LOCKED,
    STATUS_SETTLED,
    IdempotencyKey,
    Order,
    OrderEvent,
)
from PE.commerce.models.receipt import Receipt
from PE.commerce.models.review import Review
from PE.commerce.models.seller import Seller, Shop
from PE.commerce.schemas import catalog as schemas
from PE.commerce.services import catalog, reviews, settlement
from PE.commerce.services.credit_score import MIN_ORDERS_FOR_SCORE, MIN_TENURE_DAYS

logger = logging.getLogger("seed_weesstock_credit")

# ─────────────────────────── identifiable demo prefixes ───────────────────────────

# Synthetic buyers. Prefixed so --purge finds every row this script created, and so a human reading
# the orders table can tell seeded history from a real sale at a glance.
BUYER_PREFIX = "demo-weesstock-buyer-"
# The purpose-made thin-file sellers. Kept SEPARATE from the real sellers on purpose (user decision
# 2026-08-14): all the real trading accounts get a scored card, so whichever one you log in as shows
# the full profile, while the cold-start UI stays inspectable by logging in as one of these.
THIN_PREFIX = "demo-weesstock-thin-"

# Repeat-buyer rate is a scored component (W_REPEAT = 0.15), measured by the scorer as
# (buyers with >1 settled order) / (distinct buyers). It is a RATIO, so the seeder controls it by
# choosing the MIX of buyers, not a pool size.
#
# The original design cycled one fixed pool of 8 buyers with `index % POOL`. Measured against the
# real dev DB that pinned repeat_rate at exactly 1.00 for all seven scored sellers: every seller
# has >= 13 sales, so all 8 buyers necessarily bought more than once. A component that reads 1.00
# for everyone differentiates nothing and is not a shape any real shop has.
#
# So each seller's buyers are split in two, per tier:
#   * REGULARS — a small group that buys repeatedly. Every regular ends up with >1 order, so this
#     group is exactly the numerator of the repeat rate.
#   * ONE-TIMERS — a distinct buyer per sale, each buying exactly once. These are the denominator's
#     remainder, and they are what keeps the rate below 1.0.
# Repeat rate therefore converges to regulars / (regulars + one_timers) — a number set per tier
# rather than an artefact of the sale count.
_REGULAR_SLICE = "r"
_ONE_TIME_SLICE = "o"

# The trading account with no sellable product. Skipped by name-independent means (no priced product
# listing), but recorded here so the skip is legible in the log rather than looking like a bug.
_SKIP_REASON_NO_PRODUCT = "no priced product listing — nothing to sell"

# Sale dates are spread across a fraction of the seller's own tenure, leaving the newest sale a few
# days back. Bounded by tenure because a receipt cannot predate its shop; the margin at the recent
# end keeps a run today and a run tomorrow describing the same shop instead of drifting.
_NEWEST_SALE_DAYS = 2
_TENURE_USE_FRACTION = 0.85


@dataclass(frozen=True)
class Tier:
    """One rung of the credit spread — the SHAPE of a seller's seeded history.

    ``settled``/``failed`` are counts and set the fulfilment term (settled / (settled + failed)),
    the second-heaviest component at 0.25. Revenue is NOT specified here: it falls out of the
    shop's real listing prices (see the module docstring), so this only controls volume.

    ``regulars``/``one_timers`` set the repeat-buyer term (W_REPEAT = 0.15) directly: the rate
    converges to ``regulars / (regulars + one_timers)``. Both must be > 0 — all-regulars pins the
    rate at 1.0 (the bug this replaced) and all-one-timers pins it at 0.0.
    """
    name: str
    settled: int
    failed: int
    ratings: tuple[int, ...]        # lifetime ratings, one per reviewed sale
    regulars: int                   # buyers who come back — the repeat-rate numerator
    one_timers: int                 # buyers who bought exactly once


# The graded spread, strongest-first, assigned to real sellers by DESCENDING active product count so
# the biggest catalogue reads as the strongest business — what a human eyeballing the console would
# expect. Deliberately NOT uniform: a demo where every shop shows the same bars proves nothing about
# whether the components differentiate. Fulfilment degrades down the ladder while volume falls, so
# the two heavy terms move independently and a reader can tell which one is hurting a given shop.
TIERS: tuple[Tier, ...] = (
    Tier("strong",     settled=46, failed=1, ratings=(5, 5, 4, 5, 5, 4, 5), regulars=9, one_timers=12),
    Tier("solid",      settled=42, failed=2, ratings=(5, 4, 4, 5, 4, 4),    regulars=8, one_timers=13),
    Tier("healthy",    settled=38, failed=3, ratings=(4, 4, 5, 4, 3, 4),    regulars=7, one_timers=14),
    Tier("steady",     settled=34, failed=4, ratings=(4, 4, 3, 4, 4),       regulars=6, one_timers=15),
    Tier("moderate",   settled=30, failed=5, ratings=(4, 3, 4, 3),          regulars=5, one_timers=16),
    # Clears the cold-start gate with visibly weak fulfilment — the case that proves a SCORED card
    # can still read "work to do" rather than every scored shop looking healthy.
    Tier("marginal",   settled=24, failed=9, ratings=(3, 3, 2),             regulars=4, one_timers=16),
    Tier("developing", settled=18, failed=7, ratings=(3, 4, 3),             regulars=3, one_timers=12),
    Tier("emerging",   settled=13, failed=6, ratings=(3, 2),                regulars=2, one_timers=9),
)

# The two thin-file demo sellers, each failing a DIFFERENT cold-start gate so both branches of the
# card's growth prompt render against real rows. The prompt text is built server-side from
# orders_needed / days_needed, so these are the only way to see each branch end-to-end.
THIN_ORDERS_CASE = MIN_ORDERS_FOR_SCORE - 4      # 6 sales, tenure fine  → "N more settled sales"
THIN_TENURE_CASE = MIN_ORDERS_FOR_SCORE + 2      # 12 sales, too new     → "N more days"
_THIN_ORDERS_CASE_TENURE_DAYS = MIN_TENURE_DAYS + 10    # comfortably past the tenure gate
_THIN_TENURE_CASE_TENURE_DAYS = MIN_TENURE_DAYS - 21    # 9 days trading: fails tenure only

# Prices for the thin sellers' own demo listings, in cents. They need a catalogue to sell from, and
# these are the only listings this script creates.
_THIN_LISTING_PRICES = (450_000, 275_000, 120_000)
# Nairobi CBD-ish, matching the trending seeder's demo centre convention.
_THIN_LAT, _THIN_LNG = -1.2921, 36.8219


def _buyer_sequence(seller_slot: int | str, sales: int, *,
                    regulars: int, one_timers: int) -> list[str]:
    """Buyer uuid for each of ``sales`` sales, mixing repeat customers with one-off ones.

    Returns exactly ``sales`` entries. One-timers are consumed first and appear ONCE each; every
    remaining sale goes to a regular, round-robin, so each regular necessarily lands >1 order as
    long as the sale count exceeds ``one_timers + regulars`` (true for every tier). The resulting
    repeat rate is ``regulars / (regulars + one_timers)`` — the tier's chosen number rather than
    the 1.00 that a single cycled pool produced for every seller.

    Buyer ids are namespaced by ``seller_slot`` so shops do NOT share customers. Sharing would be
    harmless for the repeat term (the scorer groups per seller) but would make the whole platform
    look like eight shops serving the same eight people.

    Deterministic and O(sales): no RNG, so re-runs and tests agree.
    """
    if regulars <= 0:
        raise ValueError("a tier needs at least one regular, or repeat rate pins at 0.0")
    seq = [f"{BUYER_PREFIX}{seller_slot}-{_ONE_TIME_SLICE}{n}" for n in range(min(one_timers, sales))]
    for n in range(sales - len(seq)):
        seq.append(f"{BUYER_PREFIX}{seller_slot}-{_REGULAR_SLICE}{n % regulars}")
    return seq


def _idem(kind: str, seller_id: str, index: int) -> str:
    """Deterministic idempotency key. Re-running REPLAYS each transition through
    ``settlement._replay`` rather than opening a second order, so revenue never doubles."""
    return f"seed-ws-{kind}-{seller_id}-{index}"


def _tenure_days(seller: Seller, now: datetime) -> int:
    """Whole days since the seller row was created — the same quantity ``credit_score`` gates on.

    Shared by the seeding path and the dry-run plan so the plan reports the REAL tenure. It is the
    number that decides whether a shop can score at all (``MIN_TENURE_DAYS``), so a plan that
    printed a placeholder would hide the one seller-level reason seeding might not produce a card.

    ``created_at`` comes back naive from SQLite and aware from Postgres, so it is normalised here
    rather than at every call site. Floored at 1: a seller created minutes ago has traded for a
    day as far as spacing sales across tenure is concerned, and 0 would make the spread degenerate.
    """
    created = seller.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return max(1, int((now - created).total_seconds() // 86400))


def _sale_offset_days(index: int, total: int, tenure_days: int) -> int:
    """Days-before-now for sale ``index`` of ``total``, spread across the seller's own tenure.

    Newest-first (index 0 is the most recent). Even spacing rather than random dates keeps the
    30-day sub-window populated proportionally, so the card's revenue TREND reads as a steady
    business instead of a spike or a collapse. Deterministic — no RNG, so re-runs and tests agree.

    Clamped to ``tenure_days`` because a sale cannot predate the seller row: the oldest sale lands
    at ~85% of tenure, never before the shop existed.
    """
    oldest = max(_NEWEST_SALE_DAYS, int(tenure_days * _TENURE_USE_FRACTION))
    if total <= 1:
        return _NEWEST_SALE_DAYS
    span = oldest - _NEWEST_SALE_DAYS
    return _NEWEST_SALE_DAYS + (span * index) // (total - 1)


# ─────────────────────────── catalogue discovery ───────────────────────────

def _sellable(db, seller_id: str) -> tuple[list[Listing], list[Listing]]:
    """(fixed, bargain) sellable listings for a seller, ordered stably by id.

    "Sellable" means what ``open_order`` will actually accept and price: active, a real product
    (not a timeline ``post``), and priced above zero. A price-0 row would settle for nothing and
    contribute no revenue while still counting as a settled order — inflating the order count
    against the cold-start gate on sales that never moved money.

    One query, both modes partitioned in Python: the alternative (two queries per seller) is a
    needless round trip, and the row count here is a shop's catalogue — tens, not thousands.
    """
    rows = (
        db.query(Listing)
        .filter(
            Listing.seller_id == seller_id,
            Listing.is_active.is_(True),
            Listing.post_kind == POST_KIND_PRODUCT,
            Listing.price_cents > 0,
        )
        .order_by(Listing.id)
        .all()
    )
    fixed = [r for r in rows if r.pricing_mode == "fixed"]
    bargain = [r for r in rows if r.pricing_mode == "bargain"]
    return fixed, bargain


def _candidates(db) -> list[tuple[Seller, list[Listing], list[Listing]]]:
    """Real sellers that can actually trade, strongest catalogue first.

    Ordering is by sellable-product count DESCENDING so ``TIERS`` maps the biggest catalogue to the
    strongest history (see TIERS). Sellers with no sellable product are excluded — notably Eva
    Mokaya, whose only listing is a price-0 timeline post; per the user's decision she stays
    thin-file rather than having a business invented for her.

    Excludes the trending seeder's synthetic pool (``demo-trending-``) and this script's own thin
    demo sellers, which are seeded separately with a fixed shape.
    """
    sellers = (
        db.query(Seller)
        .filter(
            ~Seller.user_uuid.like("demo-trending-%"),
            ~Seller.user_uuid.like(f"{THIN_PREFIX}%"),
        )
        .order_by(Seller.created_at, Seller.id)     # stable tiebreak; no RNG
        .all()
    )
    out = []
    for s in sellers:
        fixed, bargain = _sellable(db, str(s.id))
        if not fixed and not bargain:
            continue
        out.append((s, fixed, bargain))
    # Descending catalogue size; seller id breaks ties so the tier assignment is reproducible.
    out.sort(key=lambda t: (-(len(t[1]) + len(t[2])), str(t[0].id)))
    return out


# ─────────────────────────── one sale, through the real services ───────────────────────────

def _settle_one(db, seller: Seller, listing: Listing, *, buyer: str, index: int) -> Order | None:
    """Drive ONE fixed-price sale to SETTLED (with its receipt) via the real service path.

    Returns the settled order, or None if it could not be completed (logged, never raised — one
    awkward listing must not abort the rest of a shop's history).

    Idempotent: both transitions carry deterministic keys, so a re-run replays the original order
    instead of opening a second one.
    """
    seller_id = str(seller.id)
    try:
        order = settlement.open_order(
            db, buyer, str(listing.id),
            offer_cents=None,                       # fixed: locks at the listing's own price
            idem_key=_idem("open", seller_id, index),
        )
        if order.status == STATUS_SETTLED:
            return order                            # replayed a previous run's completed sale
        if order.status != STATUS_PRICE_LOCKED:
            logger.warning("sale %d for %s: unexpected state %s", index, seller_id, order.status)
            return None
        return settlement.settle(
            db, buyer, str(order.id), idem_key=_idem("settle", seller_id, index),
        )
    except Exception:
        logger.warning("sale %d for %s failed", index, seller_id, exc_info=True)
        db.rollback()
        return None


def _fail_one(db, seller: Seller, listing: Listing, *, buyer: str, index: int) -> Order | None:
    """Drive ONE order to CANCELLED so the shop has a non-perfect fulfilment rate.

    Must use a ``bargain`` listing: a ``fixed`` order jumps straight to ``PRICE_LOCKED``, which is
    not in ``OPEN_STATUSES``, so ``cancel()`` correctly refuses it. A dead negotiation is the only
    honest way to produce a failed order through the real state machine — and it is also the
    commonest way real ones die.

    The buyer opens below list price and then walks away; the seller cancels the stale negotiation.
    """
    seller_id = str(seller.id)
    try:
        # 60% of list: a plausible lowball, comfortably inside _validate_offer's bounds.
        offer = max(1, (int(listing.price_cents) * 60) // 100)
        order = settlement.open_order(
            db, buyer, str(listing.id),
            offer_cents=offer,
            idem_key=_idem("openfail", seller_id, index),
        )
        if order.status == STATUS_CANCELLED:
            return order                            # replayed
        return settlement.cancel(db, seller.user_uuid, str(order.id))
    except Exception:
        # A collision here is expected and harmless: open_order allows only ONE open negotiation per
        # (buyer, listing), so a shop with a single bargain listing cannot host more concurrent dead
        # negotiations than the buyer pool. Logged at debug; the tier's failed count is a target.
        logger.debug("failed-order %d for %s not created", index, seller_id, exc_info=True)
        db.rollback()
        return None


# ─────────────────────────── backdating ───────────────────────────

def _backdate(db, order_id: str, when: datetime) -> None:
    """Move one order's history to ``when`` — ``Order.created_at`` AND its ``Receipt.issued_at``.

    Both, always, in one transaction. The scorer windows them INDEPENDENTLY (receipts drive revenue
    and the settled count; orders drive failed count and the repeat-buyer aggregate), so moving one
    without the other yields a shop with revenue but no orders — a profile that cannot exist, and
    one that would make the card misreport fulfilment.

    Safe because no hash covers a timestamp: ``_event_hash`` (settlement.py) canonicalizes
    (order_id, seq, event_type, actor_uuid, amount_cents, prev_hash) and ``_receipt_hash``
    (receipts.py) canonicalizes the money fields plus the chain tip. Verified by reading both, not
    assumed — and asserted by the test that re-verifies every seeded receipt hash after backdating.

    Two bulk UPDATEs keyed on primary/foreign keys — O(1) per order, no row scan.
    """
    db.execute(update(Order).where(Order.id == order_id).values(created_at=when))
    db.execute(update(Receipt).where(Receipt.order_id == order_id).values(issued_at=when))


def _review_one(db, order: Order, rating: int) -> bool:
    """Attach the buyer's rating to a settled sale. Returns True if a review now exists.

    Ratings are LIFETIME in the scorer (not windowed), so these are not backdated — a review's own
    timestamp does not affect the profile, and leaving it truthful is better than moving it for no
    reason. Already-reviewed orders are a clean no-op (UNIQUE(order_id) → ConflictError).
    """
    try:
        reviews.create_review(db, order.buyer_uuid, str(order.id), rating=rating, body=None)
        return True
    except reviews.ConflictError:
        return True                                 # replayed: this sale is already reviewed
    except Exception:
        logger.warning("review for order %s failed", order.id, exc_info=True)
        db.rollback()
        return False


# ─────────────────────────── per-seller history ───────────────────────────

def _seed_seller(
    db, seller: Seller, fixed: list[Listing], bargain: list[Listing], *,
    tier: Tier, slot: int, now: datetime,
) -> dict:
    """Lay down one seller's whole credit history and return a tally for the log.

    Sales rotate through the shop's OWN listings, so revenue reflects the real catalogue (see the
    module docstring) and every product carries some history rather than one listing absorbing
    every sale.
    """
    tenure_days = _tenure_days(seller, now)

    settled: list[Order] = []
    priceable = fixed or bargain        # a shop with only bargain listings still gets history
    buyers = _buyer_sequence(slot, tier.settled,
                             regulars=tier.regulars, one_timers=tier.one_timers)
    for i in range(tier.settled):
        listing = priceable[i % len(priceable)]
        order = _settle_one(db, seller, listing, buyer=buyers[i], index=i)
        if order is None:
            continue
        settled.append(order)
        _backdate(db, str(order.id), now - timedelta(
            days=_sale_offset_days(len(settled) - 1, tier.settled, tenure_days),
        ))
    db.commit()

    # Failed orders need a bargain listing (a fixed order cannot be cancelled — see _fail_one).
    #
    # Their buyers are a SEPARATE `-f` namespace, one per failed order. Two reasons, both measured
    # against the scorer: it counts unique/repeat buyers over SETTLED orders only, so reusing a
    # settled buyer here would not change the repeat rate but would misrepresent who bought what;
    # and `open_order` permits only one open negotiation per (buyer, listing), so a shared buyer
    # would collide and silently drop failed orders on a shop with few bargain listings.
    failed = 0
    if bargain:
        for i in range(tier.failed):
            order = _fail_one(db, seller, bargain[i % len(bargain)],
                              buyer=f"{BUYER_PREFIX}{slot}-f{i}", index=i)
            if order is None:
                continue
            failed += 1
            # Spread the dead negotiations across tenure too: all of them landing today would read
            # as a shop that just started failing, which is a different (and worse) credit story.
            _backdate(db, str(order.id), now - timedelta(
                days=_sale_offset_days(failed - 1, max(1, tier.failed), tenure_days),
            ))
        db.commit()

    # Ratings attach to the OLDEST sales: a buyer reviews after delivery, so the newest sales being
    # unreviewed is the realistic state (and keeps rating_count below settled, as in real data).
    reviewed = 0
    for rating, order in zip(tier.ratings, reversed(settled)):
        if _review_one(db, order, rating):
            reviewed += 1

    # The demo's real sellers opt in to the WeesStock market (§WeesStock F4 — the investor
    # discovery surface): the market shows only consenting sellers, and a dev stack needs
    # content. Idempotent — every run re-asserts the same flag (a seller may later unlist via
    # the API; re-seeding does not fight them).
    seller.weesstock_listed = True
    db.commit()

    return {
        "seller": seller.display_name, "tier": tier.name, "tenure_days": tenure_days,
        "settled": len(settled), "failed": failed, "reviewed": reviewed,
    }


# ─────────────────────────── thin-file demo sellers ───────────────────────────

def _ensure_thin_seller(db, slot: str, display_name: str, *, tenure_days: int,
                        now: datetime) -> tuple[Seller, list[Listing]]:
    """Idempotently create one thin-file demo seller with a small catalogue to sell from.

    These are the ONLY sellers and listings this script creates: the real accounts trade on their
    own catalogues. They exist because the cold-start UI cannot otherwise be seen against real rows
    once every real seller is scored, and because each gate needs its own failing case.

    The seller's ``created_at`` is written directly — it is the sole input to ``tenure_days``, and
    the tenure gate is exactly what one of these two sellers must fail. ``get_or_create_seller``
    stamps it from the clock, so a freshly-created row is always 0 days old.
    """
    user_uuid = f"{THIN_PREFIX}{slot}"
    shop_created = now - timedelta(days=tenure_days)

    seller = db.query(Seller).filter(Seller.user_uuid == user_uuid).one_or_none()
    if seller is None:
        shop = catalog.create_shop(db, user_uuid, schemas.ShopCreate(
            name=f"{display_name} Shop", lat=_THIN_LAT, lng=_THIN_LNG,
            display_name=display_name, category="general",
        ))
        for n, price in enumerate(_THIN_LISTING_PRICES):
            catalog.create_listing(db, user_uuid, str(shop.id), schemas.ListingCreate(
                title=f"{display_name} Item {n + 1}", price_cents=price, stock_qty=99,
            ))
        seller = db.query(Seller).filter(Seller.user_uuid == user_uuid).one()

    # Re-asserted every run, not just at creation: tenure is measured from NOW, so a fixed date
    # would drift past the gate as days pass and the thin-file demo would silently start scoring.
    db.execute(update(Seller).where(Seller.id == seller.id).values(created_at=shop_created))
    db.commit()
    db.refresh(seller)

    fixed, bargain = _sellable(db, str(seller.id))
    return seller, (fixed or bargain)


def _seed_thin(db, *, now: datetime) -> list[dict]:
    """Seed the two cold-start cases — one failing the ORDER gate, one failing the TENURE gate.

    Both branches of the card's growth prompt are server-driven (``orders_needed`` /
    ``days_needed``), so this is the only way to inspect each against real rows.
    """
    out = []
    cases = (
        ("a", "WeesStock Thin A", THIN_ORDERS_CASE, _THIN_ORDERS_CASE_TENURE_DAYS, "orders gate"),
        ("b", "WeesStock Thin B", THIN_TENURE_CASE, _THIN_TENURE_CASE_TENURE_DAYS, "tenure gate"),
    )
    for slot, name, sales, tenure_days, gate in cases:
        seller, listings = _ensure_thin_seller(db, slot, name, tenure_days=tenure_days, now=now)
        if not listings:
            logger.warning("thin seller %s has no sellable listing; skipped", name)
            continue
        done = 0
        # Mostly one-timers with a couple of regulars: a young shop with a handful of sales has
        # barely had time to build repeat custom, and the slot namespace ("thin-a"/"thin-b") keeps
        # these buyers out of the real sellers' customer bases.
        thin_buyers = _buyer_sequence(f"thin-{slot}", sales, regulars=2, one_timers=max(1, sales - 4))
        for i in range(sales):
            order = _settle_one(db, seller, listings[i % len(listings)],
                                buyer=thin_buyers[i], index=i)
            if order is None:
                continue
            done += 1
            # Inside tenure, so the thin-file story stays coherent: a 9-day-old shop cannot have a
            # 40-day-old receipt.
            _backdate(db, str(order.id), now - timedelta(
                days=_sale_offset_days(done - 1, sales, tenure_days),
            ))
        db.commit()
        out.append({"seller": name, "tier": f"THIN ({gate})", "tenure_days": tenure_days,
                    "settled": done, "failed": 0, "reviewed": 0})
    return out


# ─────────────────────────── entrypoints ───────────────────────────

def seed(db, *, now: datetime | None = None, dry_run: bool = False) -> list[dict]:
    """Seed the whole spread. Returns one tally dict per seller (also the dry-run plan).

    Refuses to run against production: these are money-shaped rows, and no environment flag should
    be able to authorize writing fabricated receipts into a real ledger.
    """
    if settings.is_production():
        raise RuntimeError(
            "refusing to seed WeesStock credit history in production: this writes settled orders "
            "and receipts"
        )
    now = now or datetime.now(timezone.utc)

    candidates = _candidates(db)
    if dry_run:
        # The plan reports what a real run WOULD do, using the same inputs the real run uses:
        # real tenure (not a placeholder), and `failed` zeroed for a shop with no bargain listing,
        # because a fixed-price order cannot be cancelled (see _fail_one).
        plan = [
            {"seller": s.display_name, "tier": TIERS[min(i, len(TIERS) - 1)].name,
             "tenure_days": _tenure_days(s, now),
             "settled": TIERS[min(i, len(TIERS) - 1)].settled,
             "failed": TIERS[min(i, len(TIERS) - 1)].failed if b else 0}
            for i, (s, _f, b) in enumerate(candidates)
        ]
        for seller, name in _skipped_sellers(db):
            plan.append({"seller": name, "tier": f"SKIPPED ({_SKIP_REASON_NO_PRODUCT})",
                         "tenure_days": _tenure_days(seller, now),
                         "settled": 0, "failed": 0})
        return plan

    results = []
    for slot, (seller, fixed, bargain) in enumerate(candidates):
        # Sellers beyond the ladder reuse its weakest rung rather than being skipped — every
        # trading shop should have some history, and the spread is set by the strongest few.
        tier = TIERS[min(slot, len(TIERS) - 1)]
        results.append(_seed_seller(db, seller, fixed, bargain,
                                    tier=tier, slot=slot, now=now))
    results.extend(_seed_thin(db, now=now))
    return results


def _skipped_sellers(db) -> list[tuple[Seller, str]]:
    """(seller, display_name) for real sellers with a shop but nothing sellable.

    Reported so a skip reads as a decision rather than an oversight (Eva Mokaya is the live
    example). The Seller row is returned, not just the name, so the plan can report its real
    tenure alongside everyone else's.
    """
    sellers = (
        db.query(Seller)
        .join(Shop, Shop.seller_id == Seller.id)
        .filter(
            ~Seller.user_uuid.like("demo-trending-%"),
            ~Seller.user_uuid.like(f"{THIN_PREFIX}%"),
        )
        .distinct()
        .all()
    )
    out = []
    for seller in sellers:
        fixed, bargain = _sellable(db, str(seller.id))
        if not fixed and not bargain:
            out.append((seller, seller.display_name))
    return out


def purge(db) -> dict:
    """Remove everything this script created, and nothing else.

    Scoped strictly to the demo prefixes. Deletion order follows the FKs: reviews and receipts
    reference orders, order_events reference orders, so orders go last. Idempotency keys are
    matched on this script's own key prefix so a real user's retry record is never touched.

    ``order_events`` is append-only by service discipline (§7) — the services never delete a row
    there. Purging a DEMO order's events is a deliberate exception, sound because the chain is
    per-order: removing an entire order's chain leaves every other order's chain intact and
    verifying. A real order's events are never in scope here.
    """
    if settings.is_production():
        raise RuntimeError("refusing to purge in production")

    order_ids = [
        str(r[0]) for r in
        db.query(Order.id).filter(Order.buyer_uuid.like(f"{BUYER_PREFIX}%")).all()
    ]
    counts = {"orders": len(order_ids)}
    if order_ids:
        counts["reviews"] = db.execute(
            delete(Review).where(Review.order_id.in_(order_ids))).rowcount
        counts["receipts"] = db.execute(
            delete(Receipt).where(Receipt.order_id.in_(order_ids))).rowcount
        counts["order_events"] = db.execute(
            delete(OrderEvent).where(OrderEvent.order_id.in_(order_ids))).rowcount
        db.execute(delete(Order).where(Order.id.in_(order_ids)))
    counts["idem_keys"] = db.execute(
        delete(IdempotencyKey).where(IdempotencyKey.idem_key.like("seed-ws-%"))).rowcount

    # The thin demo sellers' own shops/listings, then the sellers themselves.
    thin_ids = [
        str(r[0]) for r in
        db.query(Seller.id).filter(Seller.user_uuid.like(f"{THIN_PREFIX}%")).all()
    ]
    if thin_ids:
        counts["thin_listings"] = db.execute(
            delete(Listing).where(Listing.seller_id.in_(thin_ids))).rowcount
        counts["thin_shops"] = db.execute(
            delete(Shop).where(Shop.seller_id.in_(thin_ids))).rowcount
        counts["thin_sellers"] = db.execute(
            delete(Seller).where(Seller.id.in_(thin_ids))).rowcount
    db.commit()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan (per-seller tier and volumes) without writing")
    parser.add_argument("--purge", action="store_true",
                        help="remove every row this script created (demo prefixes only)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db = SessionLocal()
    try:
        if args.purge:
            logger.info("purged: %s", purge(db))
            return
        rows = seed(db, dry_run=args.dry_run)
        for r in rows:
            logger.info(
                "%-28s %-34s tenure=%3dd settled=%3d failed=%2d%s",
                # Indexed, not .get()-with-default: every row (plan or result) carries a real
                # tenure, and a placeholder 0 here would read as "cannot score" — the one thing
                # this line exists to tell you.
                str(r["seller"])[:28], r["tier"], r["tenure_days"],
                r["settled"], r["failed"],
                "" if args.dry_run else f" reviewed={r.get('reviewed', 0)}",
            )
        logger.info("%s %d seller(s)", "planned" if args.dry_run else "seeded", len(rows))
    finally:
        db.close()


if __name__ == "__main__":
    main()
