"""Ranking entitlement service tests (§8, Chunk B). Table lives here, service lives here —
no HTTP surface yet. The endpoint (routers/sellers.py) exercises this transitively in B-iii."""
from datetime import datetime, timedelta, timezone

import pytest

from PE.commerce.models.ranking import (
    ENTITLEMENT_KIND_ANNUAL,
    ENTITLEMENT_KIND_ONE_TIME_2H,
    RankingEntitlement,
)
from PE.commerce.services import ranking_entitlement as re

_NOW = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


class TestHasActiveEntitlement:
    def test_no_row_returns_false(self, db_session):
        assert re.has_active_entitlement(db_session, "user-a", _NOW) is False

    def test_blank_uuid_returns_false(self, db_session):
        # A blank sub is not identified — never grant a paywall bypass. Defensive: the
        # endpoint layer should never call with an empty sub, but a bug there must not open
        # the gate.
        assert re.has_active_entitlement(db_session, "", _NOW) is False

    def test_active_row_returns_true(self, db_session):
        re.grant_entitlement(db_session, user_uuid="user-a", kind=ENTITLEMENT_KIND_ONE_TIME_2H, now=_NOW)
        # Check 1 hour later — still inside the 2h window.
        assert re.has_active_entitlement(db_session, "user-a", _NOW + timedelta(hours=1)) is True

    def test_expired_row_returns_false(self, db_session):
        re.grant_entitlement(db_session, user_uuid="user-a", kind=ENTITLEMENT_KIND_ONE_TIME_2H, now=_NOW)
        # 3 hours later — the 2h grant has lapsed.
        assert re.has_active_entitlement(db_session, "user-a", _NOW + timedelta(hours=3)) is False

    def test_annual_kind_lasts_year(self, db_session):
        re.grant_entitlement(db_session, user_uuid="user-a", kind=ENTITLEMENT_KIND_ANNUAL, now=_NOW)
        # 300 days later — still inside the 365d window.
        assert re.has_active_entitlement(db_session, "user-a", _NOW + timedelta(days=300)) is True
        # 400 days later — lapsed.
        assert re.has_active_entitlement(db_session, "user-a", _NOW + timedelta(days=400)) is False

    def test_other_user_is_isolated(self, db_session):
        re.grant_entitlement(db_session, user_uuid="user-a", kind=ENTITLEMENT_KIND_ANNUAL, now=_NOW)
        # User B never bought → still gated.
        assert re.has_active_entitlement(db_session, "user-b", _NOW + timedelta(days=1)) is False

    def test_multiple_active_rows_still_returns_true(self, db_session):
        # Two overlapping grants — the probe cares only whether ANY row is active. This
        # matches the real "buyer tops up before expiry" case.
        re.grant_entitlement(db_session, user_uuid="user-a", kind=ENTITLEMENT_KIND_ONE_TIME_2H, now=_NOW)
        re.grant_entitlement(db_session, user_uuid="user-a", kind=ENTITLEMENT_KIND_ANNUAL, now=_NOW + timedelta(hours=1))
        # Both rows are still active at this point (2h and 365d respectively).
        rows = db_session.query(RankingEntitlement).filter(RankingEntitlement.user_uuid == "user-a").count()
        assert rows == 2
        assert re.has_active_entitlement(db_session, "user-a", _NOW + timedelta(hours=1)) is True

    def test_naive_now_tolerated(self, db_session):
        # SQLite returns naive datetimes; the service must not fall over when called with one.
        re.grant_entitlement(db_session, user_uuid="user-a", kind=ENTITLEMENT_KIND_ONE_TIME_2H, now=_NOW)
        naive_now = (_NOW + timedelta(hours=1)).replace(tzinfo=None)
        assert re.has_active_entitlement(db_session, "user-a", naive_now) is True


class TestGrantEntitlement:
    def test_rejects_unknown_kind(self, db_session):
        with pytest.raises(ValueError):
            re.grant_entitlement(db_session, user_uuid="user-a", kind="lifetime", now=_NOW)

    def test_rejects_empty_uuid(self, db_session):
        with pytest.raises(ValueError):
            re.grant_entitlement(db_session, user_uuid="", kind=ENTITLEMENT_KIND_ONE_TIME_2H, now=_NOW)

    def test_stores_expires_at_derived_from_kind(self, db_session):
        row = re.grant_entitlement(db_session, user_uuid="user-a", kind=ENTITLEMENT_KIND_ONE_TIME_2H, now=_NOW)
        # 2 hours after `now`. Tolerate SQLite's naive-datetime return by comparing timestamps.
        expected = _NOW + timedelta(hours=2)
        got = row.expires_at
        if got.tzinfo is None:
            got = got.replace(tzinfo=timezone.utc)
        assert got == expected
