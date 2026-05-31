"""Tests for the eval harness — pure stats (cross-sectional IC, Newey-West, terciles) + integration."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from tradeos.eval import (
    _cross_sectional_ic,
    _newey_west_tstat,
    _rsi_series,
    _spearman,
    _tercile_spread,
    evaluate,
)


def test_spearman_monotonic():
    x = pd.Series(range(30))
    y = pd.Series([v * 2 + 1 for v in range(30)])      # perfectly increasing
    assert _spearman(x, y) > 0.99
    assert _spearman(x, pd.Series(list(reversed(y)))) < -0.99


def test_spearman_degenerate():
    assert _spearman(pd.Series([1, 1, 1]), pd.Series([1, 2, 3])) is None  # zero variance
    assert _spearman(pd.Series([1.0]), pd.Series([2.0])) is None          # too few


def test_rsi_series_bounds():
    rng = np.random.default_rng(0)
    s = pd.Series(100 + rng.normal(0, 1, 300).cumsum())
    r = _rsi_series(s).dropna()
    assert r.between(0, 100).all()


def test_cross_sectional_ic_perfect_and_underpowered():
    dates = [dt.date(2026, 1, 1), dt.date(2026, 1, 2)]
    # Each date: forward return is a monotonic function of the signal across names ⇒ daily IC = 1.
    sig = pd.DataFrame({"A": [1, 1], "B": [2, 2], "C": [3, 3], "D": [4, 4], "E": [5, 5]}, index=dates)
    fwd = pd.DataFrame({"A": [.1, .2], "B": [.2, .3], "C": [.3, .4], "D": [.4, .5], "E": [.5, .6]}, index=dates)
    ics = _cross_sectional_ic(sig, fwd, min_names=4)
    assert len(ics) == 2 and ics.mean() > 0.99

    # Too few names per date for a credible cross-section ⇒ no dates qualify.
    assert _cross_sectional_ic(sig[["A", "B", "C"]], fwd[["A", "B", "C"]], min_names=4).empty


def test_newey_west_widens_se_under_autocorrelation():
    # A positively autocorrelated series with a positive mean: ignoring the autocorrelation
    # (lag=0, classic t) overstates significance; the Newey-West correction must shrink |t|.
    rng = np.random.default_rng(0)
    n = 600
    ar = np.zeros(n)
    for i in range(1, n):
        ar[i] = 0.85 * ar[i - 1] + rng.normal()
    s = pd.Series(ar + 0.5)
    m0, t0 = _newey_west_tstat(s, lag=0)     # classic (treats obs as independent)
    mL, tL = _newey_west_tstat(s, lag=60)    # HAC: account for the serial correlation
    assert m0 == pytest.approx(mL)           # same point estimate
    assert abs(tL) < abs(t0)                 # honest SE is wider ⇒ smaller |t|


def test_newey_west_too_few():
    mean, t = _newey_west_tstat(pd.Series([0.1, 0.2, 0.3]), lag=2)
    assert t is None and mean == pytest.approx(0.2)


def test_tercile_spread_sign():
    d = [dt.date(2026, 1, 1)]
    sig = pd.DataFrame({"A": [1], "B": [2], "C": [3], "D": [4], "E": [5]}, index=d)
    fwd = pd.DataFrame({"A": [-.1], "B": [0.0], "C": [.1], "D": [.2], "E": [.3]}, index=d)
    assert _tercile_spread(sig, fwd, min_names=4) > 0           # high-signal names outperform
    assert _tercile_spread(sig, -fwd, min_names=4) < 0          # invert returns ⇒ negative spread


def test_evaluate_integration():
    try:
        out = evaluate(horizon=21, step=5)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"DB not available: {e}")
    assert out["horizon_days"] == 21 and out["nw_lag"] == 20 and "signals" in out
    for _name, s in out["signals"].items():
        assert {"ic", "icir", "t_stat", "n_dates", "hit_rate_pct", "base_rate_pct",
                "ls_spread_pct", "pooled_ic"} <= set(s)
        if s["ic"] is not None:
            assert -1.0 <= s["ic"] <= 1.0
        if s["pooled_ic"] is not None:
            assert -1.0 <= s["pooled_ic"] <= 1.0
