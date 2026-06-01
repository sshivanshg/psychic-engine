"""Data-coverage matrix — what has actually been fetched, per holding, across every table.

A risk desk needs to know its blind spots before it trusts a read. This crosses the raw ingest
tables (prices · fundamentals · sentiment · ownership · doc_chunks) and reports, per symbol, what's
present and how fresh it is — so "I never ingested news for this name" becomes a visible cell, not a
silent zero buried inside a dial. Pure read layer: one GROUP BY per table, no compute, no LLM.

Honesty (Prime Directives #2/#8): sentiment & ownership are CURRENT snapshots (eval-barred); a
missing cell is reported as absent, never back-filled with a fabricated value.
"""

from .db import get_connection


def _group(cur, sql: str, symbols: list[str]) -> dict:
    """Run a GROUP BY-over-symbols query and return {symbol: row-tuple-after-the-symbol}."""
    ph = ",".join(["%s"] * len(symbols))
    cur.execute(sql.format(ph=ph), list(symbols))
    return {r[0]: r[1:] for r in cur.fetchall()}


def data_coverage(symbols=None) -> list[dict]:
    """Per-symbol presence + freshness across all ingest tables. Defaults to the current holdings.

    Each row:
      symbol,
      price_rows, price_start, price_end,           # prices
      quarters, latest_quarter,                     # fundamentals
      news, latest_news,                            # sentiment  (snapshot · eval-barred)
      ownership_at,                                 # ownership  (snapshot · eval-barred)
      doc_sources, doc_chunks, latest_doc_period    # doc_chunks (RAG corpus)
    """
    if symbols is None:
        # Prefer the real book; degrade to the declared universe when holdings.csv is empty, so the
        # coverage map is still useful for exploration instead of raising on an empty portfolio.
        from .config import _safe_load, load_universe
        symbols = [p.symbol for p in _safe_load()] or load_universe()
    symbols = list(symbols)
    if not symbols:
        return []

    with get_connection() as c, c.cursor() as cur:
        prices = _group(cur, "SELECT symbol, count(*), min(date), max(date) FROM prices "
                             "WHERE symbol IN ({ph}) GROUP BY symbol", symbols)
        funds = _group(cur, "SELECT symbol, count(*), max(period_end) FROM fundamentals "
                            "WHERE symbol IN ({ph}) GROUP BY symbol", symbols)
        news = _group(cur, "SELECT symbol, count(*), max(published) FROM sentiment "
                           "WHERE symbol IN ({ph}) GROUP BY symbol", symbols)
        own = _group(cur, "SELECT symbol, max(snapshot_at) FROM ownership "
                          "WHERE symbol IN ({ph}) GROUP BY symbol", symbols)
        docs = _group(cur, "SELECT symbol, count(DISTINCT source), count(*), max(period) "
                           "FROM doc_chunks WHERE symbol IN ({ph}) GROUP BY symbol", symbols)

    def _d(v):
        return str(v)[:10] if v else None

    rows = []
    for s in symbols:
        p = prices.get(s)
        f = funds.get(s)
        n = news.get(s)
        o = own.get(s)
        d = docs.get(s)
        rows.append({
            "symbol": s,
            "price_rows": int(p[0]) if p else 0,
            "price_start": _d(p[1]) if p else None,
            "price_end": _d(p[2]) if p else None,
            "quarters": int(f[0]) if f else 0,
            "latest_quarter": _d(f[1]) if f else None,
            "news": int(n[0]) if n else 0,
            "latest_news": _d(n[1]) if n else None,
            "ownership_at": _d(o[0]) if o else None,
            "doc_sources": int(d[0]) if d else 0,
            "doc_chunks": int(d[1]) if d else 0,
            "latest_doc_period": _d(d[2]) if d else None,
        })
    return rows
