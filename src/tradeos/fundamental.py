"""Fundamental Agent — structured quarterly financials (the FACTS layer), read from the DB.

Computes only RATIOS — growth % and margins — never absolute revenue across tickers, because
yfinance reports in the company's own currency (₹ for most NSE names, USD for ADR-listed ones like
INFY). Ratios are currency-invariant, so per-ticker growth/margin signals stay valid.

Descriptive only: "revenue growing 12% YoY, margins expanding" — never "buy".

CAVEAT (for the Phase-4 eval harness): point-in-time here is approximate — quarters are filtered by
`period_end`, but the numbers were only *known* ~30-45 days later (results announcement). That
announcement lag is a look-ahead to handle properly when we back-test fundamental signals.
"""

import pandas as pd

from .config import load_portfolio
from .db import get_connection


def _pct(new, old):
    if new is None or old is None or old == 0:
        return None
    return (new / old - 1) * 100


def _bucket_growth(p):
    if p is None:
        return None
    return "strong" if p >= 15 else "growing" if p >= 5 else "flat" if p >= -5 else "declining"


def _round1(x):
    return round(x, 1) if x is not None else None


def _load(symbol, as_of=None) -> pd.DataFrame:
    sql = ("SELECT period_end, total_revenue, operating_income, net_income, gross_profit "
           "FROM fundamentals WHERE symbol = %s")
    params: list = [symbol]
    if as_of is not None:
        sql += " AND period_end <= %s"
        params.append(as_of)
    sql += " ORDER BY period_end DESC"
    with get_connection() as c, c.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=["period_end", "total_revenue", "operating_income",
                                       "net_income", "gross_profit"])


def _year_ago(df: pd.DataFrame, latest):
    """The row one calendar year before `latest` (match by year-1 + month, robust to gaps)."""
    ty, tm = latest["period_end"].year - 1, latest["period_end"].month
    for _, r in df.iterrows():
        if r["period_end"].year == ty and r["period_end"].month == tm:
            return r
    return None


def load_fundamentals(symbols, as_of=None) -> dict:
    """Bulk-load quarterly fundamentals for many symbols in ONE query → {symbol: df (desc)}.

    Replaces the old per-symbol query (which opened a connection per holding)."""
    if not symbols:
        return {}
    placeholders = ",".join(["%s"] * len(symbols))
    sql = ("SELECT symbol, period_end, total_revenue, operating_income, net_income, gross_profit "
           f"FROM fundamentals WHERE symbol IN ({placeholders})")
    params: list = list(symbols)
    if as_of is not None:
        sql += " AND period_end <= %s"
        params.append(as_of)
    sql += " ORDER BY symbol, period_end DESC"
    with get_connection() as c, c.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    cols = ["symbol", "period_end", "total_revenue", "operating_income", "net_income", "gross_profit"]
    big = pd.DataFrame(rows, columns=cols)
    return {sym: g.drop(columns="symbol").reset_index(drop=True) for sym, g in big.groupby("symbol")}


def _compute_from_df(df) -> dict | None:
    if df is None or len(df) < 2:
        return None

    latest, prev = df.iloc[0], df.iloc[1]
    ya = _year_ago(df, latest)
    rev, ni, opi = latest["total_revenue"], latest["net_income"], latest["operating_income"]

    net_margin = (ni / rev * 100) if (rev and ni is not None) else None
    op_margin = (opi / rev * 100) if (rev and opi is not None) else None
    rev_yoy = _pct(rev, ya["total_revenue"]) if ya is not None else None
    ni_yoy = _pct(ni, ya["net_income"]) if ya is not None else None
    margin_ya = (ya["net_income"] / ya["total_revenue"] * 100) if (ya is not None and ya["total_revenue"]) else None
    margin_change = (net_margin - margin_ya) if (net_margin is not None and margin_ya is not None) else None

    trend = None
    if margin_change is not None:
        trend = "expanding" if margin_change > 0.5 else "contracting" if margin_change < -0.5 else "stable"

    return {
        "latest_quarter": str(latest["period_end"]),
        "revenue_yoy_pct": _round1(rev_yoy),
        "revenue_qoq_pct": _round1(_pct(rev, prev["total_revenue"])),
        "net_income_yoy_pct": _round1(ni_yoy),
        "net_margin_pct": _round1(net_margin),
        "op_margin_pct": _round1(op_margin),
        "net_margin_change_pp": _round1(margin_change),
        "dials": {
            "revenue_growth": _bucket_growth(rev_yoy),
            "earnings_growth": _bucket_growth(ni_yoy),
            "margin_trend": trend,
        },
    }


def compute_fundamental(symbol, as_of=None) -> dict | None:
    return _compute_from_df(_load(symbol, as_of))


def compute_all_fundamental(as_of=None, *, fundamentals=None, positions=None) -> dict:
    """Per-symbol fundamental reads for every holding that has quarterly data ingested.
    `fundamentals`/`positions` can be injected (shared AnalysisContext) to avoid re-querying."""
    if positions is None:
        positions = load_portfolio()
    symbols = [p.symbol for p in positions]
    if fundamentals is None:
        fundamentals = load_fundamentals(symbols, as_of)
    out = {}
    for sym in symbols:
        f = _compute_from_df(fundamentals.get(sym))
        if f:
            out[sym] = f
    return out
