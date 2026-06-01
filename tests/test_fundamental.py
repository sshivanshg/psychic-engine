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
    calendar_yoy_pct,
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
    # a NEGATIVE base (loss-making prior year) makes a growth % meaningless — and would invert sign.
    assert _pct(50, -100) is None      # loss→profit turnaround must NOT read as a decline
    assert _pct(-200, -100) is None    # loss→bigger-loss off a negative base ⇒ undefined, not +100%
    # a negative `new` over a POSITIVE base is a real, correctly-signed decline and is kept.
    assert math.isclose(_pct(-50, 100), -150, rel_tol=1e-9)   # profit→loss = −150% (truly declining)


def test_calendar_yoy_pct_matches_agent_and_is_gap_robust():
    # 2024-09 missing on purpose. shift(4) would mis-pair 2025-03 with 2024-06 (a 9-month "YoY");
    # the calendar matcher pairs 2025-03 with 2024-03 (true YoY) or yields NaN when no match exists.
    pe = [dt.date(2024, 3, 31), dt.date(2024, 6, 30), dt.date(2024, 12, 31), dt.date(2025, 3, 31)]
    rev = [100.0, 110.0, 130.0, 150.0]
    yoy = calendar_yoy_pct(pe, rev)
    assert math.isnan(yoy.iloc[0]) and math.isnan(yoy.iloc[1])    # no year-ago row yet
    assert math.isnan(yoy.iloc[2])                                # 2024-12 has no 2023-12 base
    assert math.isclose(yoy.iloc[3], 50.0, rel_tol=1e-9)          # 2025-03 vs 2024-03 = +50%
    # the SHARED definition: eval's calendar_yoy_pct equals the agent's _year_ago + _pct, last quarter
    df = pd.DataFrame({"period_end": list(reversed(pe)), "total_revenue": list(reversed(rev))})
    agent_yoy = _pct(df.iloc[0]["total_revenue"], _year_ago(df, df.iloc[0])["total_revenue"])
    assert math.isclose(yoy.iloc[-1], agent_yoy, rel_tol=1e-9)


def test_calendar_yoy_pct_guards_negative_base():
    # prior-year earnings were a LOSS: a YoY % off that base is undefined (NaN), never a sign-flip.
    pe = [dt.date(2024, 3, 31), dt.date(2025, 3, 31)]
    ni = [-100.0, 50.0]                                            # loss → profit (a turnaround)
    yoy = calendar_yoy_pct(pe, ni)
    assert math.isnan(yoy.iloc[1])                                # not −150% / "declining"


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
