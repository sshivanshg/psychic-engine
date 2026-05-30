"""Ingest daily OHLCV candles for your holdings (and the benchmark) into the DB.

Run with:  uv run tradeos-ingest   (or)   uv run python -m tradeos.ingest

Idempotent: re-running re-fetches and UPSERTs, so you never get duplicate rows —
run it as often as you like (e.g. daily after market close).
"""

from collections.abc import Iterator

import pandas as pd
import yfinance as yf

from .config import BENCHMARK, HISTORY_PERIOD, load_holdings
from .db import get_connection

# INSERT, but if (symbol, date) already exists, overwrite it instead of erroring.
# This single statement is what makes the whole pipeline safe to re-run.
UPSERT_SQL = """
INSERT INTO prices (symbol, date, open, high, low, close, adj_close, volume)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (symbol, date) DO UPDATE SET
    open        = EXCLUDED.open,
    high        = EXCLUDED.high,
    low         = EXCLUDED.low,
    close       = EXCLUDED.close,
    adj_close   = EXCLUDED.adj_close,
    volume      = EXCLUDED.volume,
    ingested_at = now();
"""


def fetch_ohlcv(ticker: str, period: str) -> pd.DataFrame:
    """Fetch daily candles for one ticker as a clean lower-cased DataFrame.

    We store BOTH series, because risk and technicals want different adjustments:
      - `close`     = split-adjusted price (Yahoo 'Close')        → levels, value, technicals.
      - `adj_close` = total-return (Yahoo 'Adj Close': splits+div) → returns / risk.
    auto_adjust=False keeps them separate (auto_adjust=True would collapse close into adj_close).
    """
    df = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=False)
    if df.empty:
        return df

    df = df.rename(columns=str.lower).rename(columns={"adj close": "adj_close"})
    df = df[["open", "high", "low", "close", "adj_close", "volume"]].dropna()
    # yfinance gives a (possibly tz-aware) DatetimeIndex; we only care about the date.
    df.index = pd.to_datetime(df.index).date
    return df


def _rows(ticker: str, df: pd.DataFrame) -> Iterator[tuple]:
    """Turn a DataFrame into the tuples the UPSERT statement expects."""
    for day, row in df.iterrows():
        yield (
            ticker,
            day,
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
            float(row["adj_close"]),
            int(row["volume"]),
        )


def ingest() -> None:
    tickers = load_holdings()
    # Always pull the benchmark too — the Risk Agent needs it to compute beta.
    if BENCHMARK and BENCHMARK not in tickers:
        tickers = tickers + [BENCHMARK]

    print(f"Ingesting {len(tickers)} ticker(s) (incl. benchmark {BENCHMARK}), period={HISTORY_PERIOD}\n")

    total = 0
    with get_connection() as conn, conn.cursor() as cur:
        for ticker in tickers:
            df = fetch_ohlcv(ticker, HISTORY_PERIOD)
            if df.empty:
                print(f"  !  {ticker}: no data returned — check the symbol/suffix")
                continue

            cur.executemany(UPSERT_SQL, list(_rows(ticker, df)))
            conn.commit()  # commit per ticker so partial runs still persist
            total += len(df)
            print(f"  ✓  {ticker}: {len(df)} rows")

    print(f"\nDone. {total} rows upserted. Verify with:  uv run tradeos-check")


if __name__ == "__main__":
    ingest()
