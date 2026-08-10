"""Regression guard for the parallelized STL decomposition.

`_stl_decompose` fans the per-building STL fits across processes when it pays
off (it is ~99% of the join's build-time CPU). The fits are independent, so the
parallel result MUST be bit-for-bit identical to the serial loop — this is a
life-safety scoring input (trend_slope / trend_r2 / failure_mode feed the
collapse score and danger tier). These tests pin that invariant so a future
edit can't silently let the parallel and serial paths diverge.
"""
import numpy as np
import pytest

from scripts.postprocess import _stl_decompose, _stl_decompose_chunk, _stl_worker_count


def _make_disp(n: int, m: int = 24, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(m, dtype=np.float64)
    slopes = rng.normal(-4.0, 6.0, n)
    seasonal = rng.uniform(0, 8, n)
    disp = (slopes[:, None] * (t[None, :] / 12.0)
            + seasonal[:, None] * np.sin(2 * np.pi * t[None, :] / 12.0)
            + rng.normal(0, 1.5, (n, m)))
    return disp.astype(np.float32)


def _assert_identical(a, b):
    names = ("trend", "trend_slope", "seasonal_amp", "trend_r2", "failure_mode")
    for name, x, y in zip(names, a, b):
        assert np.array_equal(x, y), f"{name} differs between serial and parallel paths"


def test_parallel_matches_serial_bit_for_bit():
    """The whole point: parallel output == serial output, exactly."""
    disp = _make_disp(700)                      # above the worker threshold
    serial = _stl_decompose_chunk(disp, 12)     # the serial unit of work
    parallel = _stl_decompose(disp, 12)         # may fan across processes
    _assert_identical(serial, parallel)


def test_small_input_stays_serial():
    """Below the threshold, no pool is spun up — still correct."""
    assert _stl_worker_count(50) == 1
    disp = _make_disp(50)
    _assert_identical(_stl_decompose_chunk(disp, 12), _stl_decompose(disp, 12))


def test_env_var_forces_serial(monkeypatch):
    """STL_WORKERS=1 is the escape hatch for tests/debugging."""
    monkeypatch.setenv("STL_WORKERS", "1")
    assert _stl_worker_count(5000) == 1
    disp = _make_disp(400)
    _assert_identical(_stl_decompose_chunk(disp, 12), _stl_decompose(disp, 12))


def test_worker_count_respects_cap_and_size():
    # Never more workers than buildings; capped at 8.
    assert _stl_worker_count(0) == 1
    assert 1 <= _stl_worker_count(10_000) <= 8


@pytest.mark.parametrize("n", [301, 999, 1500])
def test_row_alignment_preserved(n):
    """array_split + concatenate must preserve row order so velocity[i] ↔ building i."""
    disp = _make_disp(n, seed=n)
    serial = _stl_decompose_chunk(disp, 12)
    parallel = _stl_decompose(disp, 12)
    _assert_identical(serial, parallel)
