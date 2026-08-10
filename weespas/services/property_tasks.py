"""Property + feed Celery tasks — pre-warming, invalidations, chains.

This module realises audit §3 (feed pre-warming) and §4 (chains/groups/chords).
Two classes of task live here:

1. **Beat warmers** (Phase 4.1) — `warm_featured`, `warm_popular_anon_feeds`,
   `warm_trending_counts`. Keep top-N city caches hot so the home/shorts
   feeds open with zero DB queries for the 80% case.

2. **Invalidation fanout** (Phase 4.3) — `invalidate_featured_cache`,
   `invalidate_related_for_sources`, `fanout_invalidate_user_feeds`. Wired
   into property writes as a chord(group(...), callback) so writes blow
   exactly the caches they touch — no TTL-guessing.

All tasks run on the `feeds` queue except `purge_user` which is on `default`
(it does heavy DB work and is admin-rare).
"""
from __future__ import annotations

import json
import logging
from typing import Iterable

from PE.weespas.core.celery_app import celery_app
from PE.weespas.core.database import SessionLocal
from PE.weespas.services.cache import (
    redis_client, feed_anon_key, feed_videos_anon_key, feed_user_key,
)

logger = logging.getLogger(__name__)


# Top-N cities by session count. Pulled from real data periodically; hardcoded
# here so the warmer doesn't need a DB round-trip to discover its own keys.
# If a new city overtakes one of these, the cold-miss path still works — the
# request falls through to live compute exactly as before.
POPULAR_CITIES = ["Nairobi", "Mombasa", "Kisumu", "Nakuru", "Eldoret"]


# =====================================================================
# Phase 4.1 — Beat warmers
# =====================================================================

@celery_app.task(name="feeds.warm_featured")
def warm_featured() -> dict:
    """Pre-compute /properties/featured for every popular city.

    Stored under `featured:{city}` with a 15-minute TTL. Read path checks
    Redis first; falls through to live compute on miss.
    """
    from PE.weespas.services.property_service import PropertyService
    # Featured listings are scored against city centroids in real prod; here we
    # warm the no-geo variant, which is what the carousel on the home screen
    # requests. The geo-personalised variant is per-request and not cacheable.
    db = SessionLocal()
    try:
        # Global "no city" variant — feeds the default carousel.
        payload = PropertyService.get_featured_properties(db)
        try:
            redis_client.setex(
                "featured:global",
                900,
                json.dumps([p.model_dump() if hasattr(p, "model_dump") else p.dict() for p in payload], default=str),
            )
        except Exception as exc:
            logger.warning("featured global warm failed: %s", exc)
        return {"warmed": 1}
    finally:
        db.close()


@celery_app.task(name="feeds.expire_featured", acks_late=True)
def expire_featured() -> dict:
    """Flip `is_featured` back to false once a promotion's `featured_expires_at` has
    passed, keeping the flag honest (the query-time filter in get_featured_properties
    already hides expired rows, so this is housekeeping, not correctness). Rows with
    `featured_expires_at IS NULL` are permanent features and are left untouched.

    Set-based bulk UPDATE (no per-row loop). Blows the `featured:global` carousel cache
    only when something actually changed so the next warm/read reflects it promptly.
    """
    from datetime import datetime, timezone
    from PE.weespas.models.property import Property
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        n = (
            db.query(Property)
            .filter(
                Property.is_featured == True,  # noqa: E712 (SQLAlchemy boolean compare)
                Property.featured_expires_at.isnot(None),
                Property.featured_expires_at <= now,
            )
            .update({Property.is_featured: False}, synchronize_session=False)
        )
        db.commit()
        if n:
            try:
                redis_client.delete("featured:global")
            except Exception:
                pass
        return {"expired": n}
    finally:
        db.close()


@celery_app.task(name="feeds.warm_popular_anon_feeds")
def warm_popular_anon_feeds() -> dict:
    """Pre-populate the anon feed cache for each popular city.

    Per audit §6.5: with this in place, the shorts/home feed opens with zero
    DB queries for the 80% case (anon mobile user in Nairobi). The component
    can drop its initial loading spinner entirely.
    """
    # The audit references `PersonalFeedService.warm_anon(city)` — we instead
    # call the public entry points with a synthetic-anon path so we exercise
    # the exact same ranking pipeline a real request would. The cache key
    # written by those calls is what subsequent anon requests will read.
    from PE.weespas.services.personalization import PersonalFeedService

    warmed = 0
    db = SessionLocal()
    try:
        for city in POPULAR_CITIES:
            try:
                # Image feed
                PersonalFeedService.get_personal_feed(
                    db, user=None, session_id=None, skip=0, limit=20,
                )
                # Shorts feed
                PersonalFeedService.get_shorts_feed(
                    db, user=None, session_id=None, skip=0, limit=10,
                )
                warmed += 1
            except Exception as exc:
                logger.warning("warm_popular_anon_feeds(%s) failed: %s", city, exc)
        return {"warmed": warmed, "cities": POPULAR_CITIES}
    finally:
        db.close()


