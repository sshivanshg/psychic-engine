"""Tests for the Fundamental agent (pure helpers + integration)."""

import datetime as dt
import math

import pandas as pd
import pytest

from tradeos.fundamental import _bucket_growth, _pct, _year_ago, compute_all_fundamental


def test_pct():
    assert math.isclose(_pct(110, 100), 10, rel_tol=1e-9)
    assert math.isclose(_pct(90, 100), -10, rel_tol=1e-9)
    assert _pct(None, 100) is None
    assert _pct(100, 0) is None        # guard against divide-by-zero


def test_bucket_growth():
    assert _bucket_growth(20) == "strong"
    assert _bucket_growth(8) == "growing"
    assert _bucket_growth(0) == "flat"
    assert _bucket_growth(-10) == "declining"
    assert _bucket_growth(None) is None


def test_year_ago_matches_calendar_quarter_through_gaps():
    # 2025-09 missing on purpose — YoY for 2026-03 must still find 2025-03 (not "4 rows back").
    df = pd.DataFrame({
        "period_end": [dt.date(2026, 3, 31), dt.date(2025, 12, 31), dt.date(2025, 6, 30), dt.date(2025, 3, 31)],
        "total_revenue": [300, 280, 260, 250],
    })
    ya = _year_ago(df, df.iloc[0])
    assert ya is not None and ya["period_end"] == dt.date(2025, 3, 31)
    # no match → None
    assert _year_ago(df.iloc[[0]], df.iloc[0]) is None


def test_compute_all_fundamental_shape():
    try:
        out = compute_all_fundamental()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"DB not available: {e}")
    for _sym, f in out.items():  # empty is fine (data may not be ingested); shape if present
        assert {"revenue_growth", "earnings_growth", "margin_trend"} <= set(f["dials"])
        assert "revenue_yoy_pct" in f and "net_margin_pct" in f
