"""Structural-flag fusion — the second-sensor (engineer/authority) signal.

Pins the safety asymmetry of `composite_risk` / `danger_level`:
  - no flag (STRUCT_NONE) ⇒ byte-identical to the motion-only path (regression-safe)
  - UNSAFE / AUTH_UNSAFE raise a danger FLOOR even at zero InSAR motion
  - a clearance can NEVER silence a PLASTIC / hard-accel mover
  - a clearance decays with age and is cancelled by movement
  - absence is distinct from "cleared"; clearance never lowers the absolute tier

These are life-safety invariants: a regression here means a real threat is hidden,
so they are asserted exactly, not approximately.
"""
import math

import numpy as np
import pytest

from scripts import postprocess as pp
from scripts.postprocess import (
    composite_risk,
    danger_level,
    STRUCT_NONE,
    STRUCT_CLEARED,
    STRUCT_UNSAFE,
    STRUCT_AUTH_UNSAFE,
    STRUCT_UNSAFE_FLOOR,
    STRUCT_AUTH_UNSAFE_FLOOR,
    COLLAPSE_PROTECT_FLOOR,
    CLEAR_DECAY_DAYS,
    DANGER_HIGH,
    DANGER_CRITICAL,
    FAILURE_ELASTIC,
    FAILURE_PLASTIC,
    CLASS_INDETERMINATE,
    CLASS_INSUFFICIENT_EVIDENCE,
)


def _base(**over):
    d = dict(
        soil_class="red_clay",
        riparian_dist_m=None,
        shoreline_dist_m=None,
        vel=-5.0,
        v_ew=float("nan"),
        accel=float("nan"),
        trend_slope=float("nan"),
        failure_mode=FAILURE_ELASTIC,
        classification=CLASS_INDETERMINATE,
        fused_h_m=3.0,
    )
    d.update(over)
    return d


# ---- regression: no flag == today ----------------------------------------

@pytest.mark.parametrize("vel", [0.0, -2.0, -5.0, -12.0, -30.0])
@pytest.mark.parametrize("soil", ["red_clay", "black_cotton", "weathered_basalt"])
@pytest.mark.parametrize("fmode", [FAILURE_ELASTIC, FAILURE_PLASTIC])
def test_none_is_pure_noop(vel, soil, fmode):
    """A building with no flag scores IDENTICALLY whether the flag args are passed
    or omitted — the fusion is inert by default (the regression-safety guarantee)."""
    args = _base(vel=vel, soil_class=soil, failure_mode=fmode)
    without = composite_risk(**args)
    with_none = composite_risk(**args, structural_flag_state=STRUCT_NONE,
                               flag_age_days=float("nan"))
    assert without == with_none
    d_without = danger_level(vel=vel, v_ew=float("nan"), accel=float("nan"),
                             failure_mode=fmode, classification=CLASS_INDETERMINATE)
    d_with = danger_level(vel=vel, v_ew=float("nan"), accel=float("nan"),
                          failure_mode=fmode, classification=CLASS_INDETERMINATE,
                          structural_flag_state=STRUCT_NONE)
    assert d_without == d_with


# ---- unsafe flags raise a floor regardless of motion ----------------------

def test_unsafe_floors_at_zero_motion():
    """An engineer 'unsafe' flag forces a high score even on a stationary building —
    InSAR is blind to construction quality, so motion ≈ 0 must not read as safe."""
    stationary = _base(vel=0.0)
    assert composite_risk(**stationary) < 0.2  # motion-only: near zero
    flagged = composite_risk(**stationary, structural_flag_state=STRUCT_UNSAFE)
    assert flagged >= STRUCT_UNSAFE_FLOOR


def test_auth_unsafe_floors_higher():
    flagged = composite_risk(**_base(vel=0.0), structural_flag_state=STRUCT_AUTH_UNSAFE)
    assert flagged >= STRUCT_AUTH_UNSAFE_FLOOR


