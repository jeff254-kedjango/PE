"""Background InSAR footprint-verification of a listing.

When an agent/owner uploads a listing, we resolve its (lat, lon) against the InSAR
building footprints OFF the request thread (the spatial search can be slow), stamp the
listing's `verification_status`, and drop a notification in the uploader's inbox telling
them whether it landed on our monitored grid.

The cardinal rule travels through here intact: `unavailable` (InSAR DB offline) is NOT a
final answer and NOT "safe" — we set the status, send NO notification, and leave the row
re-verifiable. `resolve_point` swallows DuckDB errors into `unavailable` rather than
raising, so the task's retry only fires on genuine errors in our own writes.

Idempotency: a 120 s SETNX lock keyed by listing_id absorbs a double-submit so we don't
double-notify. The backfill (services/insar_backfill.py) calls the same core with
notify=False.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from PE.weespas.core.celery_app import celery_app
from PE.weespas.core.database import SessionLocal
from PE.weespas.models.property import (
    Property,
    VERIFICATION_NOT_MONITORED,
    VERIFICATION_UNAVAILABLE,
)
from PE.weespas.models.notification import KIND_LISTING_VERIFICATION
from PE.weespas.services import insar_resolver, notification_service
from PE.weespas.services.celery_helpers import redis_setnx_lock

logger = logging.getLogger(__name__)


def _copy_for(coverage: str, title: str) -> tuple[str, str]:
    """User-facing (title, body) for a delivered notification. Only monitored /
    not_monitored are ever delivered (unavailable is silent)."""
    if coverage == insar_resolver.COVERAGE_MONITORED:
        return (
            "Your listing is on the grid ✅",
            f"“{title}” sits on a building we actively monitor for ground movement. "
            "Tap to see its risk reading.",
        )
    if coverage == insar_resolver.COVERAGE_NEEDS_CONFIRMATION:
        return (
            "Help us pin your building 📍",
            f"“{title}” is near a few buildings we monitor — tap to confirm which one it "
            "is so we show the right risk reading. Takes 5 seconds.",
        )
    if coverage == insar_resolver.COVERAGE_MONITORED_LAND:
        return (
            "Ground estimate for your land 🌍",
            f"“{title}” is open land (no building reading). We've estimated the ground "
            "movement from nearby monitored buildings — tap to see it.",
        )
    return (
        "We couldn't place this one on our map",
        f"“{title}” is outside our current InSAR ground-footprints, so we can't give "
        "it a risk reading yet. Everything else about your listing is live.",
    )


def _verification_link(coverage: str, listing_id: str) -> str:
    """Deep-link for the notification. A needs_confirmation listing goes straight to the
    tap-to-confirm flow (?confirm=1); everything else opens the listing."""
    base = f"/properties/{listing_id}"
    if coverage == insar_resolver.COVERAGE_NEEDS_CONFIRMATION:
        return f"{base}?confirm=1"
    return base


def _verify(db, *, listing_id: str, recipient_user_id: str | None, notify: bool) -> str:
    """Core verification, shared by the task and the backfill sweep.

    Resolves + links the listing, stamps verification_status/verified_at, and (when
    notify) records a notification atomically with the status update. Returns the
    resulting coverage string.
    """
    prop = db.query(Property).filter(Property.id == listing_id).first()
    if prop is None:
        return "missing"

    address = prop.address
    if address is None or address.latitude is None or address.longitude is None:
        # No coordinate to resolve — honestly not monitored (never implied "safe").
        prop.verification_status = VERIFICATION_NOT_MONITORED
        prop.verified_at = datetime.now(timezone.utc)
        if notify and recipient_user_id:
            title, body = _copy_for(VERIFICATION_NOT_MONITORED, prop.title)
            notification_service.create(
                db, user_id=recipient_user_id, kind=KIND_LISTING_VERIFICATION,
                title=title, body=body, link=f"/properties/{prop.id}",
            )
        db.commit()
        return VERIFICATION_NOT_MONITORED

    # Pass the listing's attributes so the resolver can disambiguate a clustered pin
    # (category floor prior + veto-only text); category comes via the controlled-dropdown
    # relationship (slug), never free text. All optional — a missing attribute just means
    # distance-only ranking, still safe.
    category_slug = prop.category.slug if prop.category is not None else None
    result = insar_resolver.resolve_and_link(
        db, listing_id=listing_id, lat=float(address.latitude),
        lon=float(address.longitude),
        category=category_slug, title=prop.title, description=prop.description,
        size_numeric=prop.size_numeric,
    )
    # coverage strings ARE the status values (identity map — see models.property).
    prop.verification_status = result.coverage
    prop.verified_at = datetime.now(timezone.utc)

    if (
        notify
        and recipient_user_id
        and result.coverage != VERIFICATION_UNAVAILABLE
    ):
        title, body = _copy_for(result.coverage, prop.title)
        notification_service.create(
            db, user_id=recipient_user_id, kind=KIND_LISTING_VERIFICATION,
            title=title, body=body,
            link=_verification_link(result.coverage, prop.id),
        )

    db.commit()
    return result.coverage


@celery_app.task(
    name="insar.verify_listing",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
    acks_late=True,
)
def verify_listing(
    self, listing_id: str, recipient_user_id: str | None = None, notify: bool = True
) -> str:
    """Verify one freshly-uploaded listing against the InSAR footprints and notify
    the uploader. The listing row is already committed before this fires (see
    routers/properties.create_property), so a worker crash never strands it — it
    stays 'pending' and a re-run (or the backfill sweep) resolves it.
    """
    # Idempotency: collapse a rapid double-submit. If we can't take the lock, another
    # run is already handling this listing — skip rather than double-notify.
    if notify and not redis_setnx_lock(f"insar:verify:{listing_id}", 120):
        logger.info("insar.verify_listing(%s): deduped (lock held)", listing_id)
        return "deduped"

    db = SessionLocal()
    try:
        coverage = _verify(
            db, listing_id=listing_id, recipient_user_id=recipient_user_id, notify=notify
        )
        logger.info("insar.verify_listing(%s): %s", listing_id, coverage)
        return coverage
    except Exception as exc:
        logger.warning(
            "insar.verify_listing(%s) failed (attempt %s): %s",
            listing_id, self.request.retries, exc,
        )
        try:
            db.rollback()
        except Exception:
            pass
        raise
    finally:
        db.close()
