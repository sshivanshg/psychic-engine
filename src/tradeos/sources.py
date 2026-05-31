"""Price-data source adapters.

The whole system reads market data through this seam. Today it's yfinance (right tool for free
daily EOD on a handful of names); swapping to NSE bhavcopy or a paid feed (Kite/Upstox/Tiingo)
later is a new class here and a one-line change of `DEFAULT_SOURCE` — nothing else moves.
"""

from typing import Protocol

import pandas as pd


class PriceSource(Protocol):
    name: str

    def daily_ohlcv(self, ticker: str, period: str) -> pd.DataFrame:
        """Lower-cased columns: open/high/low/close/adj_close/volume; DatetimeIndex. Empty if none."""
        ...

    def quarterly_fundamentals(self, ticker: str):
        """Quarterly income statement (line-items × quarter-ends), or None if unavailable."""
        ...


class YFinanceSource:
    name = "yfinance"

    def daily_ohlcv(self, ticker: str, period: str) -> pd.DataFrame:
        import yfinance as yf
        df = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=False)
        if df.empty:
            return df
        df = df.rename(columns=str.lower).rename(columns={"adj close": "adj_close"})
        return df[["open", "high", "low", "close", "adj_close", "volume"]].dropna()

    def quarterly_fundamentals(self, ticker: str):
        import yfinance as yf
        q = yf.Ticker(ticker).quarterly_income_stmt
        return None if (q is None or q.empty) else q


DEFAULT_SOURCE: PriceSource = YFinanceSource()
