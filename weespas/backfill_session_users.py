#!/usr/bin/env python3
"""
Backfill UserSession.user_id for historical anonymous sessions.

Why this exists
---------------
The session middleware used to create user_sessions rows with user_id=NULL.
The /analytics/engagement endpoint (which powers the three line charts on
the Staff dashboard) inner-joins user_sessions to users on s.user_id, so
anonymous rows are invisible to it. After the middleware patch, *new*
sessions get stamped on the first authed request — this script does a
one-off best-effort sweep of pre-fix history.

Heuristic
---------
For every user that already has at least one linked session (post-fix), find
that user's earliest linked session and claim every still-anonymous session
that:

  • shares the same (ip_address, user_agent) tuple
  • was created within `LOOKBACK_DAYS` before the earliest linked session
  • is still user_id IS NULL (never overwrite another user's claim)

This is intentionally conservative — same IP + same UA + tight window. We
prefer leaving rows anonymous (charts stay empty for that range) over
attributing sessions to the wrong user, because the engagement chart's job
is to inform Staff decisions and silent misattribution is worse than no
data.

Usage
-----
    python backfill_session_users.py                # dry-run, prints what would change
    python backfill_session_users.py --apply       # actually writes
    python backfill_session_users.py --apply --lookback 60   # widen window

Safety
------
- Wrapped in a single transaction per user, so a partial failure on one
  user doesn't poison the whole run.
- Idempotent: running twice is a no-op because the second pass finds zero
  anonymous matches.
- Cheap by design: per-user query uses (ip_address, user_agent, created_at)
  filters; idx_session_user_created on user_sessions does most of the work.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import timedelta

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from PE.weespas.core.database import SessionLocal
from PE.weespas.models.analytics import UserSession
from PE.weespas.models.user import User


DEFAULT_LOOKBACK_DAYS = 30


def _earliest_linked_session(db: Session, user_id: str) -> UserSession | None:
    return (
        db.query(UserSession)
        .filter(UserSession.user_id == user_id)
        .order_by(UserSession.created_at.asc())
        .first()
    )


def _claim_for_user(db: Session, user_id: str, lookback_days: int, apply: bool) -> int:
    """Returns the number of rows that were (or would be) updated for this user."""
    anchor = _earliest_linked_session(db, user_id)
    if not anchor or not anchor.ip_address or not anchor.user_agent:
        # No anchor, or anchor lacks the join keys we need — skip rather
        # than guess. A missing ip/UA on the anchor would balloon the match
        # set wildly; we'd rather miss this user than over-attribute.
        return 0

    window_start = anchor.created_at - timedelta(days=lookback_days)

    candidates = (
        db.query(UserSession)
        .filter(
            and_(
                UserSession.user_id.is_(None),
                UserSession.ip_address == anchor.ip_address,
                UserSession.user_agent == anchor.user_agent,
                UserSession.created_at >= window_start,
                UserSession.created_at < anchor.created_at,
            )
        )
        .all()
    )

    if not candidates:
        return 0

    if apply:
        for sess in candidates:
            sess.user_id = user_id
        db.commit()

    return len(candidates)


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill UserSession.user_id for anonymous historical sessions.")
    parser.add_argument("--apply", action="store_true", help="Actually write changes (default: dry-run).")
    parser.add_argument(
        "--lookback",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help=f"How many days before each user's first authed session to scan (default: {DEFAULT_LOOKBACK_DAYS}).",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        # Users with at least one linked session — only they can anchor a claim.
        anchored_user_ids = [
            uid
            for (uid,) in db.query(UserSession.user_id)
            .filter(UserSession.user_id.isnot(None))
            .group_by(UserSession.user_id)
            .all()
        ]

        total_anon_before = db.query(func.count(UserSession.id)).filter(UserSession.user_id.is_(None)).scalar() or 0
        print(f"Anonymous sessions before: {total_anon_before}")
        print(f"Users with at least one linked session: {len(anchored_user_ids)}")
        print(f"Lookback window: {args.lookback} days")
        print(f"Mode: {'APPLY' if args.apply else 'dry-run'}")
        print()

        claimed_total = 0
        for user_id in anchored_user_ids:
            try:
                n = _claim_for_user(db, user_id, args.lookback, apply=args.apply)
                if n:
                    user = db.query(User).filter(User.id == user_id).first()
                    label = f"{user.email if user else '?'} ({user.role.value if user and user.role else '?'})"
                    print(f"  {'+' if args.apply else '~'} {n:>4}  {user_id}  {label}")
                    claimed_total += n
            except Exception as e:
                print(f"  ! failed for user_id={user_id}: {e}")
                try:
                    db.rollback()
                except Exception:
                    pass

        print()
        print(f"Total {'claimed' if args.apply else 'matched'}: {claimed_total}")
        if not args.apply:
            print("Dry-run — re-run with --apply to write.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
