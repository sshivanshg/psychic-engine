"""Tests for the Sentiment agent — lexicon scoring + aggregation (pure, no DB)."""

from tradeos.sentiment import compute_sentiment, score_text


def test_score_text_polarity_bounds_and_sign():
    assert score_text("Company beats estimates, profit surges to a record") > 0
    assert score_text("Stock plunges on fraud probe and analyst downgrade") < 0
    assert score_text("Company holds its annual general meeting today") == 0.0   # no lexicon hit
    assert -1.0 <= score_text("beats guidance but warns on margins") <= 1.0
    assert score_text("") == 0.0


def test_compute_sentiment_label_thresholds_and_nodata():
    assert compute_sentiment("X", articles=[]) is None                          # honest no-data
    pos = compute_sentiment("X", articles=[{"polarity": 0.8, "published": None},
                                           {"polarity": 0.6, "published": None}])
    assert pos["label"] == "positive" and pos["n_articles"] == 2
    neutral = compute_sentiment("X", articles=[{"polarity": 0.1, "published": None},
                                               {"polarity": -0.1, "published": None}])
    assert neutral["label"] == "neutral"
    neg = compute_sentiment("X", articles=[{"polarity": -0.5, "published": None}])
    assert neg["label"] == "negative" and neg["dials"]["news_flow"] == "negative"
    assert "barred from the eval" in neg["note"]                                 # eval-barred, stated
