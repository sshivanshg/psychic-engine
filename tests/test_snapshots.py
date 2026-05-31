"""Tests for the run-over-run what-changed delta (pure diff, no DB)."""

from tradeos.snapshots import compute_delta, snapshot_card


def test_snapshot_card_fingerprint():
    card = {
        "symbol": "X",
        "attention": {"score": 72},
        "technical": {"dials": {"trend": "downtrend", "momentum": "weak", "level": "near lows"}},
        "fundamental": {"dials": {"revenue_growth": "growing", "earnings_growth": "declining",
                                  "margin_trend": "contracting"}},
        "sentiment": {"label": "negative"},
        "ownership": {"dials": {"institutional": "high"}},
        "risk": {"risk_contribution_pct": 44},
    }
    snap = snapshot_card(card)
    assert snap["attention"] == 72 and snap["trend"] == "downtrend"
    assert snap["sentiment"] == "negative" and snap["institutional"] == "high"
    assert snap["risk_contribution_pct"] == 44


def test_compute_delta_first_run_and_flips():
    curr = {"attention": 72, "trend": "downtrend", "momentum": "weak",
            "risk_contribution_pct": 44, "earnings_growth": "declining"}
    assert compute_delta(None, curr) == []                  # first run: no prior to diff
    assert compute_delta(curr, dict(curr)) == []            # nothing moved
    prev = {"attention": 50, "trend": "uptrend", "momentum": "weak",
            "risk_contribution_pct": 44, "earnings_growth": "growing"}
    ch = compute_delta(prev, curr)
    assert any("trend uptrend → downtrend" in x for x in ch)
    assert any("earnings_growth growing → declining" in x for x in ch)
    assert any("attention 50 → 72" in x for x in ch)


def test_compute_delta_respects_numeric_thresholds():
    # small numeric moves below the epsilons are noise → not reported
    prev = {"attention": 70, "risk_contribution_pct": 44}
    curr = {"attention": 72, "risk_contribution_pct": 45}   # +2 score (<5), +1 risk (<3)
    assert compute_delta(prev, curr) == []


def test_compute_delta_ignores_appearing_or_vanishing_reads():
    # a dial that was None before (or is None now) isn't a "flip" — avoids noise on first data arrival
    assert compute_delta({"trend": None}, {"trend": "downtrend"}) == []
    assert compute_delta({"trend": "downtrend"}, {"trend": None}) == []
