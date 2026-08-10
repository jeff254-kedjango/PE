"""
Unit tests for ASC + DESC LOS decomposition (scripts/decompose.py).

These are pure-function tests over synthetic geometry — they don't touch the DB
or any real InSAR products, so they always run (unlike the synthetic-cohort
invariants that skip once an AOI goes real-InSAR). They pin the math the drift
(v_ew) signal depends on:

  - the LOS forward model and decomposition are exact inverses;
  - pure-vertical motion produces ~zero east-west;
  - the orbit sign convention (ASC u_E<0, DESC u_E>0; u_U>0 both) — a flipped
    heading here would invert the reported drift direction, a life-safety bug;
  - the weighted-LS covariance is finite/symmetric and flags degenerate geometry;
  - co-registration onto an identical grid is a no-op.

Run from backend/:  pytest tests/test_decompose.py
"""

from __future__ import annotations

import numpy as np
import pytest

from scripts.decompose import (
    los_unit_vector,
    decompose_asc_desc,
    coregister_field,
)

# Real Nairobi path-57/79 geometry (degrees) and a representative S1 IW
# incidence angle in radians (~33°), as MintPy stores it.
HEAD_ASC = -12.0327
HEAD_DESC = 192.0348
INC = 0.58


def _forward(v_up, v_ew, inc, heading):
    """Project a (v_up, v_ew) ground motion onto one orbit's LOS (v_north=0)."""
    u_e, _u_n, u_u = los_unit_vector(np.array([inc]), heading)
    return u_u * v_up + u_e * v_ew


def test_los_sign_convention():
    """ASC looks east-down → eastward motion moves AWAY (u_E<0); DESC the mirror
    (u_E>0). Both see uplift as toward-satellite (u_U>0). This is the guard
    against a flipped heading silently inverting drift direction."""
    ue_a, _, uu_a = los_unit_vector(np.array([INC]), HEAD_ASC)
    ue_d, _, uu_d = los_unit_vector(np.array([INC]), HEAD_DESC)
    assert ue_a[0] < 0, f"ASC u_E should be negative, got {ue_a[0]}"
    assert ue_d[0] > 0, f"DESC u_E should be positive, got {ue_d[0]}"
    assert uu_a[0] > 0 and uu_d[0] > 0, "u_U must be positive (uplift→toward sat)"


def test_eastward_motion_los_signs():
    """+east drift (no vertical) decreases ASC LOS and increases DESC LOS."""
    los_a = _forward(0.0, 1.0, INC, HEAD_ASC)
    los_d = _forward(0.0, 1.0, INC, HEAD_DESC)
    assert los_a[0] < 0 and los_d[0] > 0


def test_exact_roundtrip():
    """Forward-project a known motion through real ASC/DESC geometry, invert,
    and recover it to machine precision (the system is exactly determined)."""
    v_up, v_ew = 5.0, -3.0
    los_a = _forward(v_up, v_ew, INC, HEAD_ASC)
    los_d = _forward(v_up, v_ew, INC, HEAD_DESC)
    sig = np.array([1.0])
    inc = np.array([INC])
    ru, re, su, se, ok = decompose_asc_desc(
        los_a, los_d, inc, inc, HEAD_ASC, HEAD_DESC, sig, sig
    )
    assert ok[0]
    assert ru[0] == pytest.approx(v_up, abs=1e-6)
    assert re[0] == pytest.approx(v_ew, abs=1e-6)
    assert np.isfinite(su[0]) and np.isfinite(se[0])


def test_pure_vertical_has_no_ew():
    """Pure subsidence/uplift must decompose to ~zero east-west."""
    los_a = _forward(-4.0, 0.0, INC, HEAD_ASC)
    los_d = _forward(-4.0, 0.0, INC, HEAD_DESC)
    sig = np.array([1.0])
    inc = np.array([INC])
    ru, re, *_ = decompose_asc_desc(
        los_a, los_d, inc, inc, HEAD_ASC, HEAD_DESC, sig, sig
    )
    assert ru[0] == pytest.approx(-4.0, abs=1e-6)
    assert abs(re[0]) < 1e-9