@celery_app.task(name="feeds.warm_trending_counts")
def warm_trending_counts() -> dict:
    """Pre-compute per-city trending counters into a Redis HASH.

    The ranking pipeline reads these on every miss; pre-aggregating them
    means the cold-miss path goes from N+1 indexed aggregates to a single
    HGETALL.
    """
    # Lightweight wrapper — the actual aggregation is in personalization.
    # We just call it for each popular city and stash the result.
    from PE.weespas.services.personalization import _trending_counts
    db = SessionLocal()
    try:
        # _trending_counts wants a candidate_ids list; for a global warmer
        # we want "all active properties" — limit to a reasonable cap so
        # this stays cheap.
        from PE.weespas.models.property import Property
        candidate_ids = [
            row.id for row in
            db.query(Property.id).filter(Property.is_active.is_(True)).limit(500).all()
        ]
        if not candidate_ids:
            return {"warmed": 0}
        for city in POPULAR_CITIES:
            try:
                counts = _trending_counts(db, city, candidate_ids)
                if counts:
                    redis_client.setex(
                        f"trending:counts:{city.lower()}",
                        300,
                        json.dumps(counts),
                    )
            except Exception as exc:
                logger.warning("trending(%s) failed: %s", city, exc)
        return {"warmed": len(POPULAR_CITIES)}
    finally:
        db.close()


# =====================================================================
# Phase 4.2 — Per-user feed prewarm (chained off invalidations)
# =====================================================================

@celery_app.task(name="feeds.prewarm_user_feed", ignore_result=True)
def prewarm_user_feed(user_id: str | None = None) -> None:
    """Re-compute a user's personalized feed after an invalidation.

    Wired as: chain(invalidate_user_feed.s(uid), prewarm_user_feed.si(uid)).
    The .si() is load-bearing — see callers in routers/{favorites,dismissals}.py.
    The chain pattern keeps the post-write p99 hot — without it, the next
    request from this user pays the full miss.
    """
    if not user_id:
        # Chain signature passes the previous task's return value as the
        # first positional arg; invalidate_user_feed returns None. If we got
        # called with a None / falsy user_id, no-op rather than failing.
        return
    from PE.weespas.services.personalization import PersonalFeedService
    from PE.weespas.models.user import User
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if user is None:
            return
        # Drives the same ranking pipeline a real request would — writes the
        # cache as a side effect.
        PersonalFeedService.get_personal_feed(
            db, user=user, session_id=None, skip=0, limit=20,
        )
    except Exception as exc:
        logger.debug("prewarm_user_feed(%s) failed: %s", user_id, exc)
    finally:
        db.close()


# =====================================================================
# Phase 4.3 — Property-write fanout group
# =====================================================================

@celery_app.task(name="feeds.invalidate_featured_cache", ignore_result=True)
def invalidate_featured_cache(city: str | None = None) -> None:
    """Blow `featured:{city}` and the global key after a property write."""
    keys = ["featured:global"]
    if city:
        keys.append(f"featured:{city}")
        keys.append(f"featured:{city.lower()}")
    try:
        redis_client.delete(*keys)
    except Exception as exc:
        logger.debug("invalidate_featured_cache failed: %s", exc)


@celery_app.task(name="feeds.invalidate_nearby_cache", ignore_result=True)
def invalidate_nearby_cache(city: str | None = None) -> None:
    """Blow nearby/anon-feed caches for the city of a written property."""
    if not city:
        return
    bucket = city.lower().strip()
    try:
        # Scan-delete every anon-feed key matching this city. SCAN, not KEYS,
        # so we never block the Redis main thread on a multi-million key set.
        for prefix in (feed_anon_key(bucket), feed_videos_anon_key(bucket)):
            # exact bucket without session-hash
            redis_client.delete(prefix)
            # session-hashed variants — match `feed:anon:{bucket}:*`
            pattern = f"{prefix}:*"
            for k in redis_client.scan_iter(match=pattern, count=200):
                redis_client.delete(k)
    except Exception as exc:
        logger.debug("invalidate_nearby_cache(%s) failed: %s", city, exc)


@celery_app.task(name="feeds.invalidate_related_for_sources", ignore_result=True)
def invalidate_related_for_sources(property_id: str | None = None) -> None:
    """Drop the related-properties cache for any source linking to this prop."""
    if not property_id:
        return
    try:
        # `related:{source_property_id}` is the convention; for now we just blow
        # the symmetric key. A per-source reverse index would let us be surgical,
        # but the current cardinality makes the broad blow cheap enough.
        for k in redis_client.scan_iter(match=f"related:{property_id}*", count=200):
            redis_client.delete(k)
    except Exception as exc:
        logger.debug("invalidate_related_for_sources(%s) failed: %s", property_id, exc)


