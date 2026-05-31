"""Tests for the descriptive attention score + calibrated confidence (pure, no DB/LLM)."""

from tradeos.scoring import compute_attention, compute_confidence

_HIGH = {
    "risk": {"risk_contribution_pct": 46.6, "weight_pct": 31.7},
    "technical": {"rsi_14": 78, "pct_from_52w_high": -1,
                  "dials": {"trend": "downtrend", "momentum": "overbought", "level": "at highs"}},
    "fundamental": {"net_income_yoy_pct": -12,
                    "dials": {"revenue_growth": "declining", "earnings_growth": "declining",
                              "margin_trend": "contracting"}},
}
_CALM = {
    "risk": {"risk_contribution_pct": 8, "weight_pct": 10},
    "technical": {"rsi_14": 52, "dials": {"trend": "uptrend", "momentum": "neutral", "level": "mid-range"}},
    "fundamental": {"dials": {"revenue_growth": "growing", "earnings_growth": "growing",
                              "margin_trend": "stable"}},
}


def test_notable_outscores_calm():
    hi, lo = compute_attention(_HIGH), compute_attention(_CALM)
    assert hi["score"] > 60 and lo["score"] < 40
    assert hi["score"] > lo["score"]
    # every dimension contributed a sub-score
    assert all(hi["components"][d] is not None for d in ("risk", "technical", "fundamental"))
    assert hi["drivers"] and len(hi["drivers"]) <= 4


def test_missing_dimension_is_skipped_not_zero():
    card = {"risk": _CALM["risk"], "technical": _CALM["technical"], "fundamental": None}
    out = compute_attention(card)
    assert out["score"] is not None
    assert out["components"]["fundamental"] is None        # skipped, not counted as 0
    assert out["components"]["macro"] is None


def test_empty_card_scores_none():
    assert compute_attention({})["score"] is None


def test_weights_are_overridable():
    # zero-weighting every present dimension except risk ⇒ score collapses to the risk sub-score
    out = compute_attention(_HIGH, weights={"technical": 0.0, "fundamental": 0.0, "macro": 0.0})
    assert out["score"] == out["components"]["risk"]


def test_sentiment_and_ownership_contribute_subscores():
    card = {**_HIGH,
            "sentiment": {"mean_polarity": -0.5, "label": "negative", "n_articles": 6},
            "ownership": {"institutional_pct": 82, "dials": {"institutional": "high"}}}
    out = compute_attention(card)
    assert out["components"]["sentiment"] is not None
    assert out["components"]["ownership"] is not None


# ----------------------------- calibrated confidence -----------------------------

def test_confidence_rises_with_completeness_and_coherence():
    full = {**_HIGH,
            "macro": {"sector": "Tech", "sector_weight_pct": 47},
            "sentiment": {"label": "negative", "mean_polarity": -0.4, "n_articles": 5},
            "ownership": {"institutional_pct": 60, "dials": {"institutional": "high"}},
            "fundamental": {**_HIGH["fundamental"], "latest_quarter": "2026-03-31"},
            "technical": {**_HIGH["technical"], "sma200": 1400}}
    sparse = {"risk": _CALM["risk"], "technical": {"dials": {"trend": "uptrend"}}}
    cf, cs = compute_confidence(full), compute_confidence(sparse)
    assert cf["score"] > cs["score"]
    assert cf["level"] in ("high", "medium") and cs["level"] in ("low", "medium")
    assert "dimensions present" in cf["reasons"][0]
    assert "probability" in cf["note"]            # honest: NOT a probability of return


def test_confidence_is_low_on_empty_card():
    out = compute_confidence({})
    assert out["score"] <= 0.3 and out["level"] == "low"


def test_confidence_coherence_penalises_conflict():
    base_t = {"sma200": 1.0}
    coherent = {"risk": _CALM["risk"],
                "technical": {**base_t, "dials": {"trend": "downtrend", "momentum": "weak", "level": "near lows"}},
                "fundamental": {"latest_quarter": "2026-03-31",
                                "dials": {"earnings_growth": "declining", "margin_trend": "contracting"}}}
    conflict = {"risk": _CALM["risk"],
                "technical": {**base_t, "dials": {"trend": "uptrend", "momentum": "oversold", "level": "at highs"}},
                "fundamental": {"latest_quarter": "2026-03-31",
                                "dials": {"earnings_growth": "declining", "margin_trend": "expanding"}}}
    # same completeness + depth; coherent reads should be at least as confident as conflicting ones
    assert compute_confidence(coherent)["score"] >= compute_confidence(conflict)["score"]
