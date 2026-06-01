"""Ownership agent — institutional / insider holding read (the FACTS layer, pure compute).

Reads the per-holding ownership snapshot (yfinance `heldPercentInstitutions` / `heldPercentInsiders`)
stored in the `ownership` table. Descriptive only: "63% institutional, 18% insider" — never "buy".

HONESTY (Prime Directives #2, #8): yfinance exposes only a CURRENT ownership snapshot (no history),
so this is descriptive-of-NOW — NOT reconstructable point-in-time, and therefore **barred from the
eval harness**. Missing fields degrade to None ("no data"), never a fabricated number. The market-wide
FII/DII *flow* (the other half of the roadmap's ownership/flow agent) needs a market feed and stays
deferred behind the `MarketFlowSource` seam in sources.py.
"""

import datetime as dt

from .db import get_connection


def _pct(x):
    return round(float(x) * 100, 1) if x is not None else None


def _institutional_dial(inst_pct):
    if inst_pct is None:
        return None
    return "high" if inst_pct >= 50 else "moderate" if inst_pct >= 20 else "low"


def load_ownership(symbols, as_of=None) -> dict:
    """{symbol: row dict} from the ownership table.

    Point-in-time (Prime Directive #2): the table holds a single CURRENT snapshot per symbol (yfinance
    has no history), stamped with `snapshot_at`. With `as_of` set we only return a snapshot that was
    already taken by then (`snapshot_at <= as_of`); a snapshot taken today is therefore correctly
    INVISIBLE on a historical replay, instead of leaking present-day ownership into a past card. A
    NULL `snapshot_at` has unknown availability ⇒ excluded under `as_of` (honest gap, never a leak).
    With `as_of=None` (live) every snapshot is eligible."""
    if not symbols:
        return {}
    ph = ",".join(["%s"] * len(symbols))
    sql = (f"SELECT symbol, held_pct_institutions, held_pct_insiders, n_institutions, snapshot_at "
           f"FROM ownership WHERE symbol IN ({ph})")
    params: list = list(symbols)
    if as_of is not None:
        cutoff = as_of if isinstance(as_of, dt.date) else dt.date.fromisoformat(str(as_of)[:10])
        sql += " AND snapshot_at IS NOT NULL AND snapshot_at <= %s"
        params.append(cutoff)
    with get_connection() as c, c.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return {r[0]: {"held_pct_institutions": r[1], "held_pct_insiders": r[2],
                   "n_institutions": r[3], "snapshot_at": r[4]} for r in rows}


def compute_ownership(symbol: str, *, row=None) -> dict | None:
    r = load_ownership([symbol]).get(symbol) if row is None else row
    if not r:
        return None
    inst = _pct(r.get("held_pct_institutions"))
    ins = _pct(r.get("held_pct_insiders"))
    if inst is None and ins is None:
        return None
    return {
        "institutional_pct": inst,
        "insider_pct": ins,
        "n_institutions": r.get("n_institutions"),
        "dials": {"institutional": _institutional_dial(inst)},
        "note": "current ownership snapshot — descriptive, not point-in-time; barred from the eval harness",
    }


def compute_all_ownership(as_of=None, *, ownership=None, positions=None) -> dict:
    """Per-symbol ownership read. With `as_of` set, only a snapshot taken by then is used (a current
    snapshot is invisible on a historical replay — see `load_ownership`), so ownership can't leak
    present-day data into a past card's attention/confidence. `ownership`/`positions` injectable."""
    if positions is None:
        from .config import load_portfolio
        positions = load_portfolio()
    symbols = [p.symbol for p in positions]
    if ownership is None:
        ownership = load_ownership(symbols, as_of)
    out = {}
    for s in symbols:
        c = compute_ownership(s, row=ownership.get(s))
        if c is not None:
            out[s] = c
    return out
