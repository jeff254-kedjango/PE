"""
Shared post-processing for the buildings/subsidence/env_index pipeline.

This module is the bridge between the synthetic-data generator (`phenomena.py`)
and the real-InSAR join (`join_insar.py`). Every helper here is AOI-agnostic —
take in numpy arrays of velocity/coherence/displacement, return derived columns
that match the `init_db.sql` schema.

Environmental context for the real-InSAR path is now produced entirely from
real sources by `join_insar.build_real_env` (SoilGrids WRB soil class + OSM
coastline/waterway distance). The old synthetic env generator that lived here
was removed — there is no fabricated soil/distance/built-year code left.

  HONESTY NOTE. Every populated env column is real: `soil_class` (SoilGrids),
  `shoreline_dist_m` / `riparian_dist_m` (OSM geometry), `reclaimed_land`
  (derived from real soil == "reclaim_fill"), `built_year` (real OSM tag or
  NULL). `synthesize_env_index_rows` writes `groundwater_anom`,
  `rainfall_anom_mm` and `ndvi_proxy` as NULL — no real per-quarter source is
  wired and nothing reads them, so we do not fabricate values. `composite_risk`
  is deterministic (no random jitter).
"""

from __future__ import annotations

import math
import random
from datetime import date
from typing import Iterable

import numpy as np
import pyarrow as pa
from statsmodels.tsa.seasonal import STL


# ============================================================================
# Classification + failure-mode codes (mirror frontend bundle.ts)
# ============================================================================

CLASS_INDETERMINATE         = 0
CLASS_CONFIRMED_THREAT      = 1
CLASS_ENV_NOISE             = 2
CLASS_STABLE_ANCHOR         = 3
CLASS_MIXED_SIGNAL          = 4
# No actionable signal: the InSAR signal itself is untrustworthy (velocity σ
# above this AOI's gate, or a dead/failed-STL row). This is the ONLY honest
# "we have nothing" floor. NOTE: a trustworthy-but-non-linear building (σ OK,
# low trend_r2) is NOT here — it routes to MIXED_SIGNAL so we surface the
# accelerating/curved mover instead of burying it (see _classify, and memory
# threat-not-evidence-reframe / accel-not-clean-discriminator).
CLASS_INSUFFICIENT_EVIDENCE = 5

FAILURE_ELASTIC = 0
FAILURE_PLASTIC = 1

SOIL_CLASSES = ["black_cotton", "red_clay", "alluvial", "weathered_basalt", "coral_rag", "reclaim_fill"]


# ============================================================================
# Geometry helpers
# ============================================================================

def _meters_to_deg(at_lat: float) -> tuple[float, float]:
    dlat_per_m = 1.0 / 111_320.0
    dlon_per_m = 1.0 / (111_320.0 * math.cos(math.radians(at_lat)))
    return dlon_per_m, dlat_per_m


# ============================================================================
# InSAR-derived height (with footprint-area scaling)
# ============================================================================

def _insar_height(
    true_h: float,
    footprint_area_m2: float,
    noise_floor_m: float,
    rng: random.Random,
) -> tuple[float, float]:
    """Return (insar_height_m, sigma_m) for one footprint.

    Sentinel-1's coarse spatial resolution (~5 m × 20 m) means small
    footprints sample only a handful of phase pixels — the height inversion
    is noisy. A footprint that's, say, 8×8 m (64 m²) covers fewer than one
    full pixel and σ is high. A 30×20 m (600 m²) footprint covers many
    pixels and σ collapses toward the noise floor.

    Empirical model: σ = max(noise_floor, 30 / sqrt(area)).
    """
    sigma = max(noise_floor_m, 30.0 / math.sqrt(max(footprint_area_m2, 1.0)))
    return true_h + rng.gauss(0.0, sigma), sigma


def _fused_height(footprint_h: float, insar_h: float, insar_sigma: float) -> float:
    """Inverse-variance-weighted blend of the two estimates.

    Floor-count estimate has σ ≈ 1.5 m (uncertainty in floor height + counting
    floors from aerials). InSAR estimate has the per-building σ computed above.
    """
    sigma_floor = 1.5
    w_floor = 1.0 / (sigma_floor ** 2)
    w_insar = 1.0 / (insar_sigma ** 2)
    return (w_floor * footprint_h + w_insar * insar_h) / (w_floor + w_insar)


# ============================================================================
# Coherence-velocity classification (Principle 2 in ARCHITECTURE_TWO)
# ============================================================================

def _classify(
    v_subs: float,
    v_ew: float,
    gamma: float,
    accel: float,
    trend_r2: float,
    sigma: float,
    r2_min: float,
    sigma_max: float,
) -> int:
    """Coherence-gated classification from end-of-series velocities.

    The framework's matrix: high velocity + low coherence = environmental
    surface noise (suppress). High velocity + high coherence = confirmed
    structural threat. Low velocity + high coherence = stable reference
    anchor.

    The V4 framework's table is incomplete: it doesn't say what to do with
    moderate velocity at borderline coherence (γ in 0.35–0.60), or with
    moderate velocity at high coherence. Those buildings would silently
    fall through to INDETERMINATE — a confidently-blank cell that gives a
    non-technical reader no way to distinguish "no signal" from "real
    signal we can't yet name." We add a 5th class, MIXED_SIGNAL, so the
    badge can honestly say "something's moving, watch this one."

    Threat first, confidence modulates (NOT a veto). We classify *the threat to
    occupants*, not the quality of our evidence. So we evaluate movement first,
    then let defensibility downgrade the *label*, never discard the building:

    - σ FAILS (`sigma > sigma_max`): the signal itself is untrustworthy — the
      mm are noise. Only here do we honestly have "no actionable signal" →
      CLASS_INSUFFICIENT_EVIDENCE. The `not (x <= y)` form makes NaN
      (dead / failed-STL rows) fail this too, so they land here as intended.
    - σ PASSES but R² FAILS (`trend_r2 < r2_min`): trustworthy signal that just
      isn't a straight line. On real Huruma this is ~4473 buildings — exactly
      the "clean signal not moving linearly = a building bending into an
      accelerating curve" case. We must SURFACE these, not bury them: any real
      movement is routed to MIXED_SIGNAL ("watch this one") rather than the
      old INSUFFICIENT veto. `accel` only refines wording downstream; it is NOT
      a clean threat discriminator on this data (STABLE accelerates as hard as
      MIXED — see memory accel-not-clean-discriminator), so we do not gate on it.
    - Both pass: the full confident ladder (ENV_NOISE / STABLE / CONFIRMED /
      MIXED / INDETERMINATE) as before.
    """
    # Untrustworthy signal (or dead/NaN row) is the only true "no signal" floor.
    if not (sigma <= sigma_max):
        return CLASS_INSUFFICIENT_EVIDENCE

    v_abs  = abs(v_subs)
    # v_ew is NaN for 1-look buildings (no descending pass → drift not measured).
    # abs(NaN) and every `ew_abs > x` below evaluate False, so an unmeasured
    # building simply doesn't gain a movement label from drift — it is never
    # treated as drift-free, only as drift-unknown. Decomposed buildings (incl. a
    # real ~0) flow through normally. Keep passing NaN, NOT 0, to preserve this.
    ew_abs = abs(v_ew)
    moving = (v_abs > 3.0 or ew_abs > 1.5)

    # Trustworthy signal that isn't a defensible straight line: a real but
    # non-linear (accelerating/curved) mover. Surface as "watch", don't veto.
    if not (trend_r2 >= r2_min):
        return CLASS_MIXED_SIGNAL if moving else CLASS_INDETERMINATE

    if gamma < 0.35 and (v_abs > 10.0 or ew_abs > 5.0):
        return CLASS_ENV_NOISE
    if v_abs < 1.5 and gamma > 0.60:
        return CLASS_STABLE_ANCHOR
    if (v_abs > 10.0 or ew_abs > 2.5) and gamma > 0.60:
        return CLASS_CONFIRMED_THREAT
    if (
        (0.35 <= gamma <= 0.60 and (v_abs > 5.0 or ew_abs > 2.5))
        or (gamma > 0.60 and (3.0 < v_abs <= 10.0 or 1.5 < ew_abs <= 2.5))
    ):
        return CLASS_MIXED_SIGNAL
    return CLASS_INDETERMINATE


# ============================================================================
# Collapse score + absolute danger level (life-safety scoring)
# ============================================================================
#
# Goal: rank buildings by likelihood of COLLAPSE so occupants can be warned
# before it happens. Two outputs, never conflated:
#   - composite_risk  : a continuous [0,1] "collapse score" where MOVEMENT sets
#                       the magnitude and environmental susceptibility only
#                       AMPLIFIES it. A building that isn't moving scores ~0 no
#                       matter how bad its soil — that's what gives the score the
#                       dynamic range an absolute scale needs. Drives the
#                       within-AOI heat-map ranking.
#   - danger_level    : an ABSOLUTE categorical tier (STABLE…CRITICAL) computed
#                       from raw movement quantities with fixed mm/yr cutoffs, so
#                       it means the same thing across AOIs and is robust to any
#                       retuning of the continuous score. Single source of truth
#                       for the frontend badge.
#
# All weights/cutoffs below are EXPERT PRIORS (no collapse ground-truth exists
# yet) — named constants so they can be swapped for fitted values later, exactly
# like DEFENSIBLE_R2_FLOOR.