def test_unsafe_never_lowers_a_higher_motion_score():
    """The flag is a one-way ratchet UP: a violent mover already above the floor is
    not pulled down to it."""
    violent = _base(vel=-40.0, failure_mode=FAILURE_PLASTIC)
    motion_only = composite_risk(**violent)
    flagged = composite_risk(**violent, structural_flag_state=STRUCT_UNSAFE)
    assert flagged >= motion_only


def test_unsafe_escalates_danger_tier():
    d = danger_level(vel=0.0, v_ew=float("nan"), accel=float("nan"),
                     failure_mode=FAILURE_ELASTIC, classification=CLASS_INDETERMINATE,
                     structural_flag_state=STRUCT_UNSAFE)
    assert d >= DANGER_HIGH
    d_auth = danger_level(vel=0.0, v_ew=float("nan"), accel=float("nan"),
                          failure_mode=FAILURE_ELASTIC, classification=CLASS_INDETERMINATE,
                          structural_flag_state=STRUCT_AUTH_UNSAFE)
    assert d_auth == DANGER_CRITICAL


def test_unsafe_overrides_insufficient_evidence_cap():
    """An on-the-ground engineer outranks our coherence/σ abstention cap."""
    d = danger_level(vel=-30.0, v_ew=float("nan"), accel=float("nan"),
                     failure_mode=FAILURE_ELASTIC,
                     classification=CLASS_INSUFFICIENT_EVIDENCE,
                     structural_flag_state=STRUCT_AUTH_UNSAFE)
    assert d == DANGER_CRITICAL


# ---- the critical anti-corruption invariant -------------------------------

def test_clearance_cannot_hide_a_plastic_mover():
    """THE load-bearing safety test: a fresh 'certified safe' clearance can NEVER
    pull a PLASTIC (progressive-failure) mover below the protect floor, nor lower
    its CRITICAL tier. This is the bribed-clearance attack — it must fail."""
    plastic = _base(vel=-30.0, failure_mode=FAILURE_PLASTIC)
    cleared_fresh = composite_risk(**plastic, structural_flag_state=STRUCT_CLEARED,
                                   flag_age_days=0.0)
    assert cleared_fresh >= COLLAPSE_PROTECT_FLOOR
    d = danger_level(vel=-30.0, v_ew=float("nan"), accel=float("nan"),
                     failure_mode=FAILURE_PLASTIC, classification=CLASS_INDETERMINATE,
                     structural_flag_state=STRUCT_CLEARED)
    assert d == DANGER_CRITICAL


def test_clearance_cannot_hide_hard_acceleration():
    accel_mover = _base(vel=-5.0, accel=-20.0)  # accel ≤ DANGER_ACCEL_CRITICAL
    cleared = composite_risk(**accel_mover, structural_flag_state=STRUCT_CLEARED,
                             flag_age_days=0.0)
    assert cleared >= COLLAPSE_PROTECT_FLOOR


# ---- clearance is bounded, decaying, motion-overridable -------------------

def test_fresh_clearance_damps_a_mild_mover():
    mild = _base(vel=-5.0)
    base = composite_risk(**mild)
    cleared = composite_risk(**mild, structural_flag_state=STRUCT_CLEARED,
                             flag_age_days=0.0)
    assert cleared < base            # it does reduce
    assert cleared >= base * 0.6     # but only weakly (≤ 35% damp)


def test_stale_clearance_is_inert():
    """A clearance older than the decay horizon has no effect — a 2019 'all clear'
    cannot silence a 2026 reading."""
    mild = _base(vel=-5.0)
    base = composite_risk(**mild)
    stale = composite_risk(**mild, structural_flag_state=STRUCT_CLEARED,
                           flag_age_days=CLEAR_DECAY_DAYS + 50.0)
    assert stale == pytest.approx(base)


