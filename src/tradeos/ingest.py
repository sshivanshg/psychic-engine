"""Ingest daily OHLCV candles for your holdings (and the benchmark) into the DB.

Run with:  uv run tradeos-ingest   (or)   uv run python -m tradeos.ingest

Idempotent: re-running re-fetches and UPSERTs, so you never get duplicate rows —
run it as often as you like (e.g. daily after market close).
"""

from collections.abc import Iterator

import pandas as pd

from .config import BENCHMARK, HISTORY_PERIOD, load_holdings
from .db import get_connection
from .log import get_logger
from .sources import DEFAULT_SOURCE

log = get_logger()

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

FUND_UPSERT_SQL = """
INSERT INTO fundamentals (symbol, period_end, total_revenue, operating_income, net_income, gross_profit)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (symbol, period_end) DO UPDATE SET
    total_revenue    = EXCLUDED.total_revenue,
    operating_income = EXCLUDED.operating_income,
    net_income       = EXCLUDED.net_income,
    gross_profit     = EXCLUDED.gross_profit,
    ingested_at      = now();
"""


def fetch_fundamentals(ticker: str) -> list[tuple]:
    """Quarterly income-statement rows from the price source. Best-effort: [] if unavailable."""
    q = DEFAULT_SOURCE.quarterly_fundamentals(ticker)
    if q is None or q.empty:
        return []

    def cell(row, col):
        if row in q.index:
            v = q.loc[row, col]
            return float(v) if pd.notna(v) else None
        return None

    rows = []
    for col in q.columns:
        period_end = col.date() if hasattr(col, "date") else col
        revenue = cell("Total Revenue", col)
        if revenue is None:
            continue  # skip quarters with no revenue
        rows.append((ticker, period_end, revenue, cell("Operating Income", col),
                     cell("Net Income", col), cell("Gross Profit", col)))
    return rows


def fetch_ohlcv(ticker: str, period: str) -> pd.DataFrame:
    """Fetch daily candles for one ticker as a clean lower-cased DataFrame.

    We store BOTH series, because risk and technicals want different adjustments:
      - `close`     = split-adjusted price (Yahoo 'Close')        → levels, value, technicals.
      - `adj_close` = total-return (Yahoo 'Adj Close': splits+div) → returns / risk.
    auto_adjust=False keeps them separate (auto_adjust=True would collapse close into adj_close).
    """
    df = DEFAULT_SOURCE.daily_ohlcv(ticker, period)
    if df.empty:
        return df
    # source returns lower-cased OHLCV + adj_close; a (possibly tz-aware) DatetimeIndex → date.
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


def ingest_symbols(tickers, *, with_benchmark: bool = False) -> int:
    """Fetch + UPSERT the given tickers (deduped). Incremental: ingest just what you pass in."""
    seen: list[str] = []
    for t in tickers:
        if t and t not in seen:
            seen.append(t)
    if with_benchmark and BENCHMARK and BENCHMARK not in seen:
        seen.append(BENCHMARK)
    if not seen:
        print("Nothing to ingest.")
        return 0

    print(f"Ingesting {len(seen)} ticker(s), period={HISTORY_PERIOD}\n")
    total = 0
    with get_connection() as conn, conn.cursor() as cur:
        for ticker in seen:
            df = fetch_ohlcv(ticker, HISTORY_PERIOD)
            if df.empty:
                print(f"  !  {ticker}: no data returned — check the symbol/suffix")
                continue
            cur.executemany(UPSERT_SQL, list(_rows(ticker, df)))
            conn.commit()  # commit per ticker so partial runs still persist
            total += len(df)
            msg = f"  ✓  {ticker}: {len(df)} rows"

            if ticker != BENCHMARK:  # the index has no fundamentals
                try:
                    frows = fetch_fundamentals(ticker)
                    if frows:
                        cur.executemany(FUND_UPSERT_SQL, frows)
                        conn.commit()
                        msg += f"  + {len(frows)} quarterly fundamentals"
                except Exception as e:  # noqa: BLE001 - fundamentals are best-effort
                    log.warning("fundamentals fetch failed for %s: %s", ticker, e)
                    msg += f"  (fundamentals skipped: {str(e)[:40]})"
            print(msg)

    print(f"\nDone. {total} price rows upserted.")
    return total


def ingest() -> None:
    """Refresh price data for the whole portfolio (+ benchmark)."""
    try:
        holdings = load_holdings()
    except (FileNotFoundError, ValueError):
        holdings = []
    if not holdings:
        print("No holdings yet. Add some:  tradeos add SYMBOL QTY [AVG_COST]")
        return
    ingest_symbols(holdings, with_benchmark=True)


if __name__ == "__main__":
    ingest()