# -- collapse_score: movement sub-score anchors (saturation points) -----------
COLLAPSE_VEL_FULL_MM_YR    = 20.0   # |subsidence vel| saturating subs term → 1
COLLAPSE_ACCEL_FULL_MM_YR2 = 8.0    # |accel| saturating the accel term → 1
COLLAPSE_SHEAR_KNEE_MM_YR  = 1.5    # |v_ew| at/below this contributes 0 (no phantom shear)
COLLAPSE_SHEAR_SPAN_MM_YR  = 4.0    # |v_ew| = knee+span saturates the shear term → 1
COLLAPSE_SLOPE_FULL_MM_YR  = 15.0   # |STL trend slope| saturating the curve term → 1
COLLAPSE_W_SUBS            = 0.45   # movement-term weights (sum to 1.0)
COLLAPSE_W_ACCEL           = 0.25
COLLAPSE_W_SHEAR           = 0.20   # NOMINAL (clean-decomposition) shear weight
COLLAPSE_W_CURVE           = 0.10
COLLAPSE_PLASTIC_FLOOR     = 0.70   # PLASTIC remaps movement into [floor, 1]
# -- collapse_score: confidence-scaled shear ----------------------------------
# Shear (east-west drift) is the LEAST measurement-trusted movement axis: InSAR's
# ASC+DESC decomposition constrains the vertical well, the east-west poorly, and
# N-S not at all. So shear's weight is scaled by the decomposition σ — full at/below
# CLEAN, ~0 at/above NOISY — and the freed weight flows to the trustworthy vertical
# (subsidence) term. σ_ew anchors: clean 2-look ≈ 0.5–1 mm/yr, decorrelated ≈ 5+.
# NaN σ_ew (signal not wired, or 1-look with no decomposition) ⇒ treated as CLEAN
# ⇒ full nominal weight ⇒ behaviour identical to before wiring.
COLLAPSE_VEW_SIGMA_CLEAN_MM_YR = 1.0   # σ_ew at/below → full shear weight
COLLAPSE_VEW_SIGMA_NOISY_MM_YR = 5.0   # σ_ew at/above → shear weight ≈ 0
# -- collapse_score: angular distortion (differential settlement) -------------
# Uniform settlement is largely benign (rigid-body translation — buildings tolerate
# metres of it); DIFFERENTIAL settlement is what cracks and collapses structures
# (Skempton–MacDonald / Boscardin–Cording angular-distortion limits). `tilt_rate`
# is the local spatial gradient of vertical velocity, (mm/yr)/m — the rate of
# angular distortion. At 0.30 (mm/yr)/m a building reaches the ~1/300 cracking
# distortion in ~11 yr, so we saturate the term there. When a tilt rate is measured
# it claims a fixed share COLLAPSE_W_TILT of the movement magnitude; NaN tilt
# (unmeasured / not wired) ⇒ no blend ⇒ movement unchanged.
COLLAPSE_TILT_FULL_MM_YR_PER_M = 0.30  # |tilt rate| saturating the tilt term → 1
COLLAPSE_W_TILT                = 0.25  # share of movement claimed by tilt when measured
# -- collapse_score: susceptibility multiplier (amplify-only) ------------------
SUSC_MAX_UPLIFT            = 0.30   # max fractional amplification (S_mult ∈ [1, 1.30])
SUSC_W_SOIL               = 0.60
SUSC_W_PROX               = 0.40
RIPARIAN_LAMBDA_M         = 400.0
SHORELINE_LAMBDA_M        = 300.0
SOIL_SUSC_LUT = {
    "black_cotton": 0.9, "alluvial": 0.7, "red_clay": 0.4, "weathered_basalt": 0.1,
    "coral_rag": 0.15, "reclaim_fill": 0.85,
}
SOIL_SUSC_DEFAULT         = 0.3
# -- collapse_score: classification gates (intent preserved) -------------------
GATE_ENV_NOISE_DAMP       = 0.30   # environmental surface noise: damped, not hidden
GATE_INSUFFICIENT_DAMP    = 0.50   # σ-untrustworthy: findable but can't top the ranking
GATE_STABLE_CAP           = 0.15   # reference anchor: capped low
COLLAPSE_PROTECT_FLOOR    = 0.70   # PLASTIC / hard-accel can't be gated below this
# -- collapse_score: external structural-flag fusion (the SECOND sensor) -------
# InSAR sees ground/surface MOTION; it is physically blind to construction quality
# (bad concrete, missing rebar, illegal added floors) — the dominant Nairobi
# collapse driver. An engineer/authority structural flag is that orthogonal signal,
# fused here with a strict safety asymmetry:
#   UNSAFE / AUTH_UNSAFE  → a danger FLOOR applied AFTER the gates, so it amplifies
#                           risk even when InSAR shows zero motion (a stationary but
#                           badly-built block must still score high).
#   NONE (uninspected)    → pure no-op. ABSENCE of a flag NEVER lowers risk
#                           (un-inspected ≠ sound).
#   CLEARED (certified)   → MAY damp risk, but bounded, age-DECAYING, and cancelled
#                           by InSAR motion — a stale "all clear" can't silence a
#                           building that is now moving, and a clearance can NEVER
#                           suppress a PLASTIC/hard-accel mover (the protect floor
#                           re-fires after the damp). This is the anti-corruption
#                           asymmetry: the bribable path (clearance) is the weak one.
STRUCT_NONE, STRUCT_CLEARED, STRUCT_UNSAFE, STRUCT_AUTH_UNSAFE = 0, 1, 2, 3
STRUCT_UNSAFE_FLOOR       = 0.85   # engineer "structurally unsafe" floor (> protect 0.70)
STRUCT_AUTH_UNSAFE_FLOOR  = 0.95   # authority condemnation / enforcement notice
CLEAR_MAX_DAMP            = 0.35   # max fractional reduction a FRESH clearance may apply
CLEAR_DECAY_DAYS          = 730.0  # clearance effect decays linearly to 0 at 2 yr
CLEAR_MOVE_LO             = 0.15   # movement M ≤ this → clearance at full strength
CLEAR_MOVE_HI             = 0.40   # movement M ≥ this → clearance fully cancelled by motion

# -- danger_level: absolute tiers + SENSITIVE cutoffs (favor catching) ---------
# Bias is deliberately toward over-warning: a missed collapse costs lives, a
# false alarm costs an inspection. Cutoffs are anchored to _classify's thresholds.
DANGER_STABLE, DANGER_LOW, DANGER_ELEVATED, DANGER_HIGH, DANGER_CRITICAL = 0, 1, 2, 3, 4
DANGER_VEL_CRITICAL   = -25.0   # mm/yr (negative = subsidence)
DANGER_VEL_HIGH       = -15.0
DANGER_VEL_ELEVATED   = -8.0
DANGER_VEL_LOW        = -3.0
DANGER_VEW_HIGH       = 5.0     # |east-west drift| mm/yr
DANGER_VEW_ELEVATED   = 2.5
DANGER_VEW_LOW        = 1.5
# Acceleration cutoffs are anchored to the MEASUREMENT NOISE floor, not picked as
# round numbers. On real InSAR, per-building accel σ ≈ 8–10 mm/yr² (γ-driven), so a
# loose −3/−6 cutoff fires on ~25–33% of buildings as pure noise — it cannot
# discriminate (matches the field finding that raw accel alone doesn't separate
# threat from stable). We set CRITICAL ≈ 2σ and HIGH ≈ 1σ so an acceleration
# trigger means a real differential outlier, not the noise floor. The trustworthy
# accelerating-failure detector remains PLASTIC (R²-gated STL trend); raw accel
# only ESCALATES, and only once it clears noise. (accel is de-meaned to the
# coherent-bulk median upstream, so these absolute cutoffs are cross-AOI valid.)
DANGER_ACCEL_CRITICAL = -16.0   # mm/yr² (≈2σ; negative = accelerating subsidence)
DANGER_ACCEL_HIGH     = -8.0    # ≈1σ


