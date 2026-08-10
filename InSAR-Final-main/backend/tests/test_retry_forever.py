"""
Unit tests for the network-resilience helper that makes the overnight InSAR
download survive a flaky connection (scripts/hyp3_pipeline._retry_forever and
_is_transient).

Pure, fast, always-run — no network, no real sleeps (sleep is injected/mocked).
They pin the "don't fail on low internet" contract:

  - a transient error (dropped socket, timeout) is retried, backed off, and the
    call eventually succeeds;
  - a NON-transient error (a real bug) propagates on the first occurrence — we
    never spin forever on something a retry can't fix;
  - backoff is exponential and capped.

Run from backend/:  pytest tests/test_retry_forever.py
"""

from __future__ import annotations

import pytest

from scripts.hyp3_pipeline import _retry_forever, _is_transient


# ---------------------------------------------------------------------------
# _is_transient
# ---------------------------------------------------------------------------
def test_oserror_is_transient():
    assert _is_transient(ConnectionResetError("reset")) is True
    assert _is_transient(TimeoutError("slow")) is True
    assert _is_transient(OSError("ehostunreach")) is True


def test_wrapped_cause_is_detected():
    # SDKs wrap the socket error; we must see through the chain.
    inner = ConnectionResetError("peer reset")
    outer = RuntimeError("download failed")
    outer.__cause__ = inner
    assert _is_transient(outer) is True


def test_real_bug_is_not_transient():
    assert _is_transient(KeyError("missing")) is False
    assert _is_transient(ValueError("bad")) is False


# ---------------------------------------------------------------------------
# _retry_forever
# ---------------------------------------------------------------------------
def test_retries_transient_then_succeeds():
    calls = {"n": 0}
    slept: list[float] = []

    def flaky():
        calls["n"] += 1
        if calls["n"] < 4:
            raise ConnectionResetError("blip")
        return "ok"

    out = _retry_forever(flaky, what="test", base=1.0, cap=100.0,
                         log=lambda *_: None, _sleep=slept.append)
    assert out == "ok"
    assert calls["n"] == 4            # failed 3×, succeeded on the 4th
    assert len(slept) == 3            # one sleep per failure
    # Exponential: 1, 2, 4 (plus jitter ≤25%), strictly increasing, under cap.
    assert slept[0] < slept[1] < slept[2]
    assert all(s <= 100.0 * 1.25 for s in slept)


def test_nontransient_raises_immediately():
    calls = {"n": 0}

    def buggy():
        calls["n"] += 1
        raise KeyError("real bug")

    with pytest.raises(KeyError):
        _retry_forever(buggy, what="test", log=lambda *_: None, _sleep=lambda _: None)
    assert calls["n"] == 1            # no retry on a non-transient error


def test_success_passes_through_without_sleeping():
    slept: list[float] = []
    out = _retry_forever(lambda: 42, what="test",
                         log=lambda *_: None, _sleep=slept.append)
    assert out == 42
    assert slept == []                # happy path never sleeps


def test_backoff_is_capped():
    slept: list[float] = []
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 12:
            raise TimeoutError("slow")
        return "done"

    _retry_forever(flaky, what="test", base=1.0, cap=10.0,
                   log=lambda *_: None, _sleep=slept.append)
    # Late delays must be clamped to cap (+ jitter), not 2**11.
    assert max(slept) <= 10.0 * 1.25
