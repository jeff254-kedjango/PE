"""
ASC + DESC line-of-sight (LOS) decomposition into vertical + east-west velocity.

A single InSAR LOS velocity cannot separate vertical motion (subsidence/uplift)
from horizontal east-west drift — it's one projection of a 3-D motion vector.
Two looks from opposite orbit geometries (ascending + descending) can: their LOS
unit vectors differ enough in the east component to invert for both v_up and
v_ew. The north component is near-degenerate with vertical for Sentinel-1's
near-polar orbits (|cos φ| ≈ 0.2, same sign on both passes), so we solve the
standard 2-component (Up, EW) system and honestly drop the unobservable N-S
component. Un-modelled N-S motion leaks slightly into v_up; this is the accepted
limitation of 2-look InSAR decomposition.

Geometry. Incidence angle θ (radians, as MintPy stores it). Satellite heading α
(degrees from north, clockwise). Sentinel-1 is RIGHT-looking, so the horizontal
azimuth of the ground→satellite LOS is φ = α − 90°. With MintPy's sign
convention (positive LOS = motion TOWARD the satellite), the LOS unit vector in
(East, North, Up):

    u_E = sin θ · sin φ ,  u_N = sin θ · cos φ ,  u_U = cos θ

Sign invariants that make EW observable and guard against a flipped heading
(a life-safety-relevant error — it would invert the reported drift direction):
ASC u_E < 0, DESC u_E > 0; u_U > 0 on both passes. Enforced by tests.

Everything is vectorized over N pixels — no per-pixel Python in the hot path.
Pure numpy/scipy (the join runs in the plain venv, not the gdal/conda env).
"""

from __future__ import annotations

import numpy as np

# Below this |det| the two LOS geometries are too parallel to separate v_up from
# v_ew (degenerate inversion); such pixels fall back to vertical-only upstream.
_DET_EPS = 1e-3