def _clip01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def composite_risk(
    *,
    soil_class: str,
    riparian_dist_m: float | None,
    shoreline_dist_m: float | None,
    vel: float,
    v_ew: float,
    accel: float,
    trend_slope: float,
    failure_mode: int,
    classification: int,
    fused_h_m: float,
    v_ew_sigma: float = float("nan"),
    tilt_rate: float = float("nan"),
    structural_flag_state: int = STRUCT_NONE,
    flag_age_days: float = float("nan"),
) -> float:
    """Continuous [0,1] COLLAPSE SCORE: movement sets the magnitude, environmental
    susceptibility only amplifies it.

        collapse = clip(M · S_mult) → classification gates → clip[0,1]

    MOVEMENT M ∈ [0,1] blends the signals that actually precede collapse, via
    LINEAR ramps (not a sigmoid — the old sigmoid gave ~0.22 shear at v_ew=0, a
    phantom floor that compressed every score into a narrow band):
      - subsidence velocity      (weight 0.45)
      - acceleration             (weight 0.25) — the single best pre-collapse cue
      - east-west shear drift     (NOMINAL weight 0.20, confidence-scaled — see below)
      - STL trend non-linearity   (weight 0.10)
    A PLASTIC failure mode (well-fit accelerating downtrend) remaps M into
    [COLLAPSE_PLASTIC_FLOOR, 1] — it can no longer rank below a stable building.

    CONFIDENCE-SCALED SHEAR. East-west drift is the least measurement-trusted axis
    (InSAR constrains vertical well, E-W poorly, N-S not at all). `v_ew_sigma` (the
    decomposition σ on v_ew, mm/yr) scales the shear weight from full at/below
    COLLAPSE_VEW_SIGMA_CLEAN to ~0 at/above COLLAPSE_VEW_SIGMA_NOISY; the weight the
    shear term gives up flows to the trustworthy vertical (subsidence) term, so a
    noisy decomposition doesn't dilute the magnitude — it just stops over-trusting a
    shaky horizontal estimate. NaN `v_ew_sigma` ⇒ treated as CLEAN (full nominal
    shear weight) ⇒ identical to the pre-confidence behaviour.

    ANGULAR DISTORTION. `tilt_rate` (|∇vel|, (mm/yr)/m — the spatial gradient of
    vertical velocity from `tilt_rate_from_velocity_field`) is the differential-
    settlement / tilt rate, which is what actually cracks structures (uniform
    settlement is benign). When measured it claims a fixed share COLLAPSE_W_TILT of
    the movement magnitude via a convex blend. NaN `tilt_rate` (unmeasured / not
    wired) ⇒ no blend ⇒ movement unchanged.

    SUSCEPTIBILITY S_mult ∈ [1.0, 1+SUSC_MAX_UPLIFT] is AMPLIFY-ONLY: bad ground
    makes a mover worse, but good ground can never HIDE a mover (a building that
    isn't moving stays ~0 regardless of soil — the dynamic range the absolute
    danger scale depends on).

    STRUCTURAL FLAG (the second sensor — see the STRUCT_* constants). An external
    engineer/authority judgement, fused with a strict safety asymmetry:
      - STRUCT_UNSAFE / STRUCT_AUTH_UNSAFE raise an absolute FLOOR (0.85 / 0.95)
        applied AFTER the protect floor, so a structurally-condemned building scores
        high even with zero InSAR motion (construction-quality failure is invisible
        to InSAR). It can never be gated or damped below that floor.
      - STRUCT_NONE (the default, and any unflagged building) is a pure no-op ⇒
        IDENTICAL to the pre-flag behaviour. Absence never lowers risk.
      - STRUCT_CLEARED applies a BOUNDED damp (≤ CLEAR_MAX_DAMP) that DECAYS to 0 by
        CLEAR_DECAY_DAYS (`flag_age_days`) and is independently cancelled as movement
        M rises over CLEAR_MOVE_LO..HI. Applied BEFORE the protect floor, so a
        PLASTIC / hard-accel mover is re-floored to COLLAPSE_PROTECT_FLOOR even with
        a fresh clearance — a clearance can never silence a real mover. NaN
        `flag_age_days` ⇒ treated as fresh (age 0).

    Sign convention: `vel`/`trend_slope` mm/yr, `accel` mm/yr², all with
    **negative = subsidence/accelerating**. NaN-honest: NaN `v_ew`/`accel`/
    `trend_slope` (unmeasured) contribute 0 to their term — never a phantom value,
    never a penalty. Deterministic (no rng).
    """
    # ---- Movement magnitude M -------------------------------------------------
    subs_score  = _clip01(-vel / COLLAPSE_VEL_FULL_MM_YR)
    accel_score = 0.0 if not math.isfinite(accel) else _clip01(-accel / COLLAPSE_ACCEL_FULL_MM_YR2)
    shear_score = (0.0 if not math.isfinite(v_ew)
                   else _clip01((abs(v_ew) - COLLAPSE_SHEAR_KNEE_MM_YR) / COLLAPSE_SHEAR_SPAN_MM_YR))
    curve_score = 0.0 if not math.isfinite(trend_slope) else _clip01(-trend_slope / COLLAPSE_SLOPE_FULL_MM_YR)
    # Confidence-scale the shear weight by the decomposition σ, and hand whatever it
    # gives up to the trustworthy vertical term (subs). NaN σ_ew ⇒ conf=1 ⇒ full
    # nominal shear weight (pre-confidence behaviour preserved exactly).
    if not math.isfinite(v_ew_sigma):
        shear_conf = 1.0
    else:
        span = COLLAPSE_VEW_SIGMA_NOISY_MM_YR - COLLAPSE_VEW_SIGMA_CLEAN_MM_YR
        shear_conf = _clip01((COLLAPSE_VEW_SIGMA_NOISY_MM_YR - v_ew_sigma) / span) if span > 0 else 1.0
    w_shear = COLLAPSE_W_SHEAR * shear_conf
    w_subs  = COLLAPSE_W_SUBS + (COLLAPSE_W_SHEAR - w_shear)   # reclaimed weight → vertical
    m_cont = (w_subs * subs_score + COLLAPSE_W_ACCEL * accel_score
              + w_shear * shear_score + COLLAPSE_W_CURVE * curve_score)
    # Angular distortion (differential settlement) — the force that actually cracks
    # buildings. ESCALATE-ONLY (like raw accel): a measured tilt raises the magnitude
    # toward 1 by its share, but a near-zero tilt NEVER lowers it. Lowering would be
    # unsafe — at 78 m InSAR resolution we cannot resolve building-scale tilt (real
    # Huruma gradients top out ~0.03 (mm/yr)/m, ≪ the 0.30 structural-distortion
    # saturation), so "no resolvable tilt" means "unknown", not "settling uniformly =
    # safe" (uniform overload still collapses — the South C lesson). NaN ⇒ no signal.
    if math.isfinite(tilt_rate):
        tilt_score = _clip01(abs(tilt_rate) / COLLAPSE_TILT_FULL_MM_YR_PER_M)
        m_cont = m_cont + COLLAPSE_W_TILT * tilt_score * (1.0 - m_cont)
    # PLASTIC: a credible progressive failure floors movement high regardless of
    # which single signal dominated.
    M = (COLLAPSE_PLASTIC_FLOOR + (1.0 - COLLAPSE_PLASTIC_FLOOR) * m_cont
         if failure_mode == FAILURE_PLASTIC else m_cont)
    M = _clip01(M)

    # ---- Susceptibility multiplier S_mult ------------------------------------
    if riparian_dist_m is not None:
        proximity_score = math.exp(-riparian_dist_m / RIPARIAN_LAMBDA_M)
    elif shoreline_dist_m is not None:
        proximity_score = math.exp(-shoreline_dist_m / SHORELINE_LAMBDA_M)
    else:
        proximity_score = 0.0
    soil_score = SOIL_SUSC_LUT.get(soil_class, SOIL_SUSC_DEFAULT)
    load_factor = 1.0 + max(0.0, fused_h_m) / 10.0
    soil_loaded = min(1.0, soil_score * load_factor)
    s_env = _clip01(SUSC_W_SOIL * soil_loaded + SUSC_W_PROX * proximity_score)
    s_mult = 1.0 + SUSC_MAX_UPLIFT * s_env

    composite = _clip01(M * s_mult)

    # ---- Classification gates (intent preserved, can't hide a real mover) -----
    if classification == CLASS_ENV_NOISE:
        composite *= GATE_ENV_NOISE_DAMP            # surface noise: damped, not hidden
    elif classification == CLASS_STABLE_ANCHOR:
        composite = min(composite, GATE_STABLE_CAP)  # reference anchor: capped low
    elif classification == CLASS_INSUFFICIENT_EVIDENCE:
        # σ-untrustworthy: keep findable but don't let it top the ranking on an
        # undefendable velocity.
        composite *= GATE_INSUFFICIENT_DAMP
    # NOTE: CLASS_MIXED_SIGNAL is intentionally NOT damped anymore. It carries the
    # rescued trustworthy-but-non-linear movers (the accelerating/curving
    # pre-collapse case); the old ×0.7 buried exactly the buildings we must surface.

    # Clearance damp (BEFORE the protect floor, on purpose). A "certified safe" flag
    # may reduce the score, but only weakly and only while it stays credible: the
    # reduction shrinks with the clearance's age (fully gone by CLEAR_DECAY_DAYS) and
    # is cancelled as the building's own movement M rises (CLEAR_MOVE_LO..HI). Both
    # gates are independent, so a stale clearance OR a moving building defeats it.
    # Placed before the protect floor so a PLASTIC / hard-accel mover is re-floored
    # below — a clearance can never silence a building that is visibly failing.
    if structural_flag_state == STRUCT_CLEARED:
        age = 0.0 if not math.isfinite(flag_age_days) else max(0.0, flag_age_days)
        decay = _clip01(age / CLEAR_DECAY_DAYS)            # 0 fresh → 1 fully decayed
        move_span = CLEAR_MOVE_HI - CLEAR_MOVE_LO
        move_override = _clip01((M - CLEAR_MOVE_LO) / move_span) if move_span > 0 else 0.0
        clear_room = CLEAR_MAX_DAMP * (1.0 - decay) * (1.0 - move_override)
        composite = composite * (1.0 - clear_room)

    # Protect floor: a credible progressive failure (PLASTIC) or hard acceleration
    # can never be gated below COLLAPSE_PROTECT_FLOOR — no classification quirk may
    # demote a building that is visibly failing.
    if failure_mode == FAILURE_PLASTIC or (math.isfinite(accel) and accel <= DANGER_ACCEL_CRITICAL):
        composite = max(composite, COLLAPSE_PROTECT_FLOOR)

    # Structural-flag floor (AFTER the protect floor). An engineer/authority "unsafe"
    # judgement amplifies regardless of InSAR motion — InSAR cannot see construction
    # quality, so a condemned-but-stationary building must still score high. This is a
    # one-way ratchet up: it can never lower a score the motion-based path set higher.
    if structural_flag_state == STRUCT_AUTH_UNSAFE:
        composite = max(composite, STRUCT_AUTH_UNSAFE_FLOOR)
    elif structural_flag_state == STRUCT_UNSAFE:
        composite = max(composite, STRUCT_UNSAFE_FLOOR)

    return _clip01(composite)


