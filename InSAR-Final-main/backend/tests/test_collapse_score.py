"""
Unit tests for the unified collapse score + absolute danger scale
(scripts/postprocess.py: `composite_risk` and `danger_level`).

These are pure-function tests over hand-built inputs — no DB, no real InSAR — so
they always run (unlike the synthetic-cohort invariants that skip once an AOI
goes real-InSAR). They pin the life-safety contract the ranking depends on:

  - movement sets the magnitude; susceptibility (soil/proximity) only AMPLIFIES,
    so a still building on bad ground can never out-rank a moving one;
  - acceleration and PLASTIC failure mode — the best pre-collapse cues, and the
    two the OLD score ignored entirely — now drive the score;
  - NaN-means-unknown: an unmeasured `v_ew`/`accel` adds neither phantom risk nor
    a penalty;
  - `danger_level` is an ABSOLUTE, cross-AOI-comparable tier (fixed mm/yr cutoffs)
    that no classification gate can use to hide a visibly failing building.

Run from backend/:  pytest tests/test_collapse_score.py
"""

from __future__ import annotations

import math

from scripts.postprocess import (
    composite_risk,
    danger_level,
    CLASS_INDETERMINATE,
    CLASS_ENV_NOISE,
    CLASS_STABLE_ANCHOR,
    CLASS_INSUFFICIENT_EVIDENCE,
    FAILURE_ELASTIC,
    FAILURE_PLASTIC,
    DANGER_STABLE,
    DANGER_LOW,
    DANGER_ELEVATED,
    DANGER_HIGH,
    DANGER_CRITICAL,
    COLLAPSE_PROTECT_FLOOR,
    GATE_STABLE_CAP,
)

NAN = float("nan")


def _score(**overrides) -> float:
    """composite_risk with safe, inert defaults; override only what a test probes.

    Defaults describe a still, well-behaved building on neutral ground: every
    movement term is zero and the classification is INDETERMINATE (no gate)."""
    kw = dict(
        soil_class="red_clay",
        riparian_dist_m=None,
        shoreline_dist_m=None,
        vel=0.0,
        v_ew=0.0,
        accel=0.0,
        trend_slope=0.0,
        failure_mode=FAILURE_ELASTIC,
        classification=CLASS_INDETERMINATE,
        fused_h_m=9.0,
    )
    kw.update(overrides)
    return composite_risk(**kw)


# ---------------------------------------------------------------------------
# 1. A real progressive failure must out-rank a stable building on bad ground.
#    This is the headline bug the rewrite fixes: the OLD score (40% static
#    susceptibility, blind to PLASTIC/accel) ranked a still black-cotton tower
#    ABOVE an actively failing one.
# ---------------------------------------------------------------------------
def test_plastic_outranks_stable_bad_soil():
    plastic = _score(
        soil_class="weathered_basalt", vel=-6.0, trend_slope=-6.0,
        failure_mode=FAILURE_PLASTIC, fused_h_m=9.0,
    )
    stable_bad_soil = _score(
        soil_class="black_cotton", vel=0.0, fused_h_m=30.0,
    )
    assert plastic > stable_bad_soil


# ---------------------------------------------------------------------------
# 2. Wide dynamic range: a still building on the worst soil scores ~0, NOT the
#    inflated mid-band the old formula produced. This is what lets the absolute
#    danger scale mean anything.
# ---------------------------------------------------------------------------
def test_still_building_bad_soil_scores_near_zero():
    s = _score(
        soil_class="black_cotton", vel=0.0, accel=0.0, v_ew=NAN,
        fused_h_m=30.0, classification=CLASS_STABLE_ANCHOR,
    )
    assert s < 0.05


# ---------------------------------------------------------------------------
# 3. Acceleration is a first-class input now: an accelerating building strictly
#    out-scores an otherwise identical non-accelerating one.
# ---------------------------------------------------------------------------
def test_acceleration_increases_score():
    accelerating = _score(vel=-5.0, accel=-10.0)
    steady = _score(vel=-5.0, accel=0.0)
    assert accelerating > steady


# ---------------------------------------------------------------------------
# 4. NaN v_ew adds no phantom shear (the old sigmoid floored ~0.056 at v_ew=0):
#    unknown drift ≈ zero drift, and real drift scores strictly higher.
# ---------------------------------------------------------------------------
def test_nan_vew_no_phantom_shear():
    unknown = _score(vel=-5.0, v_ew=NAN)
    zero = _score(vel=-5.0, v_ew=0.0)
    drifting = _score(vel=-5.0, v_ew=4.0)
    assert math.isclose(unknown, zero, abs_tol=1e-9)
    assert drifting > zero


# ---------------------------------------------------------------------------
# 5. danger_level is monotonic in |vel| and |v_ew|.
# ---------------------------------------------------------------------------
def test_danger_level_monotonic():
    def d(vel=0.0, v_ew=0.0):
        return danger_level(
            vel=vel, v_ew=v_ew, accel=0.0,
            failure_mode=FAILURE_ELASTIC, classification=CLASS_INDETERMINATE,
        )

    # deeper subsidence → tier never decreases
    vel_levels = [d(vel=v) for v in (0.0, -3.0, -8.0, -15.0, -25.0)]
    assert vel_levels == sorted(vel_levels)
    assert vel_levels[0] == DANGER_STABLE and vel_levels[-1] == DANGER_CRITICAL

    # larger east-west drift → tier never decreases
    ew_levels = [d(v_ew=e) for e in (0.0, 1.5, 2.5, 5.0)]
    assert ew_levels == sorted(ew_levels)
    assert ew_levels[0] == DANGER_STABLE and ew_levels[-1] == DANGER_HIGH


