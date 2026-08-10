"""Pure unit tests for the ranking function — monotonic in each signal, no I/O."""
from datetime import datetime, timedelta, timezone

from PE.commerce.services import ranking

_NOW = datetime(2026, 6, 27, 12, 0, 0, tzinfo=timezone.utc)
_W = dict(w_distance=0.5, w_freshness=0.3, w_intent=0.2, radius_m=2000.0, halflife_h=24.0)


def _score(distance_m, age_h, intent):
    return ranking.score(
        distance_m=distance_m,
        created_at=_NOW - timedelta(hours=age_h),
        intent_weight=intent,
        now=_NOW,
        **_W,
    )


def test_closer_scores_higher():
    assert _score(100, 1, 1.0) > _score(1500, 1, 1.0)


def test_fresher_scores_higher():
    assert _score(500, 1, 1.0) > _score(500, 100, 1.0)


def test_higher_intent_scores_higher():
    assert _score(500, 1, 1.0) > _score(500, 1, 0.1)


def test_proximity_zero_at_radius_edge():
    # At the radius edge proximity term is 0; only freshness+intent remain.
    edge = _score(2000, 0, 1.0)
    assert abs(edge - (0.3 * 1.0 + 0.2 * 1.0)) < 1e-9


def test_intent_clamped_to_one():
    # An out-of-range intent_weight cannot inflate the score past the weight.
    assert _score(0, 0, 5.0) == _score(0, 0, 1.0)


# ----------------------------- §8 promo boost (pure) -----------------------------

def test_promo_boost_full_at_start_zero_at_expiry():
    started = _NOW
    expires = _NOW + timedelta(hours=2)
    assert abs(ranking.promo_boost(started, expires, started) - 1.0) < 1e-9
    # Linear decay: halfway through the window the boost is ~0.5.
    mid = started + timedelta(hours=1)
    assert abs(ranking.promo_boost(started, expires, mid) - 0.5) < 1e-9
    # At/after expiry → 0.0 (no boost, the post has decayed).
    assert ranking.promo_boost(started, expires, expires) == 0.0
    assert ranking.promo_boost(started, expires, expires + timedelta(seconds=1)) == 0.0


def test_promo_boost_zero_when_unpromoted():
    # A listing with no window contributes nothing — never negative, never raises.
    assert ranking.promo_boost(None, None, _NOW) == 0.0
    assert ranking.promo_boost(_NOW, None, _NOW) == 0.0


def test_promo_boost_naive_timestamps_assumed_utc():
    # SQLite returns naive datetimes; the boost must not raise on a naive/aware mix.
    started = datetime(2026, 6, 27, 12, 0, 0)          # naive
    expires = datetime(2026, 6, 27, 14, 0, 0)          # naive
    now = datetime(2026, 6, 27, 13, 0, 0, tzinfo=timezone.utc)
    assert abs(ranking.promo_boost(started, expires, now) - 0.5) < 1e-9


def test_promo_is_additive_and_bounded():
    # A promoted listing outranks an identical un-promoted one...
    base = ranking.score(distance_m=500, created_at=_NOW, intent_weight=1.0, now=_NOW, **_W)
    boosted = ranking.score(
        distance_m=500, created_at=_NOW, intent_weight=1.0, now=_NOW,
        promo_started_at=_NOW, promo_expires_at=_NOW + timedelta(hours=2),
        w_promo=0.25, **_W,
    )
    assert boosted > base
    assert abs((boosted - base) - 0.25) < 1e-9  # exactly the promo term at window start


def test_promo_cannot_override_proximity():
    # The anti-cold-start guarantee: a FAR promoted listing must not bury a CLOSE un-promoted one.
    close_unpromoted = ranking.score(
        distance_m=50, created_at=_NOW, intent_weight=1.0, now=_NOW, **_W,
    )
    far_promoted = ranking.score(
        distance_m=1900, created_at=_NOW, intent_weight=1.0, now=_NOW,
        promo_started_at=_NOW, promo_expires_at=_NOW + timedelta(hours=2),
        w_promo=0.25, **_W,
    )
    assert close_unpromoted > far_promoted


def test_media_is_additive_and_defaults_off():
    # Omitting the media args yields exactly the pre-media score (byte-identical default).
    base = ranking.score(distance_m=500, created_at=_NOW, intent_weight=1.0, now=_NOW, **_W)
    with_weight_no_media = ranking.score(
        distance_m=500, created_at=_NOW, intent_weight=1.0, now=_NOW,
        w_media=0.15, has_media=0.0, **_W,
    )
    assert abs(with_weight_no_media - base) < 1e-12  # no media ⇒ no nudge
    with_media = ranking.score(
        distance_m=500, created_at=_NOW, intent_weight=1.0, now=_NOW,
        w_media=0.15, has_media=1.0, **_W,
    )
    assert with_media > base
    assert abs((with_media - base) - 0.15) < 1e-9  # exactly the media term


def test_media_cannot_override_proximity():
    # The anti-cold-start guarantee for media too: a FAR image-bearing listing must not bury a
    # CLOSE imageless one — the media nudge (0.15) is well below w_distance (0.5).
    close_no_media = ranking.score(
        distance_m=50, created_at=_NOW, intent_weight=1.0, now=_NOW,
        w_media=0.15, has_media=0.0, **_W,
    )
    far_with_media = ranking.score(
        distance_m=1900, created_at=_NOW, intent_weight=1.0, now=_NOW,
        w_media=0.15, has_media=1.0, **_W,
    )
    assert close_no_media > far_with_media


def test_has_media_parses_not_truthy():
    # Must PARSE the JSON string, not string-truthiness-test it.
    assert ranking.has_media(None) is False
    assert ranking.has_media("") is False
    assert ranking.has_media("[]") is False          # truthy non-empty string, but no media
    assert ranking.has_media("not json") is False    # malformed ⇒ False, never raises
    assert ranking.has_media('["/uploads/a.jpg"]') is True
    assert ranking.has_media('{"not": "a list"}') is False


def test_precomputed_promo_matches_window_derived():
    # The feed evaluates promo_boost ONCE and passes it in as `promo=`; that must yield exactly the
    # same score as letting score() derive it from the window (so the single-eval refactor is a
    # pure de-duplication, not a behaviour change).
    started, expires = _NOW, _NOW + timedelta(hours=2)
    mid = started + timedelta(hours=1)
    window_derived = ranking.score(
        distance_m=500, created_at=_NOW, intent_weight=1.0, now=mid,
        promo_started_at=started, promo_expires_at=expires, w_promo=0.25, **_W,
    )
    precomputed = ranking.score(
        distance_m=500, created_at=_NOW, intent_weight=1.0, now=mid, w_promo=0.25,
        promo=ranking.promo_boost(started, expires, mid), **_W,
    )
    assert abs(precomputed - window_derived) < 1e-12
    # An explicit promo overrides the window args entirely (the passed value is authoritative).
    forced_zero = ranking.score(
        distance_m=500, created_at=_NOW, intent_weight=1.0, now=mid,
        promo_started_at=started, promo_expires_at=expires, w_promo=0.25, promo=0.0, **_W,
    )
    no_promo = ranking.score(
        distance_m=500, created_at=_NOW, intent_weight=1.0, now=mid, w_promo=0.25, **_W,
    )
    assert abs(forced_zero - no_promo) < 1e-12
