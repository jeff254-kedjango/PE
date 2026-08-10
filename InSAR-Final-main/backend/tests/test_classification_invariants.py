"""
Regression test for the synthetic risk engine. The framework doesn't mention
ground truth at all; in production this is the first question a structural
engineer client asks. Until we have real GNSS pins, the next best thing is
**named cohort invariants**: for each AOI, declare regions that the physics
in `phenomena.py` *must* produce a particular outcome on, and fail loudly if
the generator drifts.

We deliberately don't pin individual `building_id`s — those are sensitive to
RNG draw order, and a benign refactor of `_huruma_buildings` would break the
test for the wrong reason. We pin *cohort-level* invariants instead:

  - At least one CONFIRMED_THREAT building exists inside each declared
    Huruma hotspot (the physics adds -15 to -22 mm/yr there).
  - At least one STABLE_ANCHOR exists in the inland weathered-basalt cohort.
  - Mombasa's reclaimed-fill cohort drifts seaward (mean v_ew < 0).
  - Mombasa's reclaimed-fill cohort has a higher mean composite than the
    inland coral-rag cohort.
  - PLASTIC failure mode (Tier 2) is present in both AOIs.
  - Velocity σ (Tier 3) is monotonically larger for low-coherence buildings.

These invariants are properties of the *physics model*, not of the RNG seed,
so they survive seed changes; they fail only when the underlying behaviour
of `phenomena.py` shifts.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "demo.duckdb"
PROVENANCE_PATH = ROOT / "data" / "provenance.json"


def _provenance(aoi_code: str) -> str:
    """Data origin for an AOI: 'synthetic' | 'partial' | 'insar'.

    The invariants in this file assert properties of the *synthetic* risk
    engine (`phenomena.py`): engineered hotspots at fixed offsets, the
    σ = k(1-γ) model, the 0.2× ENV_NOISE gate. Once an AOI is joined to real
    InSAR (provenance 'insar') those synthetic signatures are gone — a real
    velocity field has no hotspot planted at (-300, 150) m — so the
    synthetic-only invariants no longer apply and must skip, not fail.
    Defaults to 'synthetic' when provenance is unrecorded (fresh seed).
    """
    if not PROVENANCE_PATH.exists():
        return "synthetic"
    return json.loads(PROVENANCE_PATH.read_text()).get(aoi_code, "synthetic")


def _require_synthetic(aoi_code: str) -> None:
    """Skip a synthetic-engine invariant for an AOI now backed by real InSAR."""
    prov = _provenance(aoi_code)
    if prov == "insar":
        pytest.skip(
            f"{aoi_code} provenance is '{prov}' (real InSAR); this asserts a "
            "synthetic-generator property that does not hold on measured data"
        )


@pytest.fixture(scope="module")
def con() -> duckdb.DuckDBPyConnection:
    if not DB_PATH.exists():
        pytest.skip(
            f"DuckDB not seeded at {DB_PATH}. "
            "Run `python -m scripts.seed_synthetic` from backend/ first.",
        )
    c = duckdb.connect(str(DB_PATH), read_only=True)
    c.execute("LOAD spatial;")
    yield c
    c.close()


# Mirrors phenomena.py CLASS_* constants — kept in sync manually.
CLASS_INDETERMINATE = 0
CLASS_CONFIRMED_THREAT = 1
CLASS_ENV_NOISE = 2
CLASS_STABLE_ANCHOR = 3
CLASS_MIXED_SIGNAL = 4
CLASS_INSUFFICIENT_EVIDENCE = 5

FAILURE_ELASTIC = 0
FAILURE_PLASTIC = 1


# ---------------------------------------------------------------------------
# Huruma — informal-settlement subsidence
# ---------------------------------------------------------------------------

# Hotspot centres + radii (m) as declared in `_huruma_velocity`. These ARE the
# physics, so if they drift the synthetic story drifts with them.
HURUMA_HOTSPOTS = [(-300, 150, 250), (400, -400, 180), (-600, -700, 300)]


def _huruma_local_xy(centroid_lon: float, centroid_lat: float) -> tuple[float, float]:
    """Re-project Huruma centroids back to the local-metre frame used by
    `phenomena._huruma_buildings`. The AOI centre is at (36.873, -1.255) per
    `aois.py`; conversion uses the same constants as `_meters_to_deg`."""
    import math
    aoi_lon, aoi_lat = 36.873, -1.255
    dlat_per_m = 1.0 / 111_320.0
    dlon_per_m = 1.0 / (111_320.0 * math.cos(math.radians(aoi_lat)))
    x = (centroid_lon - aoi_lon) / dlon_per_m
    y = (centroid_lat - aoi_lat) / dlat_per_m
    return x, y


def test_huruma_hotspots_produce_confirmed_threats(con):
    """Every hotspot in `_huruma_velocity` should classify ≥ 1 building as
    CONFIRMED_THREAT. If this fails, the velocity model is no longer crossing
    the -10 mm/yr threshold under high coherence — a regression we want to catch."""
    _require_synthetic("huruma")
    rows = con.execute(
        """
        SELECT centroid_lon, centroid_lat, classification
        FROM buildings WHERE aoi_code = 'huruma'
        """
    ).fetchall()
    assert rows, "Huruma has no buildings"

    for hx, hy, r in HURUMA_HOTSPOTS:
        n_threats = 0
        for lon, lat, cls in rows:
            x, y = _huruma_local_xy(lon, lat)
            if (x - hx) ** 2 + (y - hy) ** 2 <= r ** 2 and cls == CLASS_CONFIRMED_THREAT:
                n_threats += 1
        assert n_threats >= 1, (
            f"hotspot at ({hx},{hy}) r={r} produced 0 CONFIRMED_THREAT buildings. "
            "Has _huruma_velocity been weakened, or _classify thresholds raised?"
        )


def test_huruma_stable_anchors_exist_on_basalt(con):
    """Inland weathered-basalt cohort is the stable-reference zone: bias is
    -0.5 mm/yr, coherence is fine, so we should see ≥ 5 STABLE_ANCHORs."""
    _require_synthetic("huruma")
    (n,) = con.execute(
        """
        SELECT COUNT(*) FROM buildings
        WHERE aoi_code = 'huruma'
          AND soil_class = 'weathered_basalt'
          AND classification = ?
        """,
        [CLASS_STABLE_ANCHOR],
    ).fetchone()
    assert n >= 5, f"only {n} STABLE_ANCHORs on huruma weathered_basalt (expected ≥5)"


def test_huruma_env_noise_buildings_have_dampened_composite(con):
    """ENV_NOISE buildings should have their composite_risk multiplied by 0.2
    relative to what their raw signal would otherwise produce. A direct way
    to assert this: ENV_NOISE composite-rank-mean must be below the AOI mean."""
    _require_synthetic("huruma")
    mean_noise, mean_all = con.execute(
        """
        SELECT
          (SELECT AVG(e.composite_risk) FROM buildings b
            JOIN environmental_index e USING (building_id, aoi_code)
            WHERE b.aoi_code='huruma' AND b.classification = ?),
          (SELECT AVG(composite_risk) FROM environmental_index WHERE aoi_code='huruma')
        """,
        [CLASS_ENV_NOISE],
    ).fetchone()
    assert mean_noise is not None, "no ENV_NOISE buildings on huruma (expected some)"
    assert mean_noise < mean_all, (
        f"ENV_NOISE composite mean {mean_noise:.3f} not below AOI mean {mean_all:.3f}; "
        "is the 0.2x gate in _composite_risk still wired up?"
    )


# ---------------------------------------------------------------------------
# Mombasa — coastal subsidence
# ---------------------------------------------------------------------------


def test_mombasa_reclaim_fill_drifts_seaward(con):
    """Reclaimed-fill cohort should have mean horizontal velocity < 0
    (seaward = westward = negative). Driven by `_mombasa_ew_bias`."""
    _require_synthetic("mombasa")
    (mean_ew,) = con.execute(
        """
        SELECT AVG(s.velocity_horizontal_ew_mm_yr)
        FROM buildings b
        JOIN subsidence_time_series s USING (building_id, aoi_code)
        WHERE b.aoi_code='mombasa' AND b.reclaimed_land
          AND s.observation_date = (SELECT MAX(observation_date) FROM subsidence_time_series WHERE aoi_code='mombasa')
        """
    ).fetchone()
    assert mean_ew is not None, "no reclaimed_land buildings on mombasa"
    assert mean_ew < -1.0, (
        f"reclaim_fill mean v_ew = {mean_ew:.2f} mm/yr; expected strongly negative (seaward)"
    )


def test_mombasa_reclaim_composite_higher_than_inland(con):
    """Load-weighted soil + creep + seaward drift should push reclaim cohort
    composite well above inland coral-rag. This is the 'demo story'."""
    rec, inland = con.execute(
        """
        SELECT
          (SELECT AVG(e.composite_risk) FROM buildings b
            JOIN environmental_index e USING (building_id, aoi_code)
            WHERE b.aoi_code='mombasa' AND b.reclaimed_land),
          (SELECT AVG(e.composite_risk) FROM buildings b
            JOIN environmental_index e USING (building_id, aoi_code)
            WHERE b.aoi_code='mombasa' AND NOT b.reclaimed_land AND b.soil_class='coral_rag')
        """
    ).fetchone()
    assert rec is not None and inland is not None
    assert rec > inland + 0.05, (
        f"reclaim composite mean {rec:.3f} should exceed inland coral_rag {inland:.3f}"
        " by at least 0.05; load_factor or seaward-drift contribution looks weak"
    )


# ---------------------------------------------------------------------------
# Tier 2 — STL decomposition must produce both failure modes
# ---------------------------------------------------------------------------


def test_both_aois_produce_plastic_failures(con):
    """If every building is ELASTIC, the STL threshold is too tight (or trends
    have collapsed). If every building is PLASTIC, the threshold is too loose.

    This is a property of the *synthetic* generator, which plants enough
    accelerating-creep buildings to clear the STL threshold. On a real-InSAR
    AOI the defensibility gate marks the bulk of pixels INSUFFICIENT (no linear
    trend survives), so an all-ELASTIC outcome is correct, not threshold drift —
    that AOI is skipped rather than failed.
    """
    checked = 0
    for code in ("huruma", "mombasa"):
        if _provenance(code) == "insar":
            continue
        checked += 1
        elastic, plastic = con.execute(
            """
            SELECT
              SUM(CASE WHEN failure_mode = ? THEN 1 ELSE 0 END),
              SUM(CASE WHEN failure_mode = ? THEN 1 ELSE 0 END)
            FROM buildings WHERE aoi_code = ?
            """,
            [FAILURE_ELASTIC, FAILURE_PLASTIC, code],
        ).fetchone()
        total = elastic + plastic
        plastic_frac = plastic / total
        assert 0.05 <= plastic_frac <= 0.85, (
            f"{code}: PLASTIC fraction {plastic_frac:.1%} outside the sane band "
            f"[5%, 85%] ({plastic} of {total}); STL thresholds may have drifted"
        )
    if checked == 0:
        pytest.skip("both AOIs are real InSAR; STL-threshold invariant is synthetic-only")


# ---------------------------------------------------------------------------
# Tier 3 — velocity σ scales with (1 - coherence)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("aoi_code", ["huruma", "mombasa"])
def test_velocity_sigma_anticorrelated_with_coherence(con, aoi_code):
    """σ ≈ k * (1 - γ): high-σ buildings must have lower latest-coherence than
    low-σ buildings. We compare top-decile vs bottom-decile σ groups.

    σ = k(1-γ) is monotonic in BOTH the synthetic generator and the real
    pipeline (`join_insar._velocity_sigma_from_coherence`), so the *direction*
    must hold everywhere. The *magnitude* of the decile gap, though, depends on
    how widely coherence is spread: the synthetic field spans γ∈[~0.3, ~0.99]
    so the gap clears 0.10, while a real high-coherence AOI (Huruma γ≈0.93,
    tightly clustered) shows a smaller but still strictly-ordered gap. Require
    the strict ordering always; require the 0.10 gap only on synthetic spread.
    """
    df = con.execute(
        """
        WITH latest_coh AS (
          SELECT building_id, coherence,
                 ROW_NUMBER() OVER (PARTITION BY building_id ORDER BY observation_date DESC) AS rn
          FROM subsidence_time_series WHERE aoi_code = ?
        )
        SELECT b.velocity_sigma_mm_yr, c.coherence
        FROM buildings b JOIN latest_coh c USING (building_id)
        WHERE b.aoi_code = ? AND c.rn = 1
        """,
        [aoi_code, aoi_code],
    ).fetchall()
    sig = np.array([r[0] for r in df], dtype=np.float32)
    coh = np.array([r[1] for r in df], dtype=np.float32)
    finite = np.isfinite(sig) & np.isfinite(coh)
    sig, coh = sig[finite], coh[finite]
    assert sig.size > 0, f"{aoi_code}: no finite σ/coherence rows"
    hi = sig >= np.percentile(sig, 90)
    lo = sig <= np.percentile(sig, 10)
    # Direction holds on real and synthetic data alike.
    assert coh[hi].mean() < coh[lo].mean(), (
        f"{aoi_code}: σ↑ should track γ↓ but top-decile-σ coh={coh[hi].mean():.3f} "
        f">= bottom-decile-σ coh={coh[lo].mean():.3f}; model decoupled?"
    )
    # Magnitude floor only where coherence spread is synthetic-wide.
    if _provenance(aoi_code) != "insar":
        assert coh[hi].mean() < coh[lo].mean() - 0.10, (
            f"{aoi_code}: σ-γ gap too small on synthetic data "
            f"({coh[hi].mean():.3f} vs {coh[lo].mean():.3f}); model weakened?"
        )


# ---------------------------------------------------------------------------
# Tier 3 — cohort percentiles cover the [0, 100] range
# ---------------------------------------------------------------------------


def test_cohort_percentiles_span_full_range(con):
    """If percentiles are squished, the cohort binning is degenerate (only one
    bucket, or singletons everywhere). We expect at least one building near each
    end of the range in both AOIs."""
    for code in ("huruma", "mombasa"):
        lo, hi = con.execute(
            """
            SELECT MIN(cohort_composite_pct), MAX(cohort_composite_pct)
            FROM buildings WHERE aoi_code = ? AND cohort_size > 1
            """,
            [code],
        ).fetchone()
        assert lo <= 10, f"{code}: lowest composite pct = {lo} (expected ≤ 10)"
        assert hi >= 90, f"{code}: highest composite pct = {hi} (expected ≥ 90)"
