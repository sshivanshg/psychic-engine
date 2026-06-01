"""Research swing (not production): does SECTOR-NEUTRALIZING the signals — or a COMBINED
composite — reveal stock-selection edge that the raw single-signal eval can't see?

A 106-name Indian cross-section is dominated by sector bets (Financials 24, Materials 16, ...).
A raw cross-sectional IC mixes "this stock vs its sector" with "this sector vs the market". Neutralizing
(de-meaning each signal WITHIN its sector, per date) isolates the pure selection component. We also test
an a-priori-signed composite (no fitted signs ⇒ no in-sample sign-picking).

Reuses tradeos' PURE functions (no look-ahead changes): _load_panels, SIGNALS, _cross_sectional_ic,
_newey_west_tstat, _tercile_spread, _net_spread, _p_two_sided. Standalone — does not touch eval.py.
"""

import numpy as np
import pandas as pd

from tradeos.config import COST_BPS, load_universe
from tradeos.db import get_connection
from tradeos.eval import (
    MIN_HISTORY,
    SIGNALS,
    _cross_sectional_ic,
    _net_spread,
    _newey_west_tstat,
    _p_two_sided,
    _tercile_spread,
)
from tradeos.risk import _load_panels

# Economic-prior signs (set BEFORE looking at this data ⇒ honest, no fitting):
#   momentum +, trend-following +, RSI mean-reversion − (high RSI ⇒ lower fwd), low-vol anomaly +.
COMPOSITE_SIGNS = {"momentum_3m": 1.0, "trend_vs_200sma": 1.0, "rsi_14": -1.0, "low_volatility": 1.0}


def load_sectors() -> dict:
    with get_connection() as c, c.cursor() as cur:
        cur.execute("SELECT symbol, sector FROM security_meta WHERE sector IS NOT NULL")
        return {s: sec for s, sec in cur.fetchall()}


def sector_neutralize(panel: pd.DataFrame, sectors: dict) -> pd.DataFrame:
    """De-mean each signal value within its sector, per date (row). NaN out names whose sector has <2
    members that day (can't neutralize a singleton)."""
    out = panel.copy() * np.nan
    col_sec = pd.Series({c: sectors.get(c, "UNKNOWN") for c in panel.columns})
    for _, cols in col_sec.groupby(col_sec):
        cset = list(cols.index)
        sub = panel[cset]
        out[cset] = sub.sub(sub.mean(axis=1), axis=0)  # within-sector cross-sectional residual
    return out


def _xs_z(panel: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional z-score per date (row)."""
    mu = panel.mean(axis=1)
    sd = panel.std(axis=1, ddof=0).replace(0, np.nan)
    return panel.sub(mu, axis=0).div(sd, axis=0)


def build_panels(adj: pd.DataFrame, symbols, horizon: int):
    """Per-signal trailing signal panels + one shared forward-return panel [date x symbol]."""
    fwd_cols, sig_panels = {}, {name: {} for name in SIGNALS}
    for sym in symbols:
        if sym not in adj.columns:
            continue
        s = adj[sym].dropna()
        if len(s) < MIN_HISTORY:
            continue
        fwd_cols[sym] = s.shift(-horizon) / s - 1
        for name, fn in SIGNALS.items():
            sig_panels[name][sym] = fn(s)
    fwd = pd.DataFrame(fwd_cols)
    sigs = {name: pd.DataFrame(cols) for name, cols in sig_panels.items()}
    return sigs, fwd


def score(sig: pd.DataFrame, fwd: pd.DataFrame, horizon: int, label: str) -> dict:
    nw_lag = max(1, horizon - 1)
    ics = _cross_sectional_ic(sig, fwd)
    n = len(ics)
    mean_ic = t = None
    if n >= 8:
        mean_ic, t = _newey_west_tstat(ics, nw_lag)
    spread = _tercile_spread(sig, fwd)
    net = _net_spread(spread, COST_BPS)
    return {
        "label": label, "n": n,
        "ic": mean_ic, "t": t, "p": _p_two_sided(t),
        "ls": (spread * 100) if spread is not None else None,
        "lsnet": (net * 100) if net is not None else None,
    }


def run(horizon: int):
    symbols = load_universe()
    _close, adj, _vol = _load_panels(symbols, None)
    sectors = load_sectors()
    universe = sorted(s for s in symbols if s in adj.columns)
    sigs, fwd = build_panels(adj, universe, horizon)

    rows = []
    # 1) each signal: RAW vs SECTOR-NEUTRAL
    for name in SIGNALS:
        rows.append(score(sigs[name], fwd, horizon, f"{name}"))
        rows.append(score(sector_neutralize(sigs[name], sectors), fwd, horizon, f"{name} ·SN"))

    # 2) a-priori-signed composite: sign × cross-sectional z, averaged across signals
    common_idx = fwd.index
    z_sum = pd.DataFrame(0.0, index=common_idx, columns=fwd.columns)
    cnt = pd.DataFrame(0.0, index=common_idx, columns=fwd.columns)
    for name, sgn in COMPOSITE_SIGNS.items():
        z = _xs_z(sigs[name]).reindex(index=common_idx, columns=fwd.columns)
        z_sum = z_sum.add(sgn * z, fill_value=0.0)
        cnt = cnt.add(z.notna().astype(float), fill_value=0.0)
    composite = z_sum / cnt.replace(0, np.nan)
    rows.append(score(composite, fwd, horizon, "COMPOSITE"))
    rows.append(score(sector_neutralize(composite, sectors), fwd, horizon, "COMPOSITE ·SN"))

    # Bonferroni floor over the distinct hypotheses tested here
    n_tests = len(rows)
    bonf = 0.05 / n_tests

    def f(x, d=3):
        return "  —  " if x is None else f"{x:>+.{d}f}"

    print(f"\n=== Sector-neutral & composite — {horizon}d forward · {len(universe)} names ===")
    print(f"  (·SN = sector-neutralized; Bonferroni α = 0.05/{n_tests} = {bonf:.4f} ⇒ need |t|≈{_t_for(bonf):.2f})")
    print(f"  {'variant':<20}{'n':>5}{'IC':>9}{'t':>7}{'p':>8}{'LS%':>8}{'LSnet%':>9}  flag")
    print("  " + "-" * 78)
    for r in rows:
        sig_raw = r["p"] is not None and r["p"] < 0.05
        sig_bonf = r["p"] is not None and r["p"] < bonf
        net_pos = r["lsnet"] is not None and r["lsnet"] > 0
        flag = ("**BONF+NET**" if (sig_bonf and net_pos) else "raw+net" if (sig_raw and net_pos)
                else "raw-sig" if sig_raw else "")
        print(f"  {r['label']:<20}{r['n']:>5}{f(r['ic'])}{f(r['t'],2):>7}"
              f"{('  —  ' if r['p'] is None else f'{r[chr(112)]:>.4f}'):>8}"
              f"{f(r['ls'],2):>8}{f(r['lsnet'],2):>9}  {flag}")


def _t_for(p_two_sided: float) -> float:
    """Inverse of erfc-based two-sided p → |t| (bisection; no scipy)."""
    import math
    lo, hi = 0.0, 8.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if math.erfc(mid / math.sqrt(2)) > p_two_sided:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


if __name__ == "__main__":
    for h in (21, 63):
        run(h)
