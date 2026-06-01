"""Ingest daily OHLCV candles for your holdings (and the benchmark) into the DB.

Run with:  uv run tradeos-ingest   (or)   uv run python -m tradeos.ingest

Idempotent: re-running re-fetches and UPSERTs, so you never get duplicate rows —
run it as often as you like (e.g. daily after market close).
"""

import math
from collections.abc import Iterator

import pandas as pd

from .config import BENCHMARK, HISTORY_PERIOD, load_universe
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

META_UPSERT_SQL = """
INSERT INTO security_meta (symbol, sector, industry, name)
VALUES (%s, %s, %s, %s)
ON CONFLICT (symbol) DO UPDATE SET
    sector = EXCLUDED.sector, industry = EXCLUDED.industry, name = EXCLUDED.name, ingested_at = now();
"""

SENT_UPSERT_SQL = """
INSERT INTO sentiment (symbol, title, publisher, published, polarity)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (symbol, title) DO UPDATE SET
    publisher = EXCLUDED.publisher, published = EXCLUDED.published,
    polarity = EXCLUDED.polarity, ingested_at = now();
"""

OWN_UPSERT_SQL = """
INSERT INTO ownership (symbol, held_pct_institutions, held_pct_insiders, n_institutions, snapshot_at)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (symbol) DO UPDATE SET
    held_pct_institutions = EXCLUDED.held_pct_institutions,
    held_pct_insiders     = EXCLUDED.held_pct_insiders,
    n_institutions        = EXCLUDED.n_institutions,
    snapshot_at           = EXCLUDED.snapshot_at, ingested_at = now();
"""

# Append-only price revision log (reproducibility). CURRENT_DATE = the ingest day we observed these
# values; DO NOTHING dedups same-day re-runs, so re-ingesting is still idempotent.
VINTAGE_INSERT_SQL = """
INSERT INTO price_vintages (symbol, date, vintage_date, close, adj_close, volume)
VALUES (%s, %s, CURRENT_DATE, %s, %s, %s)
ON CONFLICT (symbol, date, vintage_date) DO NOTHING;
"""


