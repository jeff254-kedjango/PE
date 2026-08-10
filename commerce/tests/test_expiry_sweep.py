"""TTL expiry sweep — settlement.expire_stale + services.expiry_sweeper.run_once.

These exercise the sweep at the service layer (not HTTP): they seed orders directly, back-date
``created_at`` past the TTL, and assert what expires. The critical test is conflict isolation —
the regression for the bug where a single mid-loop CAS conflict rolled back the whole batch.
"""
from datetime import datetime, timedelta, timezone

import pytest

from PE.commerce.core.config import settings
from PE.commerce.models.listing import Listing
from PE.commerce.models.order import (
    OrderEvent,
    STATUS_CANCELLED,
    STATUS_EXPIRED,
    STATUS_OFFERED,
    STATUS_PRICE_LOCKED,
)
from PE.commerce.models.seller import Seller, Shop
from PE.commerce.services import expiry_sweeper, settlement

_LAT, _LNG = -1.2921, 36.8219


def _seller(db, sub="seller-x"):
    s = Seller(user_uuid=sub, display_name="S")
    db.add(s)
    db.flush()
    return s


def _listing(db, seller, *, mode="bargain", price=10000):
    shop = Shop(seller_id=seller.id, name="Shop", lat=_LAT, lng=_LNG)
    db.add(shop)
    db.flush()
    li = Listing(
        shop_id=shop.id, seller_id=seller.id, title="T", price_cents=price,
        currency="KES", pricing_mode=mode, stock_qty=5, is_active=True, lat=_LAT, lng=_LNG,
    )
    db.add(li)
    db.flush()
    return li


def _open_bargain(db, listing, buyer, offer=9000, key="k"):
    return settlement.open_order(db, buyer, listing.id, offer_cents=offer, idem_key=key)


def _backdate(db, order, seconds_old):
    """Force an order's created_at to ``seconds_old`` in the past (so the TTL cutoff sees it)."""
    order.created_at = datetime.now(timezone.utc) - timedelta(seconds=seconds_old)
    db.add(order)
    db.commit()


# ----------------------------- expire_stale core behaviour -----------------------------

def test_expires_only_pending_past_ttl(db_session):
    db = db_session
    seller = _seller(db)
    li = _listing(db, seller)
    ttl = settings.pending_ttl_seconds

    stale = _open_bargain(db, li, "buyer-stale", key="a")
    fresh = _open_bargain(db, li, "buyer-fresh", key="b")
    _backdate(db, stale, ttl + 60)        # older than TTL → should expire
    # fresh stays recent → should survive

    expired = settlement.expire_stale(db)
    assert expired == 1
    db.refresh(stale)
    db.refresh(fresh)
    assert stale.status == STATUS_EXPIRED
    assert fresh.status == STATUS_OFFERED


def test_expire_appends_event(db_session):
    db = db_session
    li = _listing(db, _seller(db))
    o = _open_bargain(db, li, "buyer-ev", key="a")
    _backdate(db, o, settings.pending_ttl_seconds + 60)

    settlement.expire_stale(db)
    events = settlement.order_events(db, o.id)
    assert events[-1].event_type == "expire"
    assert events[-1].actor == "system"


def test_does_not_expire_terminal_or_locked(db_session):
    db = db_session
    seller = _seller(db)
    li_fixed = _listing(db, seller, mode="fixed")
    # A fixed order locks immediately (PRICE_LOCKED) — not an OPEN status, must never expire.
    locked = settlement.open_order(db, "buyer-lk", li_fixed.id, offer_cents=None, idem_key="lk")
    assert locked.status == STATUS_PRICE_LOCKED
    _backdate(db, locked, settings.pending_ttl_seconds + 999)

    # A cancelled (terminal) order also must never expire.
    li_b = _listing(db, seller)
    cancelled = _open_bargain(db, li_b, "buyer-cx", key="cx")
    settlement.cancel(db, "buyer-cx", cancelled.id)
    _backdate(db, cancelled, settings.pending_ttl_seconds + 999)

    assert settlement.expire_stale(db) == 0
    db.refresh(locked)
    db.refresh(cancelled)
    assert locked.status == STATUS_PRICE_LOCKED
    assert cancelled.status == STATUS_CANCELLED


def test_nothing_to_expire_returns_zero(db_session):
    assert settlement.expire_stale(db_session) == 0


# ----------------------------- the conflict-isolation regression -----------------------------

def test_conflict_on_one_order_does_not_roll_back_others(db_session, monkeypatch):
    """THE REGRESSION. Three stale orders; the MIDDLE one loses its CAS race mid-loop (a user
    accept/cancel landing the same instant). We reproduce _cas's real conflict path — it calls
    ``db.rollback()`` then raises ConflictError. The bug: that rollback rolled back the WHOLE
    session, discarding every PRIOR (already-applied-but-uncommitted) expiry in the batch, so the
    returned count lied and those orders silently stayed pending. The fix commits per order, so
    earlier expiries are durable before a later conflict and only the conflicting order is skipped.
    """
    db = db_session
    seller = _seller(db)
    li = _listing(db, seller)
    ttl = settings.pending_ttl_seconds

    orders = []
    for i in range(3):
        o = _open_bargain(db, li, f"buyer-{i}", key=f"k{i}")
        _backdate(db, o, ttl + 60)
        orders.append(o)
    conflict_id = orders[1].id

    real_cas = settlement._cas

    def flaky_cas(db_, order, **kw):
        # The middle order loses the race: do exactly what real _cas does on a lost CAS —
        # rollback the session, then raise. This is the precise path the loop must survive.
        if order.id == conflict_id:
            db_.rollback()
            raise settlement.ConflictError("simulated concurrent transition")
        return real_cas(db_, order, **kw)

    monkeypatch.setattr(settlement, "_cas", flaky_cas)

    expired = settlement.expire_stale(db)

    # Exactly the two non-conflicting orders expired, and they are DURABLE (committed before the
    # conflict) — not wiped by the middle order's rollback. The count is honest.
    assert expired == 2
    for i in (0, 2):
        db.refresh(orders[i])
        assert orders[i].status == STATUS_EXPIRED, f"order {i} should be expired and durable"
    db.refresh(orders[1])
    assert orders[1].status == STATUS_OFFERED, "conflicting order must NOT be expired"


# ----------------------------- run_once (the sweeper entrypoint) -----------------------------

def test_run_once_expires_via_service(db_session):
    db = db_session
    li = _listing(db, _seller(db))
    o = _open_bargain(db, li, "buyer-ro", key="ro")
    _backdate(db, o, settings.pending_ttl_seconds + 60)

    n = expiry_sweeper.run_once(db)
    assert n == 1
    db.refresh(o)
    assert o.status == STATUS_EXPIRED
