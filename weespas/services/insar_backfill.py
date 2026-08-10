"""One-time backfill: bring every existing listing into sync with the InSAR map.

After this feature ships, new uploads are verified on submit (services.insar_verify_tasks).
This sweep does the same for the listings that already existed — resolving each one's
coordinate against the InSAR footprints and stamping its `verification_status` — but
SILENTLY (no inbox notification; nobody wants 200 "we verified your old listing" pings).

Properties of the sweep:
  - Keyset-paginated by id (bounded memory; re-runnable from scratch).
  - Idempotent: re-running just re-resolves. `resolve_and_link` upserts/cleans the
    BuildingLink, so a row already monitored/not_monitored is simply confirmed.
  - Honest: a listing whose resolve comes back 'unavailable' (InSAR DB off) stays
    'unavailable' and is left for a later re-run — never silently flipped to a tier.

Reuses the same `_verify` core as the live task with notify=False, so the status-mapping
logic lives in exactly one place.
"""
from __future__ import annotations

import logging
from typing import Dict

from PE.weespas.core.database import SessionLocal
from PE.weespas.models.property import Property
from PE.weespas.services.insar_verify_tasks import _verify

logger = logging.getLogger(__name__)


def backfill_verification(batch: int = 500) -> Dict[str, int]:
    """Sweep all active listings, resolve+link+stamp each (no notifications).

    Returns a tally keyed by resulting coverage (monitored / needs_confirmation /
    monitored_land / not_monitored / unavailable / missing). Safe to run repeatedly —
    a human-confirmed link is authoritative and is never re-resolved over (the guard
    lives in resolve_and_link).
    """
    tally: Dict[str, int] = {}
    last_id = ""  # keyset cursor (ids are uuid strings; "" sorts before all)
    scanned = 0

    while True:
        db = SessionLocal()
        try:
            rows = (
                db.query(Property.id)
                .filter(Property.is_active.is_(True), Property.id > last_id)
                .order_by(Property.id.asc())
                .limit(batch)
                .all()
            )
            if not rows:
                break
            for (listing_id,) in rows:
                last_id = listing_id
                try:
                    coverage = _verify(
                        db, listing_id=listing_id, recipient_user_id=None, notify=False
                    )
                except Exception as exc:
                    # One bad row must not abort the sweep. Roll back its partial txn,
                    # tally it, and continue — a re-run will retry it.
                    logger.warning("backfill: listing %s failed: %s", listing_id, exc)
                    try:
                        db.rollback()
                    except Exception:
                        pass
                    coverage = "error"
                tally[coverage] = tally.get(coverage, 0) + 1
                scanned += 1
        finally:
            db.close()

    logger.info("backfill complete: scanned=%s tally=%s", scanned, tally)
    return tally