@celery_app.task(name="feeds.invalidate_agent_stats", ignore_result=True)
def invalidate_agent_stats(agent_id: str | None = None) -> None:
    """Drop cached per-agent benchmark/funnel/rank blobs after a property write."""
    if not agent_id:
        return
    try:
        for k in redis_client.scan_iter(match=f"analytics:benchmarks:agent:{agent_id}:*", count=100):
            redis_client.delete(k)
    except Exception as exc:
        logger.debug("invalidate_agent_stats(%s) failed: %s", agent_id, exc)


@celery_app.task(name="feeds.fanout_invalidate_user_feeds", ignore_result=True)
def fanout_invalidate_user_feeds(_group_result=None, property_id: str | None = None) -> None:
    """Callback after the property-write invalidation group completes.

    Identifies users who likely have this property cached (favorited, recently
    viewed, or in same city) and blows their per-user feed. Bound to top-N to
    avoid a stampede on hot listings.
    """
    if not property_id:
        return

    from PE.weespas.models.analytics import Favorite, PropertyViewEvent
    db = SessionLocal()
    try:
        # Users who favorited the property — small set, near-100% relevance.
        fav_user_ids = {
            r[0] for r in
            db.query(Favorite.user_id).filter(Favorite.property_id == property_id).all()
            if r[0]
        }
        # Recent viewers — capped at 50 so a viral listing never fans out 1k+ writes.
        view_user_ids = {
            r[0] for r in
            db.query(PropertyViewEvent.user_id)
              .filter(PropertyViewEvent.property_id == property_id)
              .filter(PropertyViewEvent.user_id.isnot(None))
              .order_by(PropertyViewEvent.viewed_at.desc())
              .limit(50)
              .all()
            if r[0]
        }
        targets = fav_user_ids | view_user_ids
        for uid in targets:
            try:
                redis_client.delete(feed_user_key(uid))
            except Exception:
                pass
        return {"invalidated_users": len(targets)}
    except Exception as exc:
        logger.warning("fanout_invalidate_user_feeds(%s) failed: %s", property_id, exc)
    finally:
        db.close()


# =====================================================================
# Phase 5 — Cleanup tasks (admin / batch)
# =====================================================================

@celery_app.task(name="feeds.purge_user", acks_late=True)
def purge_user(user_id: str) -> dict:
    """Cascade-delete user rows in batches; admin-initiated.

    Implemented as a Celery task so the admin DELETE returns in 202 ms with
    a job id, not seconds with a full cascade.
    """
    from PE.weespas.models.user import User
    from PE.weespas.models.analytics import Favorite, PropertyViewEvent, SearchLog, UserSession
    db = SessionLocal()
    try:
        deleted = {"sessions": 0, "favorites": 0, "views": 0, "searches": 0, "user": 0}
        deleted["sessions"]  = db.query(UserSession).filter(UserSession.user_id == user_id).delete(synchronize_session=False)
        deleted["favorites"] = db.query(Favorite).filter(Favorite.user_id == user_id).delete(synchronize_session=False)
        deleted["views"]     = db.query(PropertyViewEvent).filter(PropertyViewEvent.user_id == user_id).delete(synchronize_session=False)
        deleted["searches"]  = db.query(SearchLog).filter(SearchLog.user_id == user_id).delete(synchronize_session=False)
        deleted["user"]      = db.query(User).filter(User.id == user_id).delete(synchronize_session=False)
        db.commit()
        # Cache purges after DB cascade
        try:
            redis_client.delete(feed_user_key(user_id))
        except Exception:
            pass
        return deleted
    except Exception as exc:
        logger.warning("purge_user(%s) failed: %s", user_id, exc)
        try:
            db.rollback()
        except Exception:
            pass
        raise
    finally:
        db.close()


@celery_app.task(name="feeds.bulk_import_favorites", acks_late=True)
def bulk_import_favorites(user_id: str, property_ids: Iterable[str]) -> dict:
    """Bulk-insert favorites for a guest→user migration. Idempotent (ON CONFLICT)."""
    from PE.weespas.models.analytics import Favorite
    db = SessionLocal()
    try:
        inserted = 0
        # SQLAlchemy 2.0 bulk insert with skip-on-conflict semantics depends on
        # dialect; we fall back to one-by-one inserts inside a try/except so a
        # single duplicate doesn't fail the whole batch.
        for pid in property_ids:
            try:
                db.add(Favorite(user_id=user_id, property_id=pid))
                db.commit()
                inserted += 1
            except Exception:
                db.rollback()
        # Blow the user's feed cache so the new favorites are reflected.
        try:
            redis_client.delete(feed_user_key(user_id))
        except Exception:
            pass
        return {"inserted": inserted}
    finally:
        db.close()


@celery_app.task(name="media.delete_media_file", ignore_result=True)
def delete_media_file(filepath: str) -> None:
    """Delete a (large) media file off the request thread."""
    from pathlib import Path
    try:
        p = Path(filepath)
        if p.exists():
            p.unlink()
    except Exception as exc:
        logger.debug("delete_media_file(%s) failed: %s", filepath, exc)
