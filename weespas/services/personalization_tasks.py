"""Celery tasks that keep the personalized feed cache fresh.

Invocations from request handlers use ``.delay(...)`` so the request thread
never blocks on the cache write.
"""
from __future__ import annotations

from PE.weespas.core.celery_app import celery_app
from PE.weespas.services.personalization import PersonalFeedService


@celery_app.task(
    # EXPLICIT name, like every other task in this app. Without it Celery derives
    # the name from `func.__module__`, which made this the one task whose name
    # depended on *which import path* loaded the file: the API imports
    # `PE.weespas.services.personalization_tasks`, so it published
    # "PE.weespas.services.personalization_tasks.invalidate_user_feed", while the
    # worker's `include=` list loaded the same file as bare "services.*" and
    # registered the short name. The worker then rejected every message with
    # "Received unregistered task of type ..." — and because `apply_async` does
    # NOT raise for a name no worker knows, the caller's `except Exception`
    # fallback never fired and the invalidation was silently dropped.
    # The "feeds." prefix also routes it to the feeds queue (task_routes), which
    # is where its chained partner `feeds.prewarm_user_feed` already runs.
    name="feeds.invalidate_user_feed",
    ignore_result=True,
    acks_late=False,
)
def invalidate_user_feed(user_id: str) -> None:
    PersonalFeedService.invalidate(user_id)
