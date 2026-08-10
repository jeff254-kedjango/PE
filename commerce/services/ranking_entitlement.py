"""Ranking entitlement service (§8, Chunk B) — the paywall gate for radius > 200 km.

Two functions only in this increment (payment integration is deferred):

  * ``has_active_entitlement(db, user_uuid, now)`` — returns True iff the caller has a row
    with ``expires_at > now``. O(1) via ``ix_ranking_entitlements_user_expires``.
  * ``grant_entitlement(db, user_uuid, kind, now)`` — write path used by admin scripts and
    tests today; the payment webhook will call the same function tomorrow. Duration is
    determined by ``kind`` (2h for ``one_time_2h``, 365 days for ``annual``), so the caller
    never encodes durations by hand → a single place to change if pricing shifts.

Not exposed to buyers/sellers directly this increment; the endpoint layer (routers) is the
only real caller today.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from PE.commerce.models.ranking import (
    ENTITLEMENT_KIND_ANNUAL,
    ENTITLEMENT_KIND_ONE_TIME_2H,
    ENTITLEMENT_KINDS,
    RankingEntitlement,
)


# Duration table — a single source of truth. If we later want to sell 7-day passes or similar,
# adding a kind + a row here is the whole change (plus the ENTITLEMENT_KINDS tuple).
_DURATIONS = {
    ENTITLEMENT_KIND_ONE_TIME_2H: timedelta(hours=2),
    ENTITLEMENT_KIND_ANNUAL: timedelta(days=365),
}


def _as_utc(dt: datetime) -> datetime:
    """Tolerate a naive datetime by treating it as UTC (SQLite returns naive datetimes).
    Never mutates the input (returns a new object)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def has_active_entitlement(db: Session, user_uuid: str, now: datetime) -> bool:
    """True iff the caller has at least one entitlement row whose ``expires_at`` is in the
    future. A caller with NO row, ALL expired, or an empty ``user_uuid`` returns False (the
    caller MUST be identified — a blank sub is treated as "no grant"). O(1): the composite
    index ``(user_uuid, expires_at DESC)`` makes this an index seek + one range predicate.
    """
    if not user_uuid:
        return False
    now_utc = _as_utc(now)
    # `EXISTS` semantics: we only care whether ANY qualifying row is present, not how many.
    # Using `.first()` on a bounded query returns as soon as one row matches.
    row = (
        db.query(RankingEntitlement.id)
        .filter(
            RankingEntitlement.user_uuid == user_uuid,
            RankingEntitlement.expires_at > now_utc,
        )
        .first()
    )
    return row is not None


def grant_entitlement(
    db: Session,
    *,
    user_uuid: str,
    kind: str,
    now: datetime,
) -> RankingEntitlement:
    """Create an entitlement row for the caller and return it. Idempotent for the CALLER —
    but not deduped: two grant calls create two rows, each with its own expiry. That's the
    right shape for future purchases (a buyer topping up an annual sub gets a NEW row whose
    expiry extends BEYOND the still-active one; `has_active_entitlement` reads the
    newest-first index, so the longest-lived active row wins the probe).

    Raises ValueError on an unknown ``kind`` (kept close to the write so the failure surfaces
    at the caller, not later at read time)."""
    if kind not in ENTITLEMENT_KINDS:
        raise ValueError(f"Unknown ranking entitlement kind: {kind!r}")
    if not user_uuid:
        raise ValueError("user_uuid is required")
    duration = _DURATIONS[kind]
    row = RankingEntitlement(
        id=str(uuid.uuid4()),
        user_uuid=user_uuid,
        kind=kind,
        expires_at=_as_utc(now) + duration,
    )
    db.add(row)
    db.flush()
    return row