def danger_level(
    *,
    vel: float,
    v_ew: float,
    accel: float,
    failure_mode: int,
    classification: int,
    structural_flag_state: int = STRUCT_NONE,
) -> int:
    """ABSOLUTE danger tier (STABLE…CRITICAL) from raw movement, with fixed mm/yr
    cutoffs so it is COMPARABLE ACROSS AOIs and robust to retuning the continuous
    collapse score. Single source of truth for the frontend threat badge.

    Bias is SENSITIVE (favor catching collapse): PLASTIC, hard acceleration, or
    severe subsidence all force CRITICAL; the lower tiers trip at modest motion.

    NaN-honest: `abs(NaN)` fails every `|v_ew|` comparison, so a 1-look building
    (drift unmeasured) is judged on vertical + acceleration + PLASTIC alone — it is
    never DOWN-ranked for the drift we couldn't measure. NaN `accel` skips the
    acceleration branches (isfinite-guarded).

    STRUCTURAL FLAG: an engineer/authority "unsafe" judgement ESCALATES the absolute
    tier (AUTH_UNSAFE → CRITICAL, UNSAFE → at least HIGH), applied last so it overrides
    even the INSUFFICIENT_EVIDENCE cap. A CLEARED flag NEVER lowers the tier — the
    absolute badge must stay un-silenceable (clearance only softens the continuous
    collapse score, never the categorical threat tier). Default STRUCT_NONE ⇒ no-op.
    """
    a_ew = abs(v_ew)  # NaN propagates → every `a_ew >= x` is False
    if (failure_mode == FAILURE_PLASTIC
            or (math.isfinite(accel) and accel <= DANGER_ACCEL_CRITICAL)
            or vel <= DANGER_VEL_CRITICAL):
        level = DANGER_CRITICAL
    elif (vel <= DANGER_VEL_HIGH or a_ew >= DANGER_VEW_HIGH
          or (math.isfinite(accel) and accel <= DANGER_ACCEL_HIGH)):
        level = DANGER_HIGH
    elif vel <= DANGER_VEL_ELEVATED or a_ew >= DANGER_VEW_ELEVATED:
        level = DANGER_ELEVATED
    elif vel <= DANGER_VEL_LOW or a_ew >= DANGER_VEW_LOW:
        level = DANGER_LOW
    else:
        level = DANGER_STABLE
    # σ-untrustworthy signal cannot claim the top tiers unless it's a PLASTIC
    # failure (a well-fit trend survives the σ gate by construction).
    if classification == CLASS_INSUFFICIENT_EVIDENCE and failure_mode != FAILURE_PLASTIC:
        level = min(level, DANGER_ELEVATED)
    # External structural condemnation escalates last — it must override even the
    # σ cap above (an engineer on the ground outranks our coherence gate). CLEARED
    # is intentionally absent here: a clearance can soften the score but never the tier.
    if structural_flag_state == STRUCT_AUTH_UNSAFE:
        level = DANGER_CRITICAL
    elif structural_flag_state == STRUCT_UNSAFE:
        level = max(level, DANGER_HIGH)
    return level


# ============================================================================
# STL trend decoupling
# ============================================================================

