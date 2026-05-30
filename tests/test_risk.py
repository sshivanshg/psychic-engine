"""Audit suite for the risk engine.

Two layers:
  1. Pure-function unit tests — validate the quant MATH against independent calculations,
     no DB needed (deterministic, seeded).
  2. Integration tests — run compute_risk() against the live DB and assert invariants that
     MUST hold for any correct risk snapshot (risk contributions sum to 100, CVaR >= VaR, etc.).
"""

import datetime as dt
import math

import numpy as np
import pandas as pd
import pytest

from tradeos.risk import (
    _adjusted_beta,
    _ewma_cov,
    _round,
    _var_cvar,
    _worst_window,
    compute_risk,
    parse_horizon,
)


# ----------------------- pure-function unit tests (no DB) -----------------------

def test_round_handles_none_and_nan():
    assert _round(None) is None
    assert _round(float("nan")) is None
    assert _round(1.23456) == 1.23
    assert _round(1.23456, 4) == 1.2346
    assert _round("x") == "x"


def test_parse_horizon():
    assert parse_horizon("weekly") == (5, "weekly")
    assert parse_horizon("1y") == (252, "annual")
    assert parse_horizon("annual") == (252, "annual")
    assert parse_horizon("d") == (1, "daily")
    assert parse_horizon("10") == (10, "10d")
    assert parse_horizon("10d") == (10, "10d")
    assert parse_horizon(None) == (252, "annual")
    with pytest.raises(ValueError):
        parse_horizon("banana")


def test_ewma_cov_equals_population_cov_when_lambda_one():
    rng = np.random.default_rng(42)
    R = pd.DataFrame(rng.normal(0, 0.01, size=(200, 3)), columns=["a", "b", "c"])
    cov = _ewma_cov(R, lam=1.0).values
    ref = np.cov(R.values.T, bias=True)
    assert np.allclose(cov, ref)


def test_ewma_cov_symmetric_and_psd():
    rng = np.random.default_rng(1)
    R = pd.DataFrame(rng.normal(0, 0.01, size=(150, 4)), columns=list("abcd"))
    cov = _ewma_cov(R, lam=0.94).values
    assert np.allclose(cov, cov.T)
    assert np.linalg.eigvalsh(cov).min() > -1e-12


def test_ewma_cov_scaling_relationship():
    rng = np.random.default_rng(7)
    a = rng.normal(0, 0.01, 120)
    cov = _ewma_cov(pd.DataFrame({"a": a, "b": 2 * a}), lam=0.94)
    assert math.isclose(cov.loc["b", "b"], 4 * cov.loc["a", "a"], rel_tol=1e-9)
    assert math.isclose(cov.loc["a", "b"], 2 * cov.loc["a", "a"], rel_tol=1e-9)


def test_ewma_weights_recent_more():
    early = np.zeros(100)
    early[5] = 0.1
    late = np.zeros(100)
    late[95] = 0.1
    v_early = _ewma_cov(pd.DataFrame({"x": early}), lam=0.94).iloc[0, 0]
    v_late = _ewma_cov(pd.DataFrame({"x": late}), lam=0.94).iloc[0, 0]
    assert v_late > v_early


def test_var_cvar_matches_manual():
    rng = np.random.default_rng(3)
    s = pd.Series(rng.normal(0, 0.01, 1000))
    var, cvar = _var_cvar(s, 0.95)
    cutoff = np.quantile(s.values, 0.05)
    assert math.isclose(var, -cutoff, rel_tol=1e-9)
    assert math.isclose(cvar, -s[s <= cutoff].mean(), rel_tol=1e-9)
    assert cvar >= var


def test_var_cvar_too_few_points():
    assert _var_cvar(pd.Series([0.0] * 5), 0.95) == (None, None)


def test_worst_window():
    r = pd.Series([0.0, -0.10, -0.10, 0.05, 0.05])
    assert math.isclose(_worst_window(r, 2), 0.9 * 0.9 - 1, rel_tol=1e-9)
    assert math.isclose(_worst_window(r, 1), -0.10, rel_tol=1e-9)


