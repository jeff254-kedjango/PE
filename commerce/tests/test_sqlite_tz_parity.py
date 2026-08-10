"""Regression guard for the SQLite ⇄ Postgres timestamptz parity adapter (conftest `_UTCDateTime`).

Context: prod is Postgres (`timestamptz`: stores UTC, reads back tz-AWARE). SQLite has no native
datetime, so a stock `DateTime(timezone=True)` there stores wall-clock tz-DROPPED and reads back
offset-NAIVE. That mismatch caused a rare, deterministic flake: the SQL window filters in
`boost.eligible_grants` / `flash_sales._active_flash` compare a tz-aware `now` bind against those
naive read-backs and occasionally empty a lane that should be populated. conftest installs an
adapter that restores timestamptz semantics on the SQLite test path. These tests pin that contract
so it cannot silently regress — if the adapter is dropped, the first assertion fails with the exact
`TypeError: can't compare offset-naive and offset-aware datetimes` this was built to eliminate.
"""
from datetime import datetime, timedelta, timezone, date

from PE.commerce.models.boost import BoostGrant
from PE.commerce.models.seller import Seller
from PE.commerce.services import boost as boost_svc

_EAT = timezone(timedelta(hours=3))  # Africa/Nairobi — a non-UTC offset, to prove normalization


def _seed_live_grant(db, started_at, expires_at):
    db.add(Seller(id="s-tz", user_uuid="u-tz", display_name="TZ Shop"))
    db.flush()
    grant = BoostGrant(
        seller_id="s-tz", target_type="listing", target_id="l-tz", tier="mtaa",
        scope_kind="nation", started_at=started_at, expires_at=expires_at,
        business_date=date(2026, 7, 26),
    )
    db.add(grant)
    db.commit()
    db.expire_all()  # force a fresh read from SQLite (not the identity-map cache)
    return grant


def test_datetime_reads_back_tz_aware_utc(db_session):
    """Every stored datetime must come back tz-aware in UTC, exactly like Postgres timestamptz."""
    now = datetime.now(timezone.utc)
    _seed_live_grant(db_session, now, now + timedelta(hours=1))
    row = db_session.query(BoostGrant).first()

    assert row.started_at.tzinfo is not None, "read-back must be tz-AWARE (the flake was naive)"
    assert row.started_at.utcoffset() == timedelta(0), "read-back must be normalized to UTC"
    # The comparison that used to raise TypeError inside the window filters:
    assert row.started_at <= now < row.expires_at


def test_non_utc_input_is_normalized_to_the_same_instant(db_session):
    """A +03:00 write and its UTC equivalent must store the SAME instant (no wall-clock drift)."""
    now = datetime.now(timezone.utc)
    _seed_live_grant(db_session, now.astimezone(_EAT), (now + timedelta(hours=1)).astimezone(_EAT))
    row = db_session.query(BoostGrant).first()

    # Stored as the same instant as the UTC value — offset carried correctly, not dropped.
    assert abs((row.started_at - now).total_seconds()) < 1e-3
    assert abs((row.expires_at - (now + timedelta(hours=1))).total_seconds()) < 1e-3


def test_eligible_grants_window_is_stable_under_aware_now(db_session):
    """The exact flaky path: a live nationwide grant is returned for an aware `now`, every time."""
    now = datetime.now(timezone.utc)
    _seed_live_grant(db_session, now - timedelta(minutes=1), now + timedelta(hours=1))

    for _ in range(20):  # deterministic — no timing window can empty a genuinely-live grant
        rows = boost_svc.eligible_grants(
            db_session, lat=-1.29, lng=36.82, now=datetime.now(timezone.utc)
        )
        assert len(rows) == 1


def test_expired_grant_is_excluded(db_session):
    """Negative control: a window strictly in the past must NOT be returned (the filter still bites)."""
    now = datetime.now(timezone.utc)
    _seed_live_grant(db_session, now - timedelta(hours=2), now - timedelta(hours=1))
    rows = boost_svc.eligible_grants(db_session, lat=-1.29, lng=36.82, now=now)
    assert rows == []
