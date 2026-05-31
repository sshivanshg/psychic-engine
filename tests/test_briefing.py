"""Tests for the briefing alert rules + payload shaping (pure, no DB/LLM)."""

from tradeos.briefing import build_briefing, evaluate_alerts


def test_every_rule_fires_on_its_condition():
    card = {
        "symbol": "X",
        "risk": {"risk_contribution_pct": 45},                      # ≥ 40% limit
        "technical": {"dials": {"trend": "downtrend", "level": "near lows"}},
        "fundamental": {"net_income_yoy_pct": -12,
                        "dials": {"earnings_growth": "declining", "margin_trend": "contracting"}},
        "sentiment": {"label": "negative", "n_articles": 7},
        "delta": {"changes": ["trend uptrend → downtrend"]},
    }
    alerts = evaluate_alerts(card)
    assert any("top risk contributor" in a for a in alerts)
    assert any("downtrend and near 52-week lows" in a for a in alerts)
    assert any("earnings declining" in a for a in alerts)
    assert any("margins contracting" in a for a in alerts)
    assert any("negative news flow" in a for a in alerts)
    assert any("changed since last run" in a for a in alerts)


def test_calm_card_trips_no_alerts():
    card = {
        "symbol": "Y",
        "risk": {"risk_contribution_pct": 8},
        "technical": {"dials": {"trend": "uptrend", "level": "mid-range"}},
        "fundamental": {"dials": {"earnings_growth": "growing", "margin_trend": "stable"}},
        "sentiment": {"label": "neutral", "n_articles": 3},
        "delta": {"changes": []},
    }
    assert evaluate_alerts(card) == []


def test_build_briefing_separates_flagged_from_quiet():
    analysis = {
        "as_of": "2026-05-29", "horizon": "annual", "risk_overview": {}, "sector_overview": {},
        "cards": [
            {"symbol": "X", "attention": {"score": 72}, "confidence": {"level": "high"},
             "risk": {"risk_contribution_pct": 45}},
            {"symbol": "Y", "attention": {"score": 20}, "confidence": {"level": "medium"},
             "risk": {"risk_contribution_pct": 8},
             "technical": {"dials": {"trend": "uptrend", "level": "mid-range"}}},
        ],
    }
    b = build_briefing(analysis)
    assert len(b["stocks"]) == 2
    assert b["n_flagged"] == 1 and b["flagged"][0]["symbol"] == "X"
    assert b["stocks"][0]["attention"] == 72 and b["stocks"][0]["confidence"] == "high"