def _stl_decompose_chunk(
    displacement: np.ndarray,
    period: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """STL over a contiguous block of buildings. Pure function of its inputs —
    this is the unit of work for both the serial loop and the parallel map, so
    the two paths run *identical* arithmetic (bit-for-bit equal outputs).

    Module-level (not a closure) so a ProcessPoolExecutor can pickle it; takes
    only an ndarray slice + an int so each worker pickles one array, not n.
    """
    n, m = displacement.shape
    trend         = np.zeros((n, m), dtype=np.float32)
    trend_slope   = np.zeros(n,      dtype=np.float32)
    seasonal_amp  = np.zeros(n,      dtype=np.float32)
    trend_r2      = np.zeros(n,      dtype=np.float32)
    failure_mode  = np.zeros(n,      dtype=np.uint8)

    xs = np.arange(m, dtype=np.float64)
    x_mean = xs.mean()
    x_var = ((xs - x_mean) ** 2).sum()

    for i in range(n):
        y = displacement[i].astype(np.float64)
        try:
            res = STL(y, period=period, robust=True).fit()
        except Exception:
            continue
        t = res.trend
        s = res.seasonal
        r = res.resid
        trend[i, :] = t.astype(np.float32)
        y_mean_t = t.mean()
        slope_per_month = ((t - y_mean_t) * (xs - x_mean)).sum() / x_var if x_var > 0 else 0.0
        trend_slope[i] = float(slope_per_month * period)
        seasonal_amp[i] = float(s.max() - s.min())
        y_var = float(y.var())
        trend_r2[i] = float(1.0 - r.var() / y_var) if y_var > 1e-9 else 0.0
        if trend_slope[i] < -5.0 and trend_r2[i] > 0.85:
            failure_mode[i] = FAILURE_PLASTIC
        else:
            failure_mode[i] = FAILURE_ELASTIC

    return trend, trend_slope, seasonal_amp, trend_r2, failure_mode


# Parallelism is opt-out via STL_WORKERS (0/1 = serial). statsmodels' STL holds
# the GIL, so threads make this *slower* — we use processes. Per-building STL is
# independent, so splitting the building axis into one contiguous chunk per
# worker is exact: chunks are reassembled in index order, so row i is identical
# to the serial result. Chunking (not per-building tasks) keeps statsmodels
# imported once per worker and pickles one slice per worker, not n arrays.
def _stl_worker_count(n: int) -> int:
    import os
    env = os.environ.get("STL_WORKERS")
    if env is not None:
        try:
            requested = int(env)
        except ValueError:
            requested = 0
        if requested <= 1:
            return 1
        cap = requested
    else:
        cap = (os.cpu_count() or 1)
    # Process spin-up + statsmodels re-import costs ~tens of ms per worker; below
    # this many buildings the serial loop wins. (Measured floor on this dataset.)
    if n < 300:
        return 1
    return max(1, min(cap, 8, n))


def _stl_decompose(
    displacement: np.ndarray,
    period: int = 12,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Per-building STL decomposition of the cumulative-displacement series.

    Returns:
        trend          (n_buildings, n_months) — robust LOESS trend component
        trend_slope    (n_buildings,)         — annualized slope of the trend, mm/yr
        seasonal_amp   (n_buildings,)         — peak-to-peak seasonal amplitude, mm
        trend_r2       (n_buildings,)         — 1 - var(resid)/var(displacement)
        failure_mode   (n_buildings,)         — uint8 ELASTIC/PLASTIC per row

    STL needs at least 2 full periods (n_months ≥ 2 × period). With period=12
    and n_months=24 this is the bare statistical floor; confidence intervals on
    trend slope are wide, and the UI is responsible for surfacing that caveat.

    This is the dominant build-time CPU cost (≈99% of the join's scoring time:
    one robust LOESS fit per building). The fits are independent, so we fan the
    building axis across processes when it pays off. The result is bit-identical
    to the serial path — same `_stl_decompose_chunk` arithmetic, reassembled in
    index order. Set STL_WORKERS=1 to force serial (tests, debugging).
    """
    n, m = displacement.shape
    workers = _stl_worker_count(n)
    if workers <= 1:
        return _stl_decompose_chunk(displacement, period)

    # np.array_split keeps chunks contiguous and in order; concatenating the
    # per-chunk outputs back along axis 0 restores the original row order, so
    # output[i] ↔ building i exactly as in the serial loop.
    chunks = [c for c in np.array_split(displacement, workers) if c.shape[0] > 0]
    try:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(_stl_decompose_chunk, chunks, [period] * len(chunks)))
    except Exception:
        # Any pool/pickle/platform failure → exact serial fallback. Correctness
        # never depends on multiprocessing being available.
        return _stl_decompose_chunk(displacement, period)

    trend        = np.concatenate([r[0] for r in results], axis=0)
    trend_slope  = np.concatenate([r[1] for r in results])
    seasonal_amp = np.concatenate([r[2] for r in results])
    trend_r2     = np.concatenate([r[3] for r in results])
    failure_mode = np.concatenate([r[4] for r in results])
    return trend, trend_slope, seasonal_amp, trend_r2, failure_mode


# ============================================================================
# Velocity uncertainty propagation
# ============================================================================

# ============================================================================
# ARCHITECTURE_THREE B1/B2/B3 — extractors for MintPy diagnostic HDF5s
# ============================================================================
#
# Every extractor here is pure-numpy, vectorised, and tolerant of missing
# inputs. The MintPy run dir may not contain closurePhase.h5 / demErr.h5 /
# coherence_series.h5 on every config (e.g. when an older MintPy version
# names them differently, or the step was disabled). When the source is
# absent we return shape-matched NaN/zero arrays so the join step still
# ships — the UI will badge those columns as "not available" rather than
# the pipeline failing.

# DEM-residual threshold above which we flag a building "DEM-uncertain". Set
# to 15 m per ARCHITECTURE_THREE B3 — the typical magnitude at which the 30 m
# SRTM error meaningfully bleeds into apparent velocity at our 80 m looks.
DEM_ERR_FLAG_M: float = 15.0


def _h5_first_existing(path_candidates: Iterable, dataset_candidates: Iterable[str]):
    """Open the first HDF5 file in `path_candidates` that exists, returning
    (h5py.File, dataset_name) for the first dataset that exists inside it.
    Caller owns the file handle.

    Returns (None, None) when nothing matches — extractors then return
    safe NaN arrays.
    """
    import h5py
    for p in path_candidates:
        if p is None or not p.exists():
            continue
        try:
            f = h5py.File(p, "r")
        except Exception:
            continue
        for ds in dataset_candidates:
            if ds in f:
                return f, ds
        f.close()
    return None, None


def extract_closure_rms(run_dir, shape: tuple[int, int]) -> np.ndarray:
    """B1 — per-pixel closure-phase RMS (rad), shape (H, W).

    MintPy's `closure_phase_bias.py` writes `closurePhase.h5` with a
    `closurePhase` dataset (per-triplet residual stack) or, in newer
    versions, a precomputed `closurePhaseRMS` 2-D layer. We prefer the
    precomputed layer when present; otherwise we collapse the triplet
    stack to RMS along the triplet axis ourselves (vectorised, no Python
    loops).

    Returns float32, NaN where unavailable.
    """
    from pathlib import Path
    rd = Path(run_dir)
    candidates = [
        rd / "geo" / "geo_closurePhase.h5",
        rd / "closurePhase.h5",
        rd / "inputs" / "closurePhase.h5",
    ]
    f, ds = _h5_first_existing(candidates, ("closurePhaseRMS", "closurePhase"))
    if f is None:
        return np.full(shape, np.nan, dtype=np.float32)
    try:
        arr = np.asarray(f[ds], dtype=np.float32)
    finally:
        f.close()
    if arr.ndim == 2:
        rms = arr
    elif arr.ndim == 3:
        # (n_triplets, H, W) → RMS along axis 0. nanmean handles missing triplets.
        rms = np.sqrt(np.nanmean(arr * arr, axis=0)).astype(np.float32)
    else:
        return np.full(shape, np.nan, dtype=np.float32)
    if rms.shape != shape:
        # Shape mismatch (e.g. closurePhase in radar coords while velocity in
        # geo). Refuse to silently misalign — return NaN, log nothing here
        # (the join logs the column as missing).
        return np.full(shape, np.nan, dtype=np.float32)
    return rms


def extract_dem_err(run_dir, shape: tuple[int, int]) -> np.ndarray:
    """B3 — per-pixel DEM residual (m), shape (H, W). NaN where unavailable.

    MintPy's `correct_topography` step writes `demErr.h5` with a single
    `dem_error` dataset (some versions: `demError`). Sign convention is
    `actual - reference DEM` in metres.
    """
    from pathlib import Path
    rd = Path(run_dir)
    candidates = [
        rd / "geo" / "geo_demErr.h5",
        rd / "demErr.h5",
    ]
    f, ds = _h5_first_existing(candidates, ("dem_error", "demError"))
    if f is None:
        return np.full(shape, np.nan, dtype=np.float32)
    try:
        arr = np.asarray(f[ds], dtype=np.float32)
    finally:
        f.close()
    if arr.shape != shape:
        return np.full(shape, np.nan, dtype=np.float32)
    return arr


def extract_coh_per_epoch(run_dir, shape: tuple[int, int, int]) -> np.ndarray:
    """B2 — per-epoch spatial coherence stack, shape (T, H, W).

    MintPy's `temporalCoherence.h5` is a single 2-D map (post-inversion fit
    quality). The actual per-epoch coherence lives in the pre-inversion
    interferogram stack as `ifgramStack.h5::coherence` (shape
    (n_pairs, H, W)). To turn that into a per-epoch series we average the
    coherence of every interferogram that *touches* a given acquisition
    date. That's the standard per-epoch coherence definition used in
    InSAR review papers.

    Returns float32, NaN where unavailable.

    Cost: one h5py read + one numpy mean per epoch — O(T × n_pairs / T)
    in practice, no Python loop over pixels.
    """
    import h5py
    from pathlib import Path

    rd = Path(run_dir)
    T, H, W = shape
    # ifgramStack.h5 is always in radar coords pre-inversion; if MintPy
    # geocoded later, we still want the pre-inversion coherence here.
    candidates = [
        rd / "inputs" / "ifgramStack.h5",
        rd / "ifgramStack.h5",
    ]
    src = next((p for p in candidates if p.exists()), None)
    if src is None:
        return np.full(shape, np.nan, dtype=np.float32)

    try:
        f = h5py.File(src, "r")
    except Exception:
        return np.full(shape, np.nan, dtype=np.float32)
    try:
        if "coherence" not in f or "date" not in f:
            return np.full(shape, np.nan, dtype=np.float32)
        # date is (n_pairs, 2) bytes: each row [ref_date, sec_date] as YYYYMMDD.
        pair_dates = np.asarray(f["date"])  # bytes or str
        coh_stack = f["coherence"]  # h5py dataset; we'll slice per-pair below
        h_pre, w_pre = coh_stack.shape[1], coh_stack.shape[2]
        if (h_pre, w_pre) != (H, W):
            # Shape mismatch — pre/post-geocode dims differ. Refuse.
            return np.full(shape, np.nan, dtype=np.float32)
        # Decode pair_dates to ISO strings → match against per-epoch dates.
        def _to_iso(b) -> str:
            s = b.decode() if isinstance(b, (bytes, bytearray)) else str(b)
            return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else s
        # Build (n_pairs, 2) of ISO strings, vectorised.
        ref = np.array([_to_iso(b) for b in pair_dates[:, 0]])
        sec = np.array([_to_iso(b) for b in pair_dates[:, 1]])
        # We need the caller's epoch ordering — pull it from the timeseries
        # dates. Read the matching `date` dataset from timeseries.h5 next
        # door, in the same conventions.
        ts_path = (rd / "geo" / "geo_timeseries.h5" if (rd / "geo" / "geo_timeseries.h5").exists()
                   else rd / "timeseries.h5")
        if not ts_path.exists():
            return np.full(shape, np.nan, dtype=np.float32)
        with h5py.File(ts_path, "r") as fts:
            ts_dates_raw = np.asarray(fts["date"])
        ts_dates = np.array([_to_iso(b) for b in ts_dates_raw])
        if ts_dates.size != T:
            return np.full(shape, np.nan, dtype=np.float32)
        # For each epoch t, mask of pairs that touch it.
        out = np.full((T, H, W), np.nan, dtype=np.float32)
        # Slurp the coherence stack in one read (float32; for our 2 km AOIs
        # n_pairs × H × W is well under 200 MB).
        coh_arr = np.asarray(coh_stack, dtype=np.float32)
        for t in range(T):
            d = ts_dates[t]
            mask = (ref == d) | (sec == d)
            if not mask.any():
                continue
            out[t] = np.nanmean(coh_arr[mask], axis=0)
        return out
    finally:
        f.close()


def pack_coh_series_per_building(
    coh_per_epoch_stack: np.ndarray,  # (T, H, W) float32
    pixel_rows: np.ndarray,           # (N,) int64
    pixel_cols: np.ndarray,           # (N,) int64
) -> tuple[np.ndarray, int]:
    """Gather (T,) per-epoch coherence at each building's pixel, pack into a
    flat float32 stream, return (binary_blobs, n_epochs).

    `binary_blobs` is an object array of length N where each element is a
    `bytes` of length 4 × T — exactly what the parquet `binary` column wants.
    The frontend reads `n_epochs` from the bundle header and slices each
    blob as a Float32Array.

    Vectorised gather over the (T,H,W) stack; no Python per-building loop
    over the time axis.
    """
    if coh_per_epoch_stack.size == 0 or pixel_rows.size == 0:
        return np.empty(0, dtype=object), int(coh_per_epoch_stack.shape[0]) if coh_per_epoch_stack.ndim == 3 else 0
    T = int(coh_per_epoch_stack.shape[0])
    # gather → shape (T, N), then transpose → (N, T)
    series = coh_per_epoch_stack[:, pixel_rows, pixel_cols].astype(np.float32, copy=False).T
    # All-NaN rows can come from dead pixels — keep them; the frontend
    # already knows to hide series for buildings with classification=0.
    contig = np.ascontiguousarray(series, dtype=np.float32)
    # Slice per row → bytes. `tobytes()` per row is the cheapest path in
    # numpy; no further packing helpers needed.
    blobs = np.empty(series.shape[0], dtype=object)
    row_nbytes = T * 4
    raw = contig.tobytes()
    for i in range(series.shape[0]):
        blobs[i] = raw[i * row_nbytes:(i + 1) * row_nbytes]
    return blobs, T


def _velocity_sigma_from_coherence(gamma: np.ndarray, k: float = 5.0) -> np.ndarray:
    """Per-building σ on the velocity estimate, derived from end-of-series
    coherence.

    Empirical model: σ ≈ k * (1 - γ), calibrated so γ=0.9 → σ≈0.5 mm/yr
    (clean InSAR pixels) and γ=0.3 → σ≈3.5 mm/yr (decorrelated rooftops).
    """
    return np.clip(k * (1.0 - gamma), 0.05, 10.0).astype(np.float32)


# ============================================================================
# Court-defensibility gate (Tier-1 integrity boundary)
# ============================================================================

# R² floor is an ABSOLUTE constant: linear-fit quality is stack-independent, and
# 0.7 is the most court-defensible cut (keeps ~16% of real Huruma pixels — the
# only ones whose trend genuinely dominates the ~5 mm atmospheric noise floor).
DEFENSIBLE_R2_FLOOR: float = 0.7
# σ cap is DATA-DERIVED per-AOI: the noise floor differs per stack (1.2-1.9 mm/yr
# on the current Huruma run), so a hardcoded cap like 1.0 would gate everything.
DEFENSIBLE_SIGMA_PERCENTILE: float = 75.0


def defensibility_thresholds(
    v_sigma: np.ndarray,
    *,
    r2_floor: float = DEFENSIBLE_R2_FLOOR,
    sigma_pct: float = DEFENSIBLE_SIGMA_PERCENTILE,
) -> tuple[float, float]:
    """Return ``(r2_min, sigma_max)`` for this AOI's stack.

    ``r2_min`` is the absolute floor (fit quality is stack-independent).
    ``sigma_max`` is derived from THIS AOI's own σ distribution (its p75), so the
    gate adapts to the stack's noise floor instead of a constant that would zero
    everything. NaN-safe; falls back to ``+inf`` (σ-gate disabled) when no finite
    σ exists.
    """
    finite = np.isfinite(v_sigma)
    sigma_max = float(np.percentile(v_sigma[finite], sigma_pct)) if finite.any() else float("inf")
    return r2_floor, sigma_max


# ============================================================================
# Cohort percentile context
# ============================================================================

def _avg_rank_pct(vals: np.ndarray) -> np.ndarray:
    """Tie-aware average-rank percentile (0..100) of a 1-D array.

    Equal values share the mean of the ranks they span — so a cluster of
    identical scores all land on the same percentile rather than being split
    arbitrarily by sort order. Single-element input → [50].
    """
    k = vals.size
    if k == 1:
        return np.array([50], dtype=np.uint8)
    order = np.argsort(vals, kind="stable")
    ranked = vals[order]
    ranks = np.empty(k, dtype=np.float64)
    i0 = 0
    while i0 < k:
        i1 = i0 + 1
        while i1 < k and ranked[i1] == ranked[i0]:
            i1 += 1
        avg_rank = (i0 + i1 - 1) / 2.0
        ranks[order[i0:i1]] = avg_rank
        i0 = i1
    return (ranks / (k - 1) * 100.0).round().astype(np.uint8)


def _rank_within_groups(
    values: np.ndarray,
    group_id: np.ndarray,
    n_groups: int,
) -> np.ndarray:
    """Per-element tie-aware percentile rank (0..100, uint8) computed *within*
    each integer group. Singleton groups → 50. Vectorized per group; the total
    work is O(n log n) across all groups.

    `group_id` is an int array in [0, n_groups). Empty groups are skipped.
    """
    n = values.size
    out = np.full(n, 50, dtype=np.uint8)
    # Bucket member indices by group in one pass.
    members: list[list[int]] = [[] for _ in range(n_groups)]
    gid = group_id.astype(np.int64, copy=False)
    for i in range(n):
        members[gid[i]].append(i)
    for grp in members:
        if len(grp) <= 1:
            continue  # singleton/empty already 50
        idx = np.asarray(grp, dtype=np.int64)
        out[idx] = _avg_rank_pct(values[idx])
    return out


def _cohort_percentiles(
    composite: np.ndarray,
    shear_abs: np.ndarray,
    fused_h_m: np.ndarray,
    soil_class: list[str],
    band_width_m: float = 5.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-building percentile rank (0..100) of composite_risk and |v_ew|,
    computed within `height_band × soil_class` cohorts.

    Returns (composite_pct, shear_pct, cohort_size) — all uint8 except size,
    which is uint16 because a cohort can exceed 255 in dense AOIs.

    Singleton cohorts get percentile 50 (neither extreme justified with n=1).

    `shear_abs` may contain NaN for buildings whose drift was never measured
    (1-look, no descending pass). Those rank at the neutral 50 — an unmeasured
    building is neither a top nor a bottom drifter — and are excluded from the
    ranking of the buildings that DO have a real |v_ew|, so NaN can't distort it.
    """
    n = len(composite)
    composite_pct = np.zeros(n, dtype=np.uint8)
    shear_pct     = np.zeros(n, dtype=np.uint8)
    cohort_size   = np.zeros(n, dtype=np.uint16)

    band = np.floor(np.clip(fused_h_m, 0.0, None) / band_width_m).astype(np.int32)
    soil_arr = np.asarray(soil_class, dtype=object)
    keys: dict[tuple[int, str], list[int]] = {}
    for i in range(n):
        keys.setdefault((int(band[i]), soil_arr[i]), []).append(i)

    for members in keys.values():
        idx = np.asarray(members, dtype=np.int64)
        if idx.size == 1:
            composite_pct[idx[0]] = 50
            shear_pct[idx[0]]     = 50
            cohort_size[idx[0]]   = 1
            continue
        composite_pct[idx] = _avg_rank_pct(composite[idx])
        # Drift ranking excludes unmeasured (NaN) buildings: rank only the finite
        # |v_ew| against each other; NaN rows stay at the neutral 50 default.
        sh = shear_abs[idx]
        finite = np.isfinite(sh)
        if finite.sum() >= 2:
            shear_pct[idx[finite]] = _avg_rank_pct(sh[finite])
            shear_pct[idx[~finite]] = 50
        else:
            shear_pct[idx] = 50
        cohort_size[idx]   = idx.size

    return composite_pct, shear_pct, cohort_size


# ============================================================================
# ARCHITECTURE_THREE C1/C4 — fixed-grid block aggregation
# ============================================================================
#
# Blocks tile each AOI into ~`target_m` squares in *degree* space (the AOIs are
# 2 km on a side, so a simple equirectangular grid is exact enough — no
# projection needed). A block is identified by a flat id `iy*nx + ix`. This is a
# pure function of building centroids: no new dependency, deterministic, and the
# whole grid for an AOI fits in a few hundred cells, so aggregation is trivial.

# Sentinel block id for a centroid that somehow falls outside the bbox (clamped
# in practice, so this is defensive only).
BLOCK_ID_NONE = np.uint16(0xFFFF)


def _block_grid_meta(bbox: tuple[float, float, float, float], target_m: float = 170.0) -> dict:
    """Grid descriptor for an AOI bbox: cell size in degrees + column/row counts.

    `target_m` is the desired block edge in metres; we convert to degrees at the
    bbox centre latitude (lon degrees shrink with cos(lat)). nx/ny are chosen so
    the grid covers the bbox with at least one cell.
    """
    minlon, minlat, maxlon, maxlat = bbox
    mid_lat = (minlat + maxlat) / 2.0
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(mid_lat))
    cell_lat_deg = target_m / m_per_deg_lat
    cell_lon_deg = target_m / m_per_deg_lon
    nx = max(1, int(math.ceil((maxlon - minlon) / cell_lon_deg)))
    ny = max(1, int(math.ceil((maxlat - minlat) / cell_lat_deg)))
    return {
        "minlon": float(minlon), "minlat": float(minlat),
        "cell_lon_deg": float(cell_lon_deg), "cell_lat_deg": float(cell_lat_deg),
        "nx": int(nx), "ny": int(ny),
    }


