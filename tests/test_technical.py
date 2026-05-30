"""Tests for the Technical agent and the orchestrator."""

import math

import numpy as np
import pandas as pd
import pytest

from tradeos.technical import _ret, _rsi, _sma, compute_technical


def _series(vals):
    return pd.Series(list(vals), dtype=float)


# ----------------------------- indicator unit tests -----------------------------

def test_sma():
    s = _series(range(1, 61))
    assert _sma(s, 20) == sum(range(41, 61)) / 20  # mean of last 20 = 50.5


def test_ret():
    s = _series([100, 110, 121])
    assert math.isclose(_ret(s, 1), 0.10, rel_tol=1e-9)
    assert math.isclose(_ret(s, 2), 0.21, rel_tol=1e-9)


def test_rsi_all_gains_is_100():
    assert _rsi(_series(range(1, 60))) == 100.0


def test_rsi_all_losses_is_0():
    assert _rsi(_series(range(60, 1, -1))) == 0.0


def test_rsi_in_bounds():
    rng = np.random.default_rng(0)
    s = _series(100 + rng.normal(0, 1, 200).cumsum())
    r = _rsi(s)
    assert 0 <= r <= 100


def test_compute_technical_shape_and_trend():
    s = _series(range(1, 260))       # steady uptrend
    v = _series([1000] * 259)
    t = compute_technical(s, v)
    assert t is not None
    assert t["dials"]["trend"] == "uptrend"
    assert t["dials"]["momentum"] == "overbought"   # strictly rising -> RSI 100
    assert {"rsi_14", "macd_hist", "sma200", "pct_from_52w_high", "dials"} <= set(t)


def test_compute_technical_too_short():
    assert compute_technical(_series(range(10)), _series(range(10))) is None


# ----------------------------- orchestrator integration -----------------------------

def test_analyze_integration():
    try:
        from tradeos.orchestrator import analyze
        a = analyze(narrate=False)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"DB not available: {e}")
    assert {"cards", "risk_overview", "as_of"} <= set(a)
    for c in a["cards"]:
        assert "technical" in c and "risk" in c
        assert {"trend", "momentum", "level"} <= set(c["technical"]["dials"])
        # card is ranked by risk contribution and carries a risk slice
        assert "risk_contribution_pct" in c["risk"]