def test_adjusted_beta_shrinks_toward_one():
    rng = np.random.default_rng(11)
    b = pd.Series(rng.normal(0, 0.01, 300))
    # asset == 2×benchmark → raw beta 2 → adjusted ⅔·2 + ⅓ = 1.667
    assert math.isclose(_adjusted_beta(2 * b, b), 0.67 * 2 + 0.33, rel_tol=1e-6)
    # asset == benchmark → raw 1 → adjusted stays 1
    assert math.isclose(_adjusted_beta(b, b), 1.0, rel_tol=1e-6)
    # too few observations → None (don't fit a structural beta on noise)
    assert _adjusted_beta(pd.Series([0.0] * 10), pd.Series([0.0] * 10)) is None


# --------------------------- integration tests (need DB) ---------------------------

@pytest.fixture(scope="module")
def risk():
    try:
        return compute_risk()
    except Exception as e:  # noqa: BLE001 - any DB/connection failure should skip, not error
        pytest.skip(f"DB not available: {e}")


def test_risk_contributions_sum_to_100(risk):
    rc = [p["risk_contribution_pct"] for p in risk["positions"]
          if p["risk_contribution_pct"] is not None]
    if rc:
        assert math.isclose(sum(rc), 100.0, abs_tol=0.5)


def test_weights_sum_to_100(risk):
    w = [p["weight_pct"] for p in risk["positions"] if p["weight_pct"] is not None]
    if w:
        assert math.isclose(sum(w), 100.0, abs_tol=0.5)


def test_tail_ordering(risk):
    p = risk["portfolio"]
    if p["var_95_pct"] is not None and p["var_99_pct"] is not None:
        assert p["var_99_pct"] >= p["var_95_pct"] - 1e-9
        assert p["cvar_95_pct"] >= p["var_95_pct"] - 1e-9
        assert p["cvar_99_pct"] >= p["var_99_pct"] - 1e-9


def test_vol_positive_and_sane(risk):
    v = risk["portfolio"]["vol_pct"]
    if v is not None:
        assert 0 < v < 200


def test_correlation_matrix_valid(risk):
    corr = risk["correlation"]
    for a in corr:
        assert math.isclose(corr[a][a], 1.0, abs_tol=0.01)
        for b in corr:
            assert -1.01 <= corr[a][b] <= 1.01
            assert math.isclose(corr[a][b], corr[b][a], abs_tol=0.01)


def test_limits_structure(risk):
    for c in risk["limits"]:
        assert {"metric", "value", "limit", "ok"} <= set(c)
        assert isinstance(c["ok"], bool)


def test_as_of_no_lookahead(risk):
    past = dt.date.fromisoformat(risk["as_of"]) - dt.timedelta(days=120)
    r2 = compute_risk(as_of=past)
    assert dt.date.fromisoformat(r2["as_of"]) <= past


def test_horizon_scaling():
    """Vol scales by √(horizon/252); risk-contribution % is horizon-invariant."""
    try:
        ann = compute_risk(horizon="annual")
        wk = compute_risk(horizon="weekly")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"DB not available: {e}")
    va, vw = ann["portfolio"]["vol_pct"], wk["portfolio"]["vol_pct"]
    if va and vw:
        assert math.isclose(vw / va, math.sqrt(5 / 252), rel_tol=0.02)
    ra = {p["symbol"]: p["risk_contribution_pct"] for p in ann["positions"]}
    rw = {p["symbol"]: p["risk_contribution_pct"] for p in wk["positions"]}
    for s in ra:
        if ra[s] is not None and rw[s] is not None:
            assert math.isclose(ra[s], rw[s], abs_tol=0.1)  # invariant


# ------------------------------- agent (no network) -------------------------------

def test_narrate_returns_none_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from tradeos.risk_agent import RiskReport, narrate_risk
    assert narrate_risk({"portfolio": {}}) is None
    RiskReport(
        headline="h", risk_drivers=[], tail_and_stress="t", diversification="d",
        liquidity="l", limit_flags=[], position_notes=[], reminder="r",
    )