def los_unit_vector(
    inc_rad: np.ndarray, heading_deg: float | np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """LOS unit vector (ground→satellite) in (East, North, Up), per pixel.

    `inc_rad` is the incidence angle in RADIANS (MintPy's native units).
    `heading_deg` is the satellite heading in degrees from north (clockwise);
    Sentinel-1 ASC ≈ −12°, DESC ≈ +192°. Returns (u_E, u_N, u_U), each the
    shape of `inc_rad`.
    """
    inc = np.asarray(inc_rad, dtype=np.float64)
    phi = np.radians(np.asarray(heading_deg, dtype=np.float64) - 90.0)
    sin_t = np.sin(inc)
    u_e = sin_t * np.sin(phi)
    u_n = sin_t * np.cos(phi)
    u_u = np.cos(inc)
    return u_e, u_n, u_u


def decompose_asc_desc(
    v_asc: np.ndarray,
    v_desc: np.ndarray,
    inc_asc: np.ndarray,
    inc_desc: np.ndarray,
    head_asc: float | np.ndarray,
    head_desc: float | np.ndarray,
    sig_asc: np.ndarray,
    sig_desc: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Invert co-registered ASC + DESC LOS velocities for (v_up, v_ew).

    All velocity/incidence/sigma args are arrays of identical shape (one element
    per ground pixel), already co-registered onto a shared grid. Headings may be
    scalars (per-orbit) or arrays. Returns:

        v_up, v_ew      mm/yr, NaN where the inversion is unusable
        sig_up, sig_ew  1-σ uncertainty (mm/yr) from the weighted-LS covariance
        ok_mask         bool, True where a real 2-look solution was produced

    The 2×2 system is exactly determined, so v_up/v_ew are independent of the
    weights; the per-orbit σ (σ ≈ k(1−γ)) enters only the covariance, via
    Cov = (AᵀWA)⁻¹ with W = diag(1/σ_asc², 1/σ_desc²). Degenerate geometry
    (|det| < _DET_EPS) or any NaN input yields NaN outputs and ok_mask=False, so
    the caller can fall back to vertical-only for those pixels.
    """
    v_asc = np.asarray(v_asc, dtype=np.float64)
    v_desc = np.asarray(v_desc, dtype=np.float64)

    # A = [[a, b], [c, d]] = [[u_U_asc, u_E_asc], [u_U_desc, u_E_desc]].
    b, _n_a, a = los_unit_vector(inc_asc, head_asc)   # b=u_E_asc, a=u_U_asc
    d, _n_d, c = los_unit_vector(inc_desc, head_desc)  # d=u_E_desc, c=u_U_desc

    det = a * d - b * c
    finite = (
        np.isfinite(v_asc) & np.isfinite(v_desc)
        & np.isfinite(a) & np.isfinite(b) & np.isfinite(c) & np.isfinite(d)
    )
    ok_mask = finite & (np.abs(det) >= _DET_EPS)

    # Safe det for the division; masked-off pixels are overwritten with NaN below.
    det_safe = np.where(ok_mask, det, 1.0)
    v_up = (d * v_asc - b * v_desc) / det_safe
    v_ew = (-c * v_asc + a * v_desc) / det_safe

    # Weighted-LS covariance. σ floored so a perfect-coherence pixel (σ→0) can't
    # divide by zero; M = AᵀWA, Cov = M⁻¹, σ_up=√Cov₀₀, σ_ew=√Cov₁₁.
    sa = np.maximum(np.asarray(sig_asc, dtype=np.float64), 1e-6)
    sd = np.maximum(np.asarray(sig_desc, dtype=np.float64), 1e-6)
    w1 = 1.0 / (sa * sa)
    w2 = 1.0 / (sd * sd)
    m00 = a * a * w1 + c * c * w2
    m11 = b * b * w1 + d * d * w2
    m01 = a * b * w1 + c * d * w2
    det_m = m00 * m11 - m01 * m01
    det_m_safe = np.where(ok_mask & (det_m > 0), det_m, 1.0)
    sig_up = np.sqrt(np.maximum(m11 / det_m_safe, 0.0))
    sig_ew = np.sqrt(np.maximum(m00 / det_m_safe, 0.0))

    nan = np.float64(np.nan)
    v_up = np.where(ok_mask, v_up, nan)
    v_ew = np.where(ok_mask, v_ew, nan)
    sig_up = np.where(ok_mask, sig_up, nan)
    sig_ew = np.where(ok_mask, sig_ew, nan)
    return v_up, v_ew, sig_up, sig_ew, ok_mask


def coregister_field(
    field: np.ndarray,
    src_xs: np.ndarray,
    src_ys: np.ndarray,
    dst_xs: np.ndarray,
    dst_ys: np.ndarray,
) -> np.ndarray:
    """Bilinearly resample a (H, W) field from its own grid onto a target grid.

    `src_xs`/`src_ys` are the source axes (monotonically increasing, matching
    `field`'s columns/rows); `dst_xs`/`dst_ys` the target axes. Points outside
    the source extent return NaN (honest "no data" rather than an extrapolation).
    Used to put the DESC velocity/coherence/incidence onto the ASC pixel grid
    before decomposition. Pure scipy — no gdal.
    """
    from scipy.interpolate import RegularGridInterpolator

    interp = RegularGridInterpolator(
        (np.asarray(src_ys, dtype=np.float64), np.asarray(src_xs, dtype=np.float64)),
        np.asarray(field, dtype=np.float64),
        method="linear",
        bounds_error=False,
        fill_value=np.nan,
    )
    gy, gx = np.meshgrid(
        np.asarray(dst_ys, dtype=np.float64),
        np.asarray(dst_xs, dtype=np.float64),
        indexing="ij",
    )
    pts = np.stack([gy.ravel(), gx.ravel()], axis=-1)
    return interp(pts).reshape(len(dst_ys), len(dst_xs))
