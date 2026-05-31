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

    def security_meta(self, ticker: str) -> dict:
        """Sector / industry / long name for one ticker, or {} if unavailable."""
        ...

    def news(self, ticker: str) -> list[dict]:
        """Recent headlines: [{title, publisher, published}]. CURRENT snapshot only (no history)."""
        ...

    def ownership(self, ticker: str) -> dict:
        """Institutional / insider holding fractions (0-1). CURRENT snapshot only (no history)."""
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

    def security_meta(self, ticker: str) -> dict:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        return {
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "name": info.get("longName") or info.get("shortName"),
        }

    def news(self, ticker: str) -> list[dict]:
        import yfinance as yf
        try:
            items = yf.Ticker(ticker).news or []
        except Exception:  # noqa: BLE001 - news is best-effort; never break ingestion
            return []
        out: list[dict] = []
        for it in items:
            # yfinance's news schema shifts between versions; the newer one nests under `content`.
            c = it.get("content") if isinstance(it.get("content"), dict) else it
            title = c.get("title") or it.get("title")
            if not title:
                continue
            prov = c.get("provider")
            publisher = prov.get("displayName") if isinstance(prov, dict) else it.get("publisher")
            published = it.get("providerPublishTime") or c.get("pubDate") or c.get("displayTime")
            out.append({"title": title, "publisher": publisher, "published": published})
        return out

    def ownership(self, ticker: str) -> dict:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        return {
            "held_pct_institutions": info.get("heldPercentInstitutions"),
            "held_pct_insiders": info.get("heldPercentInsiders"),
            "n_institutions": None,   # .info doesn't reliably carry a holder count
        }


class MarketFlowSource(Protocol):
    """The market-wide FII/DII flow seam (the other half of the macro/ownership agent).

    There is no free, reliable, point-in-time FII/DII feed, so the default returns None ("no source
    configured") and the macro agent degrades honestly — never a fragile scrape passed off as data.
    Wire a real adapter (NSE/BSE activity, a paid feed) here and the macro agent picks it up.
    """

    name: str

    def latest_flows(self) -> dict | None:
        ...


class NullFlowSource:
    name = "none"

    def latest_flows(self) -> dict | None:
        return None


DEFAULT_SOURCE: PriceSource = YFinanceSource()
DEFAULT_FLOW_SOURCE: MarketFlowSource = NullFlowSource()
