"""Unit tests for the per-shop sponsored-cap OVERRIDE service (§8.3 item 1).

Covers: owner-checked apply (cross-owner ⇒ None), idempotent UPSERT + re-open-as-pending, ceiling
clamp, staff decide (approve/reject/default-cap), and the hot-path resolve_caps (approved-positive
only, empty-input short-circuit)."""
from __future__ import annotations

from PE.commerce.core.config import settings
from PE.commerce.models.seller import Seller, Shop
from PE.commerce.services import boost_cap, proximity

_LAT, _LNG = -1.2921, 36.8219


def _seed_shop(db, *, user_uuid="seller-A", name="Shop"):
    seller = Seller(user_uuid=user_uuid, display_name="S")
    db.add(seller)
    db.flush()
    shop = Shop(seller_id=seller.id, name=name, lat=_LAT, lng=_LNG)
    proximity.set_location(shop, _LAT, _LNG)
    db.add(shop)
    db.commit()
    return seller, shop


# ----------------------------- apply -----------------------------

def test_apply_creates_pending_for_owner(db_session):
    _, shop = _seed_shop(db_session)
    row = boost_cap.apply_for_override(db_session, "seller-A", shop.id, requested_cap=5)
    assert row is not None
    assert row.status == "pending"
    assert row.requested_cap == 5
    assert row.approved_cap is None


def test_apply_cross_owner_returns_none_no_leak(db_session):
    _, shop = _seed_shop(db_session, user_uuid="seller-A")
    # A different seller must not be able to apply against a shop they don't own.
    assert boost_cap.apply_for_override(db_session, "seller-B", shop.id, requested_cap=5) is None


def test_apply_unknown_shop_returns_none(db_session):
    _seed_shop(db_session)
    assert boost_cap.apply_for_override(db_session, "seller-A", "no-such-shop", 5) is None


def test_apply_clamps_to_ceiling(db_session):
    _, shop = _seed_shop(db_session)
    row = boost_cap.apply_for_override(
        db_session, "seller-A", shop.id, requested_cap=settings.boost_cap_override_max + 999
    )
    assert row.requested_cap == settings.boost_cap_override_max


def test_apply_clamps_floor_to_one(db_session):
    _, shop = _seed_shop(db_session)
    row = boost_cap.apply_for_override(db_session, "seller-A", shop.id, requested_cap=0)
    assert row.requested_cap == 1


def test_reapply_upserts_single_row_and_resets_pending(db_session):
    _, shop = _seed_shop(db_session)
    first = boost_cap.apply_for_override(db_session, "seller-A", shop.id, requested_cap=4)
    boost_cap.decide_override(db_session, "staff-1", first.id, approve=True)
    # Re-applying must reuse the SAME row (unique per shop) and re-open it as pending, clearing the
    # prior approval so a fresh ask can't ride a stale grant.
    second = boost_cap.apply_for_override(db_session, "seller-A", shop.id, requested_cap=7)
    assert second.id == first.id
    assert second.status == "pending"
    assert second.requested_cap == 7
    assert second.approved_cap is None
    assert second.decided_by is None
    # And resolve now sees nothing (no longer approved).
    assert boost_cap.resolve_caps(db_session, {shop.id}) == {}


# ----------------------------- get_override (non-destructive read) -----------------------------

def test_get_override_owned_no_row(db_session):
    _, shop = _seed_shop(db_session)
    owned, row = boost_cap.get_override(db_session, "seller-A", shop.id)
    assert owned is True
    assert row is None


def test_get_override_owned_with_row(db_session):
    _, shop = _seed_shop(db_session)
    boost_cap.apply_for_override(db_session, "seller-A", shop.id, requested_cap=4)
    owned, row = boost_cap.get_override(db_session, "seller-A", shop.id)
    assert owned is True
    assert row is not None and row.requested_cap == 4


def test_get_override_cross_owner_not_owned_no_leak(db_session):
    _, shop = _seed_shop(db_session, user_uuid="seller-A")
    owned, row = boost_cap.get_override(db_session, "seller-B", shop.id)
    assert owned is False and row is None


