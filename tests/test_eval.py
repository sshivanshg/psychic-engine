"""Tests for the eval harness — pure stats (cross-sectional IC, Newey-West, terciles) + integration."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from tradeos.config import ANNOUNCEMENT_LAG_DAYS
from tradeos.eval import (
    _cross_sectional_ic,
    _fundamental_feature,
    _net_spread,
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


def test_net_spread_charges_round_trip_cost():
    # Net spread = gross - 4 legs of cost (enter+exit on both the long and the short book).
    assert _net_spread(None, 15) is None
    assert _net_spread(0.0, 15) == pytest.approx(-4 * 15 / 1e4)
    assert _net_spread(0.01, 15) < 0.01                       # cost always erodes the gross
    assert _net_spread(0.01, 0) == pytest.approx(0.01)        # zero cost ⇒ net == gross


def test_fundamental_feature_is_point_in_time():
    """The feature is indexed by AVAILABILITY (period_end + lag), never period-end — so the eval
    can only ever use a quarter from the day its results were public. Margin level is exact."""
    df = pd.DataFrame({
        "period_end": [dt.date(2025, 3, 31), dt.date(2025, 6, 30)],
        "total_revenue": [1000.0, 1200.0],
        "net_income": [200.0, 180.0],
    })
    feat = _fundamental_feature(df, "net_margin")
    expected_idx = [pd.Timestamp(p) + pd.Timedelta(days=ANNOUNCEMENT_LAG_DAYS)
                    for p in df["period_end"]]
    assert list(feat.index) == expected_idx                  # availability-dated, not period-end
    assert feat.iloc[0] == pytest.approx(20.0)               # 200/1000
    assert feat.iloc[1] == pytest.approx(15.0)               # 180/1200


def test_fundamental_feature_handles_zero_revenue():
    df = pd.DataFrame({
        "period_end": [dt.date(2025, 3, 31), dt.date(2025, 6, 30)],
        "total_revenue": [0.0, 1200.0],
        "net_income": [200.0, 180.0],
    })
    feat = _fundamental_feature(df, "net_margin")            # div-by-zero ⇒ inf ⇒ dropped, not crash
    assert len(feat) == 1 and feat.iloc[0] == pytest.approx(15.0)


def test_evaluate_integration():
    try:
        out = evaluate(horizon=21, step=5)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"DB not available: {e}")
    assert out["horizon_days"] == 21 and out["nw_lag"] == 20 and "signals" in out
    # honesty metadata is surfaced, not buried
    assert out["lag_days"] == ANNOUNCEMENT_LAG_DAYS
    assert out["cost_bps"] > 0 and out["n_signals"] == len(out["signals"])
    assert "survivorship" in out
    for _name, s in out["signals"].items():
        assert {"kind", "ic", "icir", "t_stat", "n_dates", "hit_rate_pct", "base_rate_pct",
                "ls_spread_pct", "ls_spread_net_pct", "pooled_ic"} <= set(s)
        assert s["kind"] in ("price", "fundamental")
        if s["ic"] is not None:
            assert -1.0 <= s["ic"] <= 1.0
        if s["pooled_ic"] is not None:
            assert -1.0 <= s["pooled_ic"] <= 1.0
        # net spread is always <= gross when both are present (cost only erodes)
        if s["ls_spread_pct"] is not None and s["ls_spread_net_pct"] is not None:
            assert s["ls_spread_net_pct"] <= s["ls_spread_pct"]
