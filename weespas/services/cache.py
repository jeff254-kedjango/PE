"""Shared Redis client.

Single connection pool reused across requests. Decoded responses so values
come back as ``str``/``int`` (we JSON-encode/decode our own values).
"""
from redis import Redis

from PE.weespas.core.config import settings

redis_client: Redis = Redis.from_url(settings.redis_url, decode_responses=True)


def feed_user_key(user_id: str) -> str:
    return f"feed:user:{user_id}"


def feed_anon_key(geo_bucket: str) -> str:
    return f"feed:anon:{geo_bucket}"


def feed_videos_user_key(user_id: str) -> str:
    """Cache key for the per-user short-video feed (separate from image feed)."""
    return f"feed:v:user:{user_id}"


def feed_videos_anon_key(geo_bucket: str) -> str:
    """Cache key for the per-session anon short-video feed."""
    return f"feed:v:anon:{geo_bucket}"