def test_get_override_never_mutates_an_approval(db_session):
    # The whole reason this read exists: opening the seller control must NOT reset an approved
    # override back to pending (which is what apply_for_override does).
    _, shop = _seed_shop(db_session)
    r = boost_cap.apply_for_override(db_session, "seller-A", shop.id, requested_cap=6)
    boost_cap.decide_override(db_session, "staff-1", r.id, approve=True)
    owned, row = boost_cap.get_override(db_session, "seller-A", shop.id)
    assert owned is True
    assert row.status == "approved" and row.approved_cap == 6
    # Still approved for the hot path — the read left it untouched.
    assert boost_cap.resolve_caps(db_session, {shop.id}) == {shop.id: 6}


# ----------------------------- decide -----------------------------

def test_decide_approve_default_cap_is_requested(db_session):
    _, shop = _seed_shop(db_session)
    row = boost_cap.apply_for_override(db_session, "seller-A", shop.id, requested_cap=6)
    decided = boost_cap.decide_override(db_session, "staff-1", row.id, approve=True)
    assert decided.status == "approved"
    assert decided.approved_cap == 6
    assert decided.decided_by == "staff-1"
    assert decided.decided_at is not None


def test_decide_approve_explicit_cap_clamped(db_session):
    _, shop = _seed_shop(db_session)
    row = boost_cap.apply_for_override(db_session, "seller-A", shop.id, requested_cap=6)
    decided = boost_cap.decide_override(
        db_session, "staff-1", row.id, approve=True,
        approved_cap=settings.boost_cap_override_max + 50,
    )
    assert decided.approved_cap == settings.boost_cap_override_max


def test_decide_reject_clears_cap(db_session):
    _, shop = _seed_shop(db_session)
    row = boost_cap.apply_for_override(db_session, "seller-A", shop.id, requested_cap=6)
    boost_cap.decide_override(db_session, "staff-1", row.id, approve=True)
    rejected = boost_cap.decide_override(db_session, "staff-2", row.id, approve=False)
    assert rejected.status == "rejected"
    assert rejected.approved_cap is None
    assert boost_cap.resolve_caps(db_session, {shop.id}) == {}


def test_decide_unknown_id_returns_none(db_session):
    assert boost_cap.decide_override(db_session, "staff-1", "no-such-id", approve=True) is None


# ----------------------------- resolve (hot path) -----------------------------

def test_resolve_empty_input_short_circuits(db_session):
    assert boost_cap.resolve_caps(db_session, set()) == {}


def test_resolve_returns_only_approved_positive(db_session):
    _, shop_a = _seed_shop(db_session, user_uuid="a", name="A")
    _, shop_b = _seed_shop(db_session, user_uuid="b", name="B")
    _, shop_c = _seed_shop(db_session, user_uuid="c", name="C")
    # A: approved cap 4 → included. B: pending → excluded. C: no override → absent.
    ra = boost_cap.apply_for_override(db_session, "a", shop_a.id, requested_cap=4)
    boost_cap.decide_override(db_session, "staff", ra.id, approve=True)
    boost_cap.apply_for_override(db_session, "b", shop_b.id, requested_cap=9)  # left pending
    caps = boost_cap.resolve_caps(db_session, {shop_a.id, shop_b.id, shop_c.id})
    assert caps == {shop_a.id: 4}


# ----------------------------- list_pending -----------------------------

def test_list_pending_fifo_and_excludes_decided(db_session):
    _, s1 = _seed_shop(db_session, user_uuid="a", name="A")
    _, s2 = _seed_shop(db_session, user_uuid="b", name="B")
    r1 = boost_cap.apply_for_override(db_session, "a", s1.id, requested_cap=3)
    boost_cap.apply_for_override(db_session, "b", s2.id, requested_cap=5)
    boost_cap.decide_override(db_session, "staff", r1.id, approve=True)  # r1 no longer pending
    pending = boost_cap.list_pending(db_session)
    assert [p.shop_id for p in pending] == [s2.id]
