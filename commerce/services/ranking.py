"""The feed ranking function — a pure, explainable function of stored signals.

This is the "algorithm" (architecture doc §8): normal social media ranks by an existing
following and so taxes every small seller with a cold-start problem. Commerce ranks by
**proximity × freshness × intent** — no follower count anywhere. The function is
deterministic and cheap (O(1) per item), so it runs identically across DB dialects and a
caller can always be told *why* an item ranks where it does.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone


def decode_media_urls(media_urls: str | None) -> list[str]:
    """Decode the stored ``media_urls`` JSON-array string → a list of URL strings. The column holds a
    JSON string (or None); a missing/blank/malformed value yields ``[]``, never raises. Single source
    of the media-decode rule, shared by the feed serializer (``schemas.feed.to_feed_item``) and the
    ranking media nudge (``has_media`` below) so display and scoring can never disagree about whether
    a listing carries media."""
    if not media_urls:
        return []
    try:
        decoded = json.loads(media_urls)
    except (ValueError, TypeError):
        return []
    return [str(u) for u in decoded] if isinstance(decoded, list) else []


def has_media(media_urls: str | None) -> bool:
    """True iff ``media_urls`` decodes to a NON-EMPTY array of URLs. Must PARSE, not
    string-truthiness-test: ``"[]"`` is a truthy non-empty string but means *no media*. Built on
    ``decode_media_urls`` so the "does this listing carry media" rule shares one decoder with the
    feed serializer. O(1) in the bounded stored string."""
    return len(decode_media_urls(media_urls)) > 0


def _proximity(distance_m: float, radius_m: float) -> float:
    """1.0 at the caller's location, decaying linearly to 0.0 at the radius edge.
    Clamped to [0, 1] (a row beyond the radius — only possible via float epsilon at the
    boundary — contributes 0, never negative)."""
    if radius_m <= 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 - distance_m / radius_m))


def _freshness(created_at: datetime, now: datetime, halflife_h: float) -> float:
    """Exponential decay with a tunable half-life: 1.0 brand-new, 0.5 at one half-life,
    etc. Tolerant of naive timestamps (SQLite returns naive datetimes) by assuming UTC."""
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    age_h = max(0.0, (now - created_at).total_seconds() / 3600.0)
    if halflife_h <= 0:
        return 0.0
    return 0.5 ** (age_h / halflife_h)


def _as_utc(dt: datetime) -> datetime:
    """Assume UTC for a naive timestamp (SQLite returns naive datetimes); pass through aware."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def promo_boost(
    promo_started_at: datetime | None,
    promo_expires_at: datetime | None,
    now: datetime,
) -> float:
    """The §8 "selling now" boost: 1.0 at the window's start, decaying LINEARLY to 0.0 at
    expiry; 0.0 when the listing isn't promoted (null window) or the window has already passed.

    Pure function of the stored window vs ``now`` — so the boost decays with NO write (no sweep
    needed; a row simply ranks lower as time passes and contributes nothing once expired). A
    malformed/zero-length window degrades to 0.0 rather than dividing by zero."""
    if promo_expires_at is None:
        return 0.0
    now = _as_utc(now)
    expires = _as_utc(promo_expires_at)
    if now >= expires:
        return 0.0  # window passed → no boost (the post has decayed)
    # Without a recorded start we can't compute a decay fraction, so treat the boost as full
    # until expiry (still bounded, still ends cleanly at the edge).
    if promo_started_at is None:
        return 1.0
    started = _as_utc(promo_started_at)
    window_s = (expires - started).total_seconds()
    if window_s <= 0:
        return 1.0  # degenerate window; full boost until the (already-handled) expiry edge
    remaining_frac = (expires - now).total_seconds() / window_s
    return max(0.0, min(1.0, remaining_frac))


def score(
    *,
    distance_m: float,
    created_at: datetime,
    intent_weight: float,
    now: datetime,
    w_distance: float,
    w_freshness: float,
    w_intent: float,
    radius_m: float,
    halflife_h: float,
    promo_started_at: datetime | None = None,
    promo_expires_at: datetime | None = None,
    w_promo: float = 0.0,
    promo: float | None = None,
    w_media: float = 0.0,
    has_media: float = 0.0,
) -> float:
    """Weighted sum of the normalized signals plus the optional §8 promo boost and a soft media
    nudge. All inputs are stored columns or config constants — no hidden state, no I/O. Higher is
    better.

    The promo term is ADDITIVE on top of the base proximity×freshness×intent score: a "selling
    now" listing ranks above an equivalent un-promoted neighbour, but the boost cannot override
    proximity wholesale (it is one weighted term, and it decays to 0 by expiry). This preserves
    the anti-cold-start property — promotion is a freshness signal, not a pay-to-win override.

    The media term (``w_media * has_media``, ``has_media`` ∈ {0.0, 1.0}) is the same shape: a gentle
    additive nudge so an image-bearing listing edges out an otherwise-equal imageless one. It is a
    QUALITY signal, never a filter — a plain social text post legitimately carries no media and is
    simply not nudged (not excluded), and ``w_media`` stays well below ``w_distance`` so a closer
    imageless item still outranks a far image-rich one (proximity is never overridden). Both
    ``w_media`` and ``has_media`` default to 0.0 so a caller that omits them gets exactly the
    pre-media score (byte-identical).

    ``promo`` lets a caller that ALSO needs the boost value (e.g. to set an ``is_promoted`` flag)
    pass the already-computed ``promo_boost(...)`` in, so the window is evaluated once against a
    single ``now`` rather than twice. When None (the default), it is computed here from the window
    — so existing callers that pass only ``promo_started_at``/``promo_expires_at`` are unchanged."""
    proximity = _proximity(distance_m, radius_m)
    freshness = _freshness(created_at, now, halflife_h)
    intent = max(0.0, min(1.0, intent_weight))  # stored signal, clamped to [0, 1]
    if promo is None:
        promo = promo_boost(promo_started_at, promo_expires_at, now)
    media = max(0.0, min(1.0, has_media))  # {0,1} in practice; clamped defensively
    return (
        w_distance * proximity
        + w_freshness * freshness
        + w_intent * intent
        + w_promo * promo
        + w_media * media
    )