def test_clearance_strength_monotonic_decreasing_in_age():
    mild = _base(vel=-5.0)
    scores = [composite_risk(**mild, structural_flag_state=STRUCT_CLEARED,
                             flag_age_days=age)
              for age in (0.0, 200.0, 400.0, 600.0, 730.0)]
    # Older clearance ⇒ less damp ⇒ score rises back toward baseline (monotonic up).
    assert all(b <= a for b, a in zip(scores, scores[1:]))


def test_motion_cancels_clearance():
    """As the building's own movement rises past CLEAR_MOVE_HI, a fresh clearance is
    fully overridden even before it ages out."""
    fast = _base(vel=-18.0)            # large movement magnitude M
    base = composite_risk(**fast)
    cleared = composite_risk(**fast, structural_flag_state=STRUCT_CLEARED,
                             flag_age_days=0.0)
    assert cleared == pytest.approx(base)


def test_nan_age_treated_as_fresh():
    """A clearance with no recorded date is treated as fresh (conservative: the damp
    applies, bounded). It still cannot beat the motion/PLASTIC guards."""
    mild = _base(vel=-5.0)
    nan_age = composite_risk(**mild, structural_flag_state=STRUCT_CLEARED,
                             flag_age_days=float("nan"))
    zero_age = composite_risk(**mild, structural_flag_state=STRUCT_CLEARED,
                              flag_age_days=0.0)
    assert nan_age == pytest.approx(zero_age)


def test_absence_distinct_from_cleared():
    """STRUCT_NONE (uninspected) and STRUCT_CLEARED (inspected, certified) must not
    collapse to the same score — absence is not a clearance."""
    mild = _base(vel=-6.0)
    none_score = composite_risk(**mild, structural_flag_state=STRUCT_NONE)
    cleared_score = composite_risk(**mild, structural_flag_state=STRUCT_CLEARED,
                                   flag_age_days=0.0)
    assert none_score != cleared_score
    assert cleared_score < none_score


# ---- the loader fails safe -------------------------------------------------

def test_loader_defaults_to_none_when_no_export(tmp_path, monkeypatch):
    """No export file ⇒ every building resolves to STRUCT_NONE (never CLEARED)."""
    from scripts import structural_flags as sf
    monkeypatch.setattr(sf, "FLAGS_DIR", tmp_path)  # empty dir
    state, age, obs, src = sf.fetch_structural_flags("nowhere", np.array([1, 2, 3]))
    assert list(state) == [STRUCT_NONE, STRUCT_NONE, STRUCT_NONE]
    assert all(math.isnan(a) for a in age)
    assert obs == [None, None, None]
    assert src == [None, None, None]


def test_loader_reads_and_ages_flags(tmp_path, monkeypatch):
    from datetime import date
    import json
    from scripts import structural_flags as sf
    monkeypatch.setattr(sf, "FLAGS_DIR", tmp_path)
    (tmp_path / "x.json").write_text(json.dumps({
        "as_of": "2026-01-01",
        "flags": {
            "10": {"state": STRUCT_UNSAFE, "observed_at": "2025-01-01", "source": "engineer"},
            "20": {"state": STRUCT_CLEARED, "observed_at": "2025-07-01", "source": "engineer"},
        },
    }))
    state, age, obs, src = sf.fetch_structural_flags("x", np.array([10, 20, 30]))
    assert list(state) == [STRUCT_UNSAFE, STRUCT_CLEARED, STRUCT_NONE]
    assert age[0] == pytest.approx(365.0)   # 2025-01-01 → 2026-01-01
    assert src[0] == "engineer"
    assert state[2] == STRUCT_NONE and src[2] is None


def test_loader_malformed_export_fails_safe(tmp_path, monkeypatch):
    from scripts import structural_flags as sf
    monkeypatch.setattr(sf, "FLAGS_DIR", tmp_path)
    (tmp_path / "bad.json").write_text("{not valid json")
    state, age, obs, src = sf.fetch_structural_flags("bad", np.array([1, 2]))
    assert list(state) == [STRUCT_NONE, STRUCT_NONE]   # never auto-clears on corruption