def assign_blocks(
    c_lon: np.ndarray,
    c_lat: np.ndarray,
    bbox: tuple[float, float, float, float],
    target_m: float = 170.0,
) -> tuple[np.ndarray, dict]:
    """Assign each (lon,lat) centroid to a fixed-grid block.

    Returns (block_id uint16 [n], grid_meta). `block_id = iy*nx + ix`, with ix/iy
    clamped into [0,nx)/[0,ny) so edge points land in the boundary cell rather
    than overflowing. n_blocks = nx*ny is always < 65535 for these AOIs, so
    uint16 is safe.
    """
    meta = _block_grid_meta(bbox, target_m)
    nx, ny = meta["nx"], meta["ny"]
    ix = np.floor((c_lon - meta["minlon"]) / meta["cell_lon_deg"]).astype(np.int64)
    iy = np.floor((c_lat - meta["minlat"]) / meta["cell_lat_deg"]).astype(np.int64)
    np.clip(ix, 0, nx - 1, out=ix)
    np.clip(iy, 0, ny - 1, out=iy)
    block_id = (iy * nx + ix).astype(np.uint16)
    return block_id, meta


def aggregate_blocks(
    block_id: np.ndarray,
    n_blocks: int,
    vel_end: np.ndarray,
    composite: np.ndarray,
    classification: np.ndarray,
) -> dict[str, np.ndarray]:
    """Per-block rollups, indexed [0, n_blocks).

    Returns dict of dense per-block arrays:
      count            (int32)   buildings in the block
      worst_velocity   (float32) most-negative end velocity (mm/yr); 0 if empty
      mean_risk        (float32) mean composite_risk; 0 if empty
      max_risk         (float32) max composite_risk; 0 if empty
      confirmed        (int32)   # buildings classified CONFIRMED_THREAT

    All vectorized with np.add/minimum.at — O(n_buildings), no Python per-block
    loop.
    """
    gid = block_id.astype(np.int64, copy=False)
    count = np.zeros(n_blocks, dtype=np.int32)
    np.add.at(count, gid, 1)

    sum_risk = np.zeros(n_blocks, dtype=np.float64)
    np.add.at(sum_risk, gid, composite.astype(np.float64))
    with np.errstate(invalid="ignore", divide="ignore"):
        mean_risk = np.where(count > 0, sum_risk / np.maximum(count, 1), 0.0).astype(np.float32)

    max_risk = np.zeros(n_blocks, dtype=np.float32)
    np.maximum.at(max_risk, gid, composite.astype(np.float32))

    # Worst (most-negative) velocity. Seed with +inf so minimum.at picks the real
    # min, then replace untouched (+inf) cells with 0.
    worst = np.full(n_blocks, np.inf, dtype=np.float32)
    np.minimum.at(worst, gid, vel_end.astype(np.float32))
    worst[~np.isfinite(worst)] = 0.0

    confirmed = np.zeros(n_blocks, dtype=np.int32)
    is_conf = (classification == CLASS_CONFIRMED_THREAT).astype(np.int32)
    np.add.at(confirmed, gid, is_conf)

    return {
        "count": count,
        "worst_velocity": worst,
        "mean_risk": mean_risk,
        "max_risk": max_risk,
        "confirmed": confirmed,
    }


