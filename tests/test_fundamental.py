"""Tests for the Fundamental agent (pure helpers + integration)."""

import datetime as dt
import math

import pandas as pd
import pytest

from tradeos.config import ANNOUNCEMENT_LAG_DAYS
from tradeos.fundamental import (
    _availability_cutoff,
    _bucket_growth,
    _pct,
    _year_ago,
    compute_all_fundamental,
    load_fundamentals,
)


def test_availability_cutoff_applies_announcement_lag():
    # Point-in-time read: a quarter is only visible once results were public (period_end + lag).
    # So the query cutoff must be as_of - lag, and None (no filter) when as_of is None.
    assert _availability_cutoff(None) is None
    as_of = dt.date(2026, 5, 31)
    assert _availability_cutoff(as_of) == as_of - dt.timedelta(days=ANNOUNCEMENT_LAG_DAYS)
    # accepts an ISO string and a datetime, not just a date
    assert _availability_cutoff("2026-05-31") == as_of - dt.timedelta(days=ANNOUNCEMENT_LAG_DAYS)
    assert _availability_cutoff(dt.datetime(2026, 5, 31, 9, 30)) == as_of - dt.timedelta(days=ANNOUNCEMENT_LAG_DAYS)


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


def test_load_fundamentals_respects_announcement_lag():
    """Invariant (Prime Directive #2): a quarter must NOT be visible until period_end + lag <= as_of,
    and must reappear once enough time has passed. Locks the look-ahead fix at the query level."""
    try:
        full = load_fundamentals(["INFY.NS"])           # as_of=None ⇒ no filter (all quarters)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"DB not available: {e}")
    df = full.get("INFY.NS")
    if df is None or df.empty:
        pytest.skip("no INFY.NS fundamentals ingested")
    latest_pe = df.iloc[0]["period_end"]                # df is period_end DESC

    # 10 days after quarter-end — inside the announcement lag — the quarter is not yet public.
    early = load_fundamentals(["INFY.NS"], as_of=latest_pe + dt.timedelta(days=10))
    early_df = early.get("INFY.NS")
    assert early_df is None or latest_pe not in list(early_df["period_end"])

    # Well past the lag, it is public again.
    late = load_fundamentals(["INFY.NS"], as_of=latest_pe + dt.timedelta(days=ANNOUNCEMENT_LAG_DAYS + 5))
    assert "INFY.NS" in late and latest_pe in list(late["INFY.NS"]["period_end"])
