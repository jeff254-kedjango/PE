"""
Unit tests for the building-height imputation and InSAR pixel-share signal
(scripts/join_insar.py: `_impute_heights` and `_pixel_share`).

Pure-function tests — no DB, no real InSAR — so they always run. They pin two
life-safety / honesty contracts:

  - a building with NO source height is NOT defaulted to the safest (lowest-load)
    extreme; it is imputed from floors or the neighbourhood median and flagged;
  - `pixel_share` correctly counts how many buildings share one ~78 m InSAR cell,
    the measurement-specificity caveat the UI surfaces (94% of real buildings
    share a cell).

Run from backend/:  pytest tests/test_height_pipeline.py
"""

from __future__ import annotations

import math

import numpy as np

from scripts.join_insar import _impute_heights, _pixel_share, MEAN_FLOOR_M

NAN = float("nan")


# ---------------------------------------------------------------------------
# _impute_heights
# ---------------------------------------------------------------------------
def test_real_height_used_and_not_imputed():
    h, imp = _impute_heights(np.array([12.0, 4.5]), np.array([0, 0]), MEAN_FLOOR_M)
    assert list(h) == [12.0, 4.5]
    assert list(imp) == [False, False]


def test_missing_height_falls_back_to_floors_not_safest_default():
    # No source height, but 5 floors known → 5 * 3.0 = 15 m, flagged imputed.
    # The OLD code would have silently used 3.0 m (the safest, lowest-load case).
    h, imp = _impute_heights(np.array([NAN]), np.array([5]), MEAN_FLOOR_M)
    assert h[0] == 5 * MEAN_FLOOR_M
    assert h[0] > MEAN_FLOOR_M          # strictly taller than the old blind default
    assert bool(imp[0]) is True


def test_missing_everything_uses_aoi_median_of_real():
    # Two real heights (10, 20 → median 15) plus one row with no height and no
    # floors → it should inherit the AOI median, NOT 3.0, and be flagged.
    raw_h = np.array([10.0, 20.0, NAN])
    raw_nf = np.array([0, 0, 0])
    h, imp = _impute_heights(raw_h, raw_nf, MEAN_FLOOR_M)
    assert h[2] == 15.0                 # median(10, 20)
    assert list(imp) == [False, False, True]


def test_all_missing_degrades_to_mean_floor():
    # No real heights anywhere → median is undefined; fall back to mean_floor_m
    # so the pipeline still produces a finite, sane height (and flags it).
    h, imp = _impute_heights(np.array([NAN, NAN]), np.array([0, 0]), MEAN_FLOOR_M)
    assert list(h) == [MEAN_FLOOR_M, MEAN_FLOOR_M]
    assert list(imp) == [True, True]


def test_zero_or_negative_height_treated_as_missing():
    # A 0 m or negative "height" is not a real measurement → imputed.
    h, imp = _impute_heights(np.array([0.0, -3.0]), np.array([2, 0]), MEAN_FLOOR_M)
    assert h[0] == 2 * MEAN_FLOOR_M      # 0 height, 2 floors → 6 m
    assert bool(imp[0]) is True
    assert bool(imp[1]) is True          # negative, no floors → median/fallback


# ---------------------------------------------------------------------------
# _pixel_share
# ---------------------------------------------------------------------------
def test_pixel_share_counts_co_cell_buildings():
    # Two buildings in cell (0,0), one in (1,1) → shares [2, 2, 1].
    share = _pixel_share([(0, 0), (0, 0), (1, 1)])
    assert list(share) == [2, 2, 1]


def test_pixel_share_all_distinct():
    share = _pixel_share([(0, 0), (1, 1), (2, 2)])
    assert list(share) == [1, 1, 1]


def test_pixel_share_all_same_cell():
    share = _pixel_share([(3, 4)] * 5)
    assert list(share) == [5, 5, 5, 5, 5]


def test_pixel_share_empty():
    share = _pixel_share([])
    assert len(share) == 0
