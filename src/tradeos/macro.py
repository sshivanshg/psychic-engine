"""Macro agent — sector exposure (the FACTS layer, pure Python).

Computes the book's SECTOR concentration from per-holding sector tags (ingested into `security_meta`
from yfinance). Descriptive only: "62% of the book sits in Technology" — never "rotate out".

Scope note (honest): the roadmap's macro agent is "FII/DII flows + sector exposure". Sector exposure
is the computable, reliable half and ships here. FII/DII flow needs a market-wide data feed (NSE/BSE
FII-DII activity) — like document fetching, a fragile scrape isn't worth bolting on for a personal
book until there's a dependable source, so it's deferred behind the same `DocumentSource`-style seam.
"""

import pandas as pd

from .config import SECTOR_CONCENTRATION_PCT, load_portfolio
from .db import get_connection

UNKNOWN = "Unknown"


def load_sectors(symbols) -> dict:
    """{symbol: sector} from security_meta; missing/unmapped symbols fall back to 'Unknown'."""
    if not symbols:
        return {}
    ph = ",".join(["%s"] * len(symbols))
    with get_connection() as c, c.cursor() as cur:
        cur.execute(f"SELECT symbol, sector FROM security_meta WHERE symbol IN ({ph})", list(symbols))
        found = {s: (sec or UNKNOWN) for s, sec in cur.fetchall()}
    return {s: found.get(s, UNKNOWN) for s in symbols}


def _round(x, n: int = 2):
    return round(float(x), n) if x is not None else None


def compute_macro(positions, close: pd.DataFrame, sectors: dict, flows: dict | None = None) -> dict:
    """Sector breakdown + concentration from current market-value weights. `flows` (market-wide
    FII/DII) is passed through when a source is configured, else surfaced as an honest 'no data'."""
    if close is None or close.empty:
        return {"by_symbol": {}, "portfolio": {}}
    last = close.ffill().iloc[-1]

    mv: dict[str, float] = {}
    for p in positions:
        lc = last.get(p.symbol)
        if p.symbol in close.columns and lc is not None and not pd.isna(lc) and p.quantity:
            mv[p.symbol] = float(lc) * p.quantity
    total = sum(mv.values())
    if not total:
        return {"by_symbol": {}, "portfolio": {}}

    sector_w: dict[str, float] = {}
    for sym, v in mv.items():
        sec = sectors.get(sym, UNKNOWN)
        sector_w[sec] = sector_w.get(sec, 0.0) + v / total * 100

    ordered = sorted(sector_w.items(), key=lambda x: x[1], reverse=True)
    shares = [w / 100 for w in sector_w.values()]
    hhi = sum(s * s for s in shares)
    known = {k: v for k, v in sector_w.items() if k != UNKNOWN}

    concentration = None
    if known:
        mx = max(known.values())
        concentration = ("high" if mx >= SECTOR_CONCENTRATION_PCT
                         else "moderate" if mx >= 25 else "low")

    # per-symbol: the share of the WHOLE BOOK that sits in this holding's sector (drives attention)
    by_symbol = {
        sym: {"sector": sectors.get(sym, UNKNOWN),
              "sector_weight_pct": _round(sector_w.get(sectors.get(sym, UNKNOWN)))}
        for sym in mv
    }

    return {
        "by_symbol": by_symbol,
        "portfolio": {
            "sectors": [{"sector": s, "weight_pct": _round(w)} for s, w in ordered],
            "top_sector": ordered[0][0] if ordered else None,
            "top_sector_pct": _round(ordered[0][1]) if ordered else None,
            "num_sectors": len(known),
            "effective_sectors": _round(1 / hhi) if hhi else None,
            "concentration": concentration,
            "unknown_pct": _round(sector_w.get(UNKNOWN, 0.0)),
            "flows": flows,                                  # market-wide FII/DII (None ⇒ no source)
            "flows_note": None if flows else "FII/DII flow: no source configured (deferred behind seam)",
        },
    }


def compute_all_macro(positions=None, *, close=None, sectors=None, flows=None) -> dict:
    """Sector exposure for the portfolio. `close`/`sectors`/`flows` injectable (shared context)."""
    if positions is None:
        positions = load_portfolio()
    symbols = [p.symbol for p in positions]
    if sectors is None:
        sectors = load_sectors(symbols)
    if close is None:
        from .risk import _load_panels
        close, _adj, _vol = _load_panels(symbols)
    if flows is None:
        from .sources import DEFAULT_FLOW_SOURCE
        flows = DEFAULT_FLOW_SOURCE.latest_flows()
    return compute_macro(positions, close, sectors, flows)