def _parse_published(v):
    """yfinance gives either an epoch int or an ISO string (schema varies). Return a datetime or None."""
    import datetime as dt
    if isinstance(v, (int, float)):
        try:
            return dt.datetime.fromtimestamp(int(v), tz=dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(v, str):
        try:
            return dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def fetch_sentiment_rows(ticker: str) -> list[tuple]:
    """News headlines scored to polarity rows. Best-effort: [] if the feed is empty/unavailable."""
    from .sentiment import score_text
    rows = []
    for art in DEFAULT_SOURCE.news(ticker):
        title = (art.get("title") or "").strip()
        if not title:
            continue
        rows.append((ticker, title[:500], art.get("publisher"),
                     _parse_published(art.get("published")), score_text(title)))
    return rows


def fetch_ownership_row(ticker: str) -> tuple | None:
    """Current institutional/insider holding snapshot, or None if the source has nothing usable."""
    import datetime as dt
    o = DEFAULT_SOURCE.ownership(ticker)
    if o.get("held_pct_institutions") is None and o.get("held_pct_insiders") is None:
        return None
    return (ticker, o.get("held_pct_institutions"), o.get("held_pct_insiders"),
            o.get("n_institutions"), dt.datetime.now(dt.timezone.utc))


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


def _px_eq(x, y) -> bool:
    """Equal up to float noise (vendor re-fetch returns identical bits absent a real restatement)."""
    if x is None or y is None:
        return x is y
    return math.isclose(float(x), float(y), rel_tol=1e-9, abs_tol=1e-12)


def _changed_vintage_rows(cur, ticker: str, df: pd.DataFrame) -> list[tuple]:
    """Rows the vendor RESTATED (or new trading days) vs the latest stored `prices` — the revision set
    to append to price_vintages. MUST run BEFORE the `prices` UPSERT overwrites the old values. Empty
    on a no-change re-ingest; the full history on the first ingest (when `prices` is empty for the
    ticker), so every (symbol, date) is logged at least once and an `as_of` replay is reconstructable.
    """
    cur.execute("SELECT date, close, adj_close, volume FROM prices WHERE symbol=%s", (ticker,))
    old = {d: (c, a, v) for d, c, a, v in cur.fetchall()}
    changed: list[tuple] = []
    for day, row in df.iterrows():
        vol = int(row["volume"]) if pd.notna(row["volume"]) else None
        new = (float(row["close"]), float(row["adj_close"]), vol)
        prev = old.get(day)
        if prev is None or not (_px_eq(prev[0], new[0]) and _px_eq(prev[1], new[1]) and prev[2] == new[2]):
            changed.append((ticker, day, new[0], new[1], new[2]))
    return changed


def _rows(ticker: str, df: pd.DataFrame) -> Iterator[tuple]:
    """Turn a DataFrame into the tuples the UPSERT statement expects.

    Volume is None-safe: a NaN volume (halt days; or a source that doesn't drop them) would make
    `int(nan)` raise and kill the whole ticker mid-ingest — the column is nullable, so degrade to None.
    """
    for day, row in df.iterrows():
        vol = row["volume"]
        yield (
            ticker,
            day,
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
            float(row["adj_close"]),
            int(vol) if pd.notna(vol) else None,
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
            vintage = _changed_vintage_rows(cur, ticker, df)   # diff vs old prices BEFORE the upsert
            cur.executemany(UPSERT_SQL, list(_rows(ticker, df)))
            if vintage:
                cur.executemany(VINTAGE_INSERT_SQL, vintage)   # append-only revision log
            conn.commit()  # commit per ticker so partial runs still persist
            total += len(df)
            msg = f"  ✓  {ticker}: {len(df)} rows"
            if vintage:
                msg += f"  + {len(vintage)} vintage rows"

            if ticker != BENCHMARK:  # the index has no fundamentals / sector
                try:
                    frows = fetch_fundamentals(ticker)
                    if frows:
                        cur.executemany(FUND_UPSERT_SQL, frows)
                        conn.commit()
                        msg += f"  + {len(frows)} quarterly fundamentals"
                except Exception as e:  # noqa: BLE001 - fundamentals are best-effort
                    log.warning("fundamentals fetch failed for %s: %s", ticker, e)
                    msg += f"  (fundamentals skipped: {str(e)[:40]})"
                try:
                    m = DEFAULT_SOURCE.security_meta(ticker)
                    if m.get("sector") or m.get("name"):
                        cur.execute(META_UPSERT_SQL,
                                    (ticker, m.get("sector"), m.get("industry"), m.get("name")))
                        conn.commit()
                        if m.get("sector"):
                            msg += f"  + sector {m['sector']}"
                except Exception as e:  # noqa: BLE001 - sector meta is best-effort
                    log.warning("security meta fetch failed for %s: %s", ticker, e)
                try:
                    srows = fetch_sentiment_rows(ticker)
                    if srows:
                        cur.executemany(SENT_UPSERT_SQL, srows)
                        conn.commit()
                        msg += f"  + {len(srows)} headlines"
                except Exception as e:  # noqa: BLE001 - news sentiment is best-effort
                    log.warning("news fetch failed for %s: %s", ticker, e)
                try:
                    orow = fetch_ownership_row(ticker)
                    if orow:
                        cur.execute(OWN_UPSERT_SQL, orow)
                        conn.commit()
                        msg += "  + ownership"
                except Exception as e:  # noqa: BLE001 - ownership snapshot is best-effort
                    log.warning("ownership fetch failed for %s: %s", ticker, e)
            print(msg)

    print(f"\nDone. {total} price rows upserted.")
    return total


def ingest() -> None:
    """Refresh price data for the whole portfolio + the back-test universe (+ benchmark).

    The universe (holdings ∪ universe.csv) is ingested so the eval cross-section can include names
    you've sold or that delisted — the survivorship fix needs their price history present."""
    try:
        universe = load_universe()
    except (FileNotFoundError, ValueError):
        universe = []
    if not universe:
        print("No holdings yet. Add some:  tradeos add SYMBOL QTY [AVG_COST]")
        return
    ingest_symbols(universe, with_benchmark=True)


if __name__ == "__main__":
    ingest()