def tilt_rate_from_velocity_field(
    c_lon: np.ndarray,
    c_lat: np.ndarray,
    vel: np.ndarray,
    *,
    radius_m: float = 120.0,
    min_neighbours: int = 4,
) -> tuple[np.ndarray, np.ndarray]:
    """Per-building ANGULAR-DISTORTION rate from the vertical-velocity field.

    The collapse-relevant quantity is not how fast a building sinks but how fast
    it sinks *relative to its immediate neighbours* — the spatial gradient of
    vertical velocity, i.e. the rate of differential settlement / tilt. A whole
    block subsiding in lockstep is benign (rigid translation); a building sinking
    while its neighbour holds still is bending, and bending is what cracks frames
    (Skempton–MacDonald, Boscardin–Cording).

    For each building we fit a local plane vel ≈ a·x + b·y + c over all neighbours
    within `radius_m` (a local equirectangular metres frame centred on the
    building), by ordinary least squares. The fitted in-plane gradient magnitude
    ``|∇vel| = sqrt(a² + b²)`` is the tilt rate in (mm/yr)/m — direction-agnostic,
    so it captures tilt along any azimuth (unlike `v_ew`, which only sees E-W).

    Returns ``(tilt_rate, support)``:
      tilt_rate (float32, [n]) — |∇vel| in (mm/yr)/m; NaN where a building has its
        own NaN velocity or fewer than `min_neighbours` finite-velocity neighbours
        (an under-determined / unsupported fit must read as unknown, never 0 —
        composite_risk treats NaN tilt as "no tilt signal", not "no tilt").
      support (int32, [n]) — neighbour count used (incl. self); for diagnostics /
        a future confidence weight.

    Pure & deterministic: no rng, no global state. Complexity O(n log n + n·k̄): a
    cKDTree built once, then one bounded radius query + a fixed 3×3 normal-equations
    solve per building (k̄ = mean neighbours in `radius_m`). No O(n²) all-pairs scan.
    """
    n = len(vel)
    tilt = np.full(n, np.nan, dtype=np.float32)
    support = np.zeros(n, dtype=np.int32)
    if n == 0:
        return tilt, support

    lat0 = float(np.nanmean(c_lat)) if np.isfinite(c_lat).any() else 0.0
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(lat0))
    # Project to a local metres frame once (equirectangular — exact enough at AOI scale).
    x = (c_lon.astype(np.float64) - float(np.nanmin(c_lon))) * m_per_deg_lon
    y = (c_lat.astype(np.float64) - float(np.nanmin(c_lat))) * m_per_deg_lat
    v = vel.astype(np.float64)

    # Only finite-velocity, finite-position points can anchor OR support a fit.
    finite = np.isfinite(v) & np.isfinite(x) & np.isfinite(y)
    if not finite.any():
        return tilt, support
    idx = np.flatnonzero(finite)            # map compact tree-index → original row
    pts = np.column_stack((x[idx], y[idx]))
    from scipy.spatial import cKDTree
    tree = cKDTree(pts)
    # One vectorised radius query for every anchor: lists of neighbour tree-indices.
    neigh = tree.query_ball_point(pts, r=radius_m)

    for ti, oi in enumerate(idx):           # ti = tree index, oi = original row
        members = neigh[ti]                 # includes self; all already finite
        k = len(members)
        support[oi] = k
        if k < min_neighbours:
            continue
        rows = idx[members]
        dx = x[rows] - x[oi]
        dy = y[rows] - y[oi]
        vv = v[rows]
        # OLS plane vel ≈ a·dx + b·dy + c via the 3×3 normal equations AᵀA·coef = Aᵀv
        # (cheaper than lstsq and numerically fine for this tiny, well-scaled system).
        Sxx = float(dx @ dx); Sxy = float(dx @ dy); Sx = float(dx.sum())
        Syy = float(dy @ dy); Sy = float(dy.sum()); Sc = float(k)
        ATA = np.array([[Sxx, Sxy, Sx], [Sxy, Syy, Sy], [Sx, Sy, Sc]])
        ATv = np.array([float(dx @ vv), float(dy @ vv), float(vv.sum())])
        try:
            coef = np.linalg.solve(ATA, ATv)
        except np.linalg.LinAlgError:
            continue                        # degenerate geometry (collinear) → unknown
        a, b = float(coef[0]), float(coef[1])
        tilt[oi] = math.sqrt(a * a + b * b)
    return tilt, support


def _acceleration_mm_yr2(velocity_per_month: np.ndarray, lookback: int = 6) -> np.ndarray:
    """Annualized change in trailing-12mo velocity over the trailing `lookback`
    months. Shape: [n_buildings]. Sign: negative = accelerating subsidence.
    """
    n, m = velocity_per_month.shape
    if m <= lookback:
        return np.zeros(n, dtype=np.float32)
    v_now   = velocity_per_month[:, -1]
    v_prior = velocity_per_month[:, -lookback - 1]
    return ((v_now - v_prior) * (12.0 / lookback)).astype(np.float32)


def _trailing_velocity(cumulative: np.ndarray, window: int = 12) -> np.ndarray:
    """For each (building, month_t), linear slope of displacement over the trailing `window` months,
    annualized to mm/yr. Vectorized; O(n_buildings × n_months)."""
    n, m = cumulative.shape
    out = np.zeros_like(cumulative)
    for t in range(m):
        lo = max(0, t - window + 1)
        seg = cumulative[:, lo:t + 1]
        k = seg.shape[1]
        if k < 2:
            out[:, t] = 0.0
            continue
        xs = np.arange(k)
        x_mean = xs.mean()
        y_mean = seg.mean(axis=1)
        num = ((seg - y_mean[:, None]) * (xs - x_mean)).sum(axis=1)
        den = ((xs - x_mean) ** 2).sum()
        slope_per_month = num / den
        out[:, t] = slope_per_month * 12.0
    return out


# ============================================================================
# Environmental-context: REAL-data path
# ============================================================================
#
# Per-building soil class and shoreline/riparian distance are produced directly
# from real sources by `join_insar.build_real_env` (SoilGrids WRB + OSM geometry).
# There is no synthetic env generator here — the previous fabricated soil-band /
# shoreline-ramp / built-year code was removed (Ref_One: no synthetic data on air).