def test_vectorized_batch_roundtrip():
    """A batch of distinct (v_up, v_ew) pixels round-trips elementwise — proves
    the inversion is genuinely vectorized, not accidentally scalar."""
    rng = np.random.default_rng(0)
    n = 256
    v_up = rng.uniform(-20, 20, n)
    v_ew = rng.uniform(-10, 10, n)
    inc = np.full(n, INC)
    ue_a, _, uu_a = los_unit_vector(inc, HEAD_ASC)
    ue_d, _, uu_d = los_unit_vector(inc, HEAD_DESC)
    los_a = uu_a * v_up + ue_a * v_ew
    los_d = uu_d * v_up + ue_d * v_ew
    sig = np.ones(n)
    ru, re, _su, _se, ok = decompose_asc_desc(
        los_a, los_d, inc, inc, HEAD_ASC, HEAD_DESC, sig, sig
    )
    assert ok.all()
    np.testing.assert_allclose(ru, v_up, atol=1e-6)
    np.testing.assert_allclose(re, v_ew, atol=1e-6)


def test_nan_input_degrades():
    """A NaN in either orbit yields NaN out and ok_mask=False (→ caller falls
    back to vertical-only), never a fabricated number."""
    inc = np.array([INC, INC])
    sig = np.array([1.0, 1.0])
    los_a = np.array([1.0, np.nan])
    los_d = np.array([1.0, 1.0])
    ru, re, _su, _se, ok = decompose_asc_desc(
        los_a, los_d, inc, inc, HEAD_ASC, HEAD_DESC, sig, sig
    )
    assert ok[0] and not ok[1]
    assert np.isnan(ru[1]) and np.isnan(re[1])


def test_degenerate_geometry_flagged():
    """Two near-identical look geometries can't separate up from east-west; the
    inversion must flag ok=False rather than amplify noise."""
    inc = np.array([INC])
    sig = np.array([1.0])
    # Same heading for both 'orbits' → parallel LOS → singular system.
    ru, re, _su, _se, ok = decompose_asc_desc(
        np.array([1.0]), np.array([1.0]), inc, inc, HEAD_ASC, HEAD_ASC, sig, sig
    )
    assert not ok[0]
    assert np.isnan(ru[0]) and np.isnan(re[0])


def test_sigma_symmetry_and_weighting():
    """Equal per-orbit σ → finite, positive σ_up/σ_ew; a noisier descending pass
    inflates the covariance (monotone in input σ)."""
    inc = np.array([INC])
    los_a = _forward(2.0, 1.0, INC, HEAD_ASC)
    los_d = _forward(2.0, 1.0, INC, HEAD_DESC)
    _ru, _re, su1, se1, _ = decompose_asc_desc(
        los_a, los_d, inc, inc, HEAD_ASC, HEAD_DESC, np.array([1.0]), np.array([1.0])
    )
    _ru2, _re2, su2, se2, _ = decompose_asc_desc(
        los_a, los_d, inc, inc, HEAD_ASC, HEAD_DESC, np.array([1.0]), np.array([3.0])
    )
    assert su1[0] > 0 and se1[0] > 0
    assert su2[0] >= su1[0] and se2[0] >= se1[0]


def test_coregister_identity():
    """Resampling a field onto its own axes returns it unchanged."""
    f = np.arange(12.0).reshape(3, 4)
    xs = np.array([0.0, 1.0, 2.0, 3.0])
    ys = np.array([0.0, 1.0, 2.0])
    out = coregister_field(f, xs, ys, xs, ys)
    np.testing.assert_allclose(out, f, atol=1e-9)


def test_coregister_out_of_extent_is_nan():
    """Target points outside the source grid return NaN (no extrapolation)."""
    f = np.arange(9.0).reshape(3, 3)
    xs = np.array([0.0, 1.0, 2.0])
    ys = np.array([0.0, 1.0, 2.0])
    out = coregister_field(f, xs, ys, np.array([5.0, 1.0]), np.array([1.0]))
    assert np.isnan(out[0, 0]) and np.isfinite(out[0, 1])