# ---------------------------------------------------------------------------
# 6. Cross-AOI comparability: danger_level depends ONLY on movement, never on
#    soil/proximity — so a tier means the same thing in every neighbourhood.
# ---------------------------------------------------------------------------
def test_danger_level_independent_of_soil():
    args = dict(vel=-16.0, v_ew=0.0, accel=0.0,
                failure_mode=FAILURE_ELASTIC, classification=CLASS_INDETERMINATE)
    # danger_level has no soil parameter at all; calling it twice is identical by
    # construction. Assert the value and that it reflects movement (HIGH at -16).
    assert danger_level(**args) == danger_level(**args) == DANGER_HIGH


# ---------------------------------------------------------------------------
# 7. A 1-look building (drift unmeasured, v_ew=NaN) is NOT down-ranked for the
#    drift we couldn't measure: same vertical signal → same tier as v_ew=0.
# ---------------------------------------------------------------------------
def test_one_look_not_downranked():
    common = dict(vel=-20.0, accel=0.0,
                  failure_mode=FAILURE_ELASTIC, classification=CLASS_INDETERMINATE)
    one_look = danger_level(v_ew=NAN, **common)
    two_look = danger_level(v_ew=0.0, **common)
    assert one_look == two_look
    assert one_look >= DANGER_HIGH


# ---------------------------------------------------------------------------
# 8. A gate can never hide a visibly failing building: ENV_NOISE + PLASTIC still
#    clears the protect floor.
# ---------------------------------------------------------------------------
def test_gate_cannot_hide_plastic():
    s = _score(
        vel=-6.0, trend_slope=-6.0, failure_mode=FAILURE_PLASTIC,
        classification=CLASS_ENV_NOISE,
    )
    assert s >= COLLAPSE_PROTECT_FLOOR

    # ...and the danger tier on a PLASTIC building survives the INSUFFICIENT cap.
    lvl = danger_level(
        vel=-6.0, v_ew=NAN, accel=0.0,
        failure_mode=FAILURE_PLASTIC, classification=CLASS_INSUFFICIENT_EVIDENCE,
    )
    assert lvl == DANGER_CRITICAL


# ---------------------------------------------------------------------------
# 9. The STABLE_ANCHOR cap still bites a genuine non-mover (reference asset stays
#    visibly low even if its ground is poor).
# ---------------------------------------------------------------------------
def test_stable_cap_holds():
    s = _score(
        soil_class="black_cotton", vel=0.0, v_ew=0.0, accel=0.0,
        fused_h_m=30.0, classification=CLASS_STABLE_ANCHOR,
    )
    assert s <= GATE_STABLE_CAP


# ---------------------------------------------------------------------------
# 10. Confidence-scaled shear + tilt are NaN-INERT by default. The new kwargs
#     default to NaN ("not wired / unmeasured"), so every score is byte-identical
#     to the pre-feature behaviour until the data is explicitly threaded through.
#     This is the zero-regression guarantee the production path relies on.
# ---------------------------------------------------------------------------
def test_new_terms_default_inert():
    for kw in (
        dict(vel=-5.0, v_ew=4.0),
        dict(vel=-12.0, accel=-10.0, v_ew=2.0, trend_slope=-4.0),
        dict(vel=0.0, v_ew=NAN, fused_h_m=30.0, soil_class="black_cotton"),
    ):
        explicit_nan = composite_risk(
            **{**dict(soil_class="red_clay", riparian_dist_m=None, shoreline_dist_m=None,
                      vel=0.0, v_ew=0.0, accel=0.0, trend_slope=0.0,
                      failure_mode=FAILURE_ELASTIC, classification=CLASS_INDETERMINATE,
                      fused_h_m=9.0), **kw},
            v_ew_sigma=NAN, tilt_rate=NAN,
        )
        assert math.isclose(_score(**kw), explicit_nan, abs_tol=1e-12)


# ---------------------------------------------------------------------------
# 11. Differential settlement (tilt) raises the score: two buildings sinking at
#     the SAME rate, one tilting (sinking unevenly across its footprint) and one
#     not, must not score equally — tilt is what cracks structures.
# ---------------------------------------------------------------------------
def test_tilt_increases_score():
    tilting = _score(vel=-5.0, tilt_rate=0.30)   # at the cracking-distortion saturation
    uniform = _score(vel=-5.0, tilt_rate=0.0)    # measured, but no differential
    no_data = _score(vel=-5.0)                   # tilt unmeasured (NaN)
    # Differential settlement RAISES the score above an otherwise-identical building.
    assert tilting > uniform
    # ESCALATE-ONLY: a near-zero / absent tilt must NEVER lower the score — at InSAR
    # resolution "no resolvable tilt" means unknown, not "settling uniformly = safe"
    # (uniform overload still collapses). So measured-zero == unmeasured == no penalty.
    assert math.isclose(uniform, no_data, abs_tol=1e-12)


# ---------------------------------------------------------------------------
# 12. Confidence-scaled shear: identical east-west drift contributes MORE when the
#     decomposition is clean (low σ_ew) than when it's noisy (high σ_ew). A shaky
#     horizontal estimate must not be trusted like a clean one.
# ---------------------------------------------------------------------------
def test_shear_confidence_scaling():
    clean = _score(vel=-4.0, v_ew=5.0, v_ew_sigma=0.5)   # ≤ CLEAN ⇒ full shear weight
    noisy = _score(vel=-4.0, v_ew=5.0, v_ew_sigma=6.0)   # ≥ NOISY ⇒ shear weight ≈ 0
    assert clean > noisy
    # NaN σ_ew (unwired) must match the clean case — full nominal weight by default.
    assert math.isclose(_score(vel=-4.0, v_ew=5.0, v_ew_sigma=NAN),
                        _score(vel=-4.0, v_ew=5.0), abs_tol=1e-12)