def synthesize_env_index_rows(
    *,
    aoi_code: str,
    building_ids: np.ndarray,
    soil_class: np.ndarray,
    riparian_dist_m: np.ndarray,
    shoreline_dist_m: np.ndarray,
    vel: np.ndarray,
    v_ew: np.ndarray,
    accel: np.ndarray,
    trend_slope: np.ndarray,
    failure_mode: np.ndarray,
    classification: np.ndarray,
    fused_h_m: np.ndarray,
    periods: list[date],
    v_ew_sigma: np.ndarray | None = None,
    tilt_rate: np.ndarray | None = None,
    structural_flag_state: np.ndarray | None = None,
    structural_flag_age_days: np.ndarray | None = None,
) -> tuple[pa.Table, np.ndarray, np.ndarray]:
    """Build the env_index parquet rows: one row per (building × quarter).

    Returns (table, composite_latest, danger_latest):
      - composite_latest: per-building collapse score (final period) — feeds
        `_cohort_percentiles` for within-AOI peer ranking.
      - danger_latest: per-building absolute danger tier (uint8) — written to the
        buildings table by the caller.

    Both are deterministic. The `groundwater_anom`/`rainfall_anom_mm`/`ndvi_proxy`
    columns are written NULL — no real per-quarter source is wired and nothing
    reads them, so we do not fabricate values.

    NaN honesty: `vel`/`v_ew`/`accel`/`trend_slope` are passed THROUGH to the
    scorers as NaN where unmeasured (do NOT coerce to 0 — that fabricates a
    reading and defeats the NaN-means-unknown contract inside composite_risk /
    danger_level). `fused_h_m` IS floored to 0 because it's only a load multiplier.
    """
    n = len(building_ids)
    rows: list[dict] = []
    composite_latest = np.zeros(n, dtype=np.float32)
    danger_latest    = np.zeros(n, dtype=np.uint8)

    for i in range(n):
        soil_i  = str(soil_class[i])
        ripa_i  = None if not np.isfinite(riparian_dist_m[i])  else float(riparian_dist_m[i])
        shor_i  = None if not np.isfinite(shoreline_dist_m[i]) else float(shoreline_dist_m[i])
        # Movement quantities flow through as-is (NaN preserved = "unmeasured").
        vel_i   = float(vel[i])
        vew_i   = float(v_ew[i])
        accel_i = float(accel[i])
        slope_i = float(trend_slope[i])
        fmode_i = int(failure_mode[i])
        cls_i   = int(classification[i])
        fh_i    = float(fused_h_m[i]) if np.isfinite(fused_h_m[i]) else 0.0
        # Confidence + differential-settlement inputs. NaN where the array isn't
        # supplied (synthetic path / older callers) ⇒ inert inside composite_risk.
        vews_i  = float("nan") if v_ew_sigma is None else float(v_ew_sigma[i])
        tilt_i  = float("nan") if tilt_rate  is None else float(tilt_rate[i])
        # External structural flag. None array (no Weespas flags / synthetic path) ⇒
        # STRUCT_NONE + NaN age ⇒ pure no-op inside both scorers (regression-safe).
        sfs_i   = STRUCT_NONE if structural_flag_state is None else int(structural_flag_state[i])
        sfa_i   = (float("nan") if structural_flag_age_days is None
                   else float(structural_flag_age_days[i]))
        # collapse score + absolute danger are deterministic and per-building (no
        # per-quarter env signal feeds them yet), so compute once per building.
        comp = composite_risk(
            soil_class=soil_i,
            riparian_dist_m=ripa_i,
            shoreline_dist_m=shor_i,
            vel=vel_i,
            v_ew=vew_i,
            accel=accel_i,
            trend_slope=slope_i,
            failure_mode=fmode_i,
            classification=cls_i,
            fused_h_m=fh_i,
            v_ew_sigma=vews_i,
            tilt_rate=tilt_i,
            structural_flag_state=sfs_i,
            flag_age_days=sfa_i,
        )
        dang = danger_level(
            vel=vel_i, v_ew=vew_i, accel=accel_i,
            failure_mode=fmode_i, classification=cls_i,
            structural_flag_state=sfs_i,
        )
        for q in periods:
            rows.append({
                "building_id":      int(building_ids[i]),
                "aoi_code":         aoi_code,
                "period_start":     q,
                # No real per-quarter groundwater/rainfall/NDVI source is wired into
                # the pipeline, and nothing reads these columns. Write NULL rather
                # than fabricate — keeps the schema complete with zero synthetic data.
                "groundwater_anom": None,
                "rainfall_anom_mm": None,
                "ndvi_proxy":       None,
                "composite_risk":   comp,
            })
        composite_latest[i] = comp
        danger_latest[i]    = dang

    return pa.Table.from_pylist(rows, schema=ENV_SCHEMA), composite_latest, danger_latest


# ============================================================================
# Arrow schemas — single source of truth shared by phenomena.py and join_insar.py
# ============================================================================

BUILDINGS_SCHEMA = pa.schema([
    ("building_id",            pa.int64()),
    ("aoi_code",               pa.string()),
    ("footprint_source",       pa.string()),
    ("osm_id",                 pa.int64()),
    ("open_buildings_id",      pa.string()),
    ("geom_wkb",               pa.binary()),
    ("centroid_lon",           pa.float64()),
    ("centroid_lat",           pa.float64()),
    ("height_m",               pa.float64()),
    ("insar_height_m",         pa.float64()),
    ("insar_height_sigma_m",   pa.float64()),
    ("fused_height_m",         pa.float64()),
    ("height_imputed",         pa.bool_()),     # True = height absent from source, estimated
    ("n_floors",               pa.int32()),
    ("insar_pixel_share",      pa.uint16()),    # # buildings sharing this building's 78 m InSAR cell
    ("soil_class",             pa.string()),
    ("riparian_dist_m",        pa.float64()),
    ("shoreline_dist_m",       pa.float64()),
    ("reclaimed_land",         pa.bool_()),
    ("built_year",             pa.int32()),
    # Tier 1: coherence-velocity classification + accel.
    ("classification",         pa.uint8()),
    ("velocity_accel_mm_yr2",  pa.float64()),
    # Tier 2: STL trend decomposition outputs.
    ("trend_slope_mm_yr",      pa.float64()),
    ("seasonal_amplitude_mm",  pa.float64()),
    ("trend_r2",               pa.float64()),
    ("failure_mode",           pa.uint8()),
    # Absolute danger tier (0=STABLE … 4=CRITICAL), comparable across AOIs. Single
    # source of truth for the frontend threat badge. See postprocess.danger_level.
    ("danger_level",           pa.uint8()),
    # Tier 3: velocity σ and cohort percentile context.
    ("velocity_sigma_mm_yr",   pa.float64()),
    ("velocity_ew_sigma_mm_yr", pa.float64()),
    ("cohort_composite_pct",   pa.uint8()),
    ("cohort_shear_pct",       pa.uint8()),
    ("cohort_size",            pa.uint16()),
    # ARCHITECTURE_THREE C1/C4 — fixed-grid block membership + block-relative
    # cohort percentile. block_id = iy*nx + ix (uint16; nx/ny from the AOI bbox,
    # see assign_blocks). cohort_block_pct = percentile rank of this building's
    # latest composite_risk *within its own block* (singletons → 50).
    ("block_id",               pa.uint16()),
    ("cohort_block_pct",       pa.uint8()),
    # ARCHITECTURE_THREE B1 — closure-phase RMS per pixel (rad). Surfaces as
    # "atmospheric noise: low/med/high" badge. High = residual tropo/decorr,
    # NOT geometry change.
    ("closure_rms_rad",        pa.float32()),
    # ARCHITECTURE_THREE B3 — DEM residual estimate (m) from MintPy's joint
    # velocity+DEM solve. |residual| > 15 m → flag this building "DEM-uncertain"
    # in the UI; the apparent velocity is partly DEM artefact, not deformation.
    ("dem_err_m",              pa.float32()),
    ("dem_err_flag",           pa.bool_()),
    # ASC+DESC decomposition provenance. "decomposed_2look" = this building's
    # pixel had a real ascending+descending east-west solution (drift measured);
    # "los_1look" = single orbit only, vertical-equivalent, drift unknown (v_ew
    # and velocity_ew_sigma_mm_yr are NULL). Lets the UI badge drift honestly.
    ("decomposition_mode",     pa.string()),
    # External structural-flag fusion (the second sensor — engineer/authority
    # judgement of construction quality, which InSAR cannot see). All NULL/0 on a
    # building with no flag ⇒ scoring is identical to the motion-only path.
    # state: 0=NONE/uninspected 1=CLEARED 2=UNSAFE 3=AUTH_UNSAFE (see STRUCT_*).
    ("structural_flag_state",       pa.uint8()),
    ("structural_flag_observed_at", pa.date32()),   # NULL if unflagged; age computed at build time
    ("structural_flag_source",      pa.string()),   # 'engineer' | 'authority' | NULL
])

# ARCHITECTURE_THREE B2 — coherence sparkline. One row per building, holding a
# fixed-length packed Float32 binary of `n_epochs` values. Stored as a
# `binary` column rather than `list<float32>` so the read path is a single
# zero-copy memcpy into a JS Float32Array. `n_epochs` is the count for the AOI;
# the frontend reads it from the bundle header and reshapes accordingly.
COH_SERIES_SCHEMA = pa.schema([
    ("building_id",  pa.int64()),
    ("aoi_code",     pa.string()),
    ("coh_series",   pa.binary()),   # raw little-endian Float32, length = 4 × n_epochs
])

SUBSIDENCE_SCHEMA = pa.schema([
    ("building_id",                  pa.int64()),
    ("aoi_code",                     pa.string()),
    ("observation_date",             pa.date32()),
    ("displacement_mm",              pa.float64()),
    ("trend_displacement_mm",        pa.float64()),
    ("velocity_mm_yr",               pa.float64()),
    ("velocity_horizontal_ew_mm_yr", pa.float64()),
    ("coherence",                    pa.float64()),
])

ENV_SCHEMA = pa.schema([
    ("building_id",      pa.int64()),
    ("aoi_code",         pa.string()),
    ("period_start",     pa.date32()),
    ("groundwater_anom", pa.float64()),
    ("rainfall_anom_mm", pa.float64()),
    ("ndvi_proxy",       pa.float64()),
    ("composite_risk",   pa.float64()),
])
