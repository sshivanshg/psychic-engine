"""Phase B — the discipline layer: risk-budgeted sizing + a cost/turnover guard.

The eval proved there's no bankable signal edge, so this is where a personal book actually compounds:
(1) stop one name / one factor from quietly owning your risk, and (2) never pay to trade unless the
risk reduction is worth the cost. DESCRIPTIVE only (Prime Directive #1): it shows you the risk-
equalizing weights and the implied trades + their cost — you make the call.

Reuses the PRODUCTION risk internals (same EWMA λ=0.94 + Ledoit-Wolf covariance the risk engine uses)
so the numbers reconcile with `tradeos risk`. Equal-Risk-Contribution solved by cyclical coordinate
descent (Griveau-Billion et al. 2013) — fast, long-only, no scipy.
"""

import numpy as np

from tradeos.config import COST_BPS, Position, RISK_LIMITS, _safe_load
from tradeos.risk import (
    DAYS_PER_YEAR,
    _ewma_cov,
    _ledoit_wolf_shrink,
    _load_panels,
)

# Illustrative book used ONLY when holdings.csv is empty — real prices/covariance from the DB, just
# placeholder quantities (deliberately tech-heavy so ERC has something to fix). Replace with your book.
ILLUSTRATIVE = [
    Position("HDFCBANK.NS", 60, None),
    Position("INFY.NS", 120, None),
    Position("ITC.NS", 300, None),
    Position("RELIANCE.NS", 40, None),
    Position("TCS.NS", 50, None),
]


def erc_weights(cov: np.ndarray, iters: int = 5000, tol: float = 1e-12) -> np.ndarray:
    """Long-only Equal-Risk-Contribution weights via cyclical coordinate descent. Each asset gets an
    equal share of portfolio risk: w_i·(Σw)_i equal across i."""
    n = cov.shape[0]
    b = np.ones(n) / n                         # equal risk budgets
    w = np.ones(n) / n
    for _ in range(iters):
        w_prev = w.copy()
        for i in range(n):
            c_i = cov[i] @ w - cov[i, i] * w[i]   # Σ_{j≠i} σ_ij w_j
            w[i] = (-c_i + np.sqrt(c_i * c_i + 4 * cov[i, i] * b[i])) / (2 * cov[i, i])
        w /= w.sum()
        if np.max(np.abs(w - w_prev)) < tol:
            break
    return w


def pct_risk_contrib(w: np.ndarray, cov: np.ndarray) -> np.ndarray:
    """Each name's % of total portfolio variance-risk (sums to 100)."""
    sig_p = float(np.sqrt(w @ cov @ w))
    if sig_p == 0:
        return np.full_like(w, np.nan)
    mrc = cov @ w
    return (w * mrc) / (sig_p * sig_p) * 100


def main() -> None:
    positions = _safe_load()
    book_label = "your holdings.csv"
    if not positions:
        positions = ILLUSTRATIVE
        book_label = "ILLUSTRATIVE book (holdings.csv empty — real covariance, placeholder qtys)"

    symbols = [p.symbol for p in positions]
    close, adj, _vol = _load_panels(symbols, None)
    adj_pos = adj.where(adj > 0)
    log_ret = np.log(adj_pos).diff()
    last_close = close.ffill().iloc[-1]

    # market-value weights (real prices), aligned to names that actually have data
    mv = {p.symbol: float(last_close[p.symbol]) * p.quantity
          for p in positions if p.symbol in close.columns and p.quantity}
    syms = [s for s in symbols if s in mv]
    total = sum(mv[s] for s in syms)
    w_cur = np.array([mv[s] / total for s in syms])

    # same covariance the risk engine builds (EWMA λ=0.94 + Ledoit-Wolf shrinkage)
    cov_df = _ewma_cov(log_ret[syms])
    cov = cov_df.loc[syms, syms].to_numpy()
    cov, _delta = _ledoit_wolf_shrink(cov, log_ret[syms].dropna(how="any").to_numpy())

    w_erc = erc_weights(cov)

    af = np.sqrt(DAYS_PER_YEAR)
    vol_cur = float(np.sqrt(w_cur @ cov @ w_cur)) * af * 100
    vol_erc = float(np.sqrt(w_erc @ cov @ w_erc)) * af * 100
    rc_cur = pct_risk_contrib(w_cur, cov)
    rc_erc = pct_risk_contrib(w_erc, cov)

    # rebalance cost: each leg (buy or sell) pays COST_BPS; Σ|Δw| = total traded fraction (both legs)
    dw = w_erc - w_cur
    traded_frac = float(np.abs(dw).sum())
    cost_frac = traded_frac * COST_BPS / 1e4
    cost_rupees = cost_frac * total

    print(f"\n=== Risk-budgeted sizing — {book_label} ===")
    print(f"  book value ₹{total:,.0f} · cost {COST_BPS:.0f} bps/leg · cov = EWMA(0.94)+Ledoit-Wolf\n")
    print(f"  {'name':<14}{'weight%':>9}{'→ERC%':>8}{'  ':>2}{'risk%':>8}{'→ERC%':>8}{'Δweight%':>10}")
    print("  " + "-" * 64)
    for i, s in enumerate(syms):
        print(f"  {s:<14}{w_cur[i]*100:>8.1f}{w_erc[i]*100:>8.1f}  "
              f"{rc_cur[i]:>8.1f}{rc_erc[i]:>8.1f}{dw[i]*100:>+10.1f}")
    print("  " + "-" * 64)
    print(f"  {'concentration':<14}{'':>8}{'':>8}  "
          f"top={rc_cur.max():>5.0f}%  →{rc_erc.max():>4.0f}%   (ERC target ≈ {100/len(syms):.0f}% each)")
    print(f"\n  annual vol:   current {vol_cur:.1f}%   →   ERC {vol_erc:.1f}%   "
          f"(limit {RISK_LIMITS['max_annual_vol_pct']:.0f}%)")
    print(f"  rebalance cost to reach ERC:  {traded_frac*100:.1f}% turnover  ·  "
          f"{cost_frac*1e4:.0f} bps  ·  ₹{cost_rupees:,.0f}")

    # the cost guard: with NO proven alpha, only risk reduction justifies the spend
    top_drop = rc_cur.max() - rc_erc.max()
    verdict = ("WORTH IT — meaningfully de-concentrates risk for the cost" if top_drop >= 10
               else "MARGINAL — small risk change for the cost; only act if a limit is breached"
               if top_drop >= 4 else "SKIP — negligible risk change; the cost is pure drag")
    print(f"\n  cost guard (no alpha ⇒ trade only to cut risk): top-name risk "
          f"{rc_cur.max():.0f}% → {rc_erc.max():.0f}%  ⇒  {verdict}")
    # vol-target overlay (descriptive): scale gross exposure to hit the vol limit
    if vol_cur > RISK_LIMITS["max_annual_vol_pct"]:
        k = RISK_LIMITS["max_annual_vol_pct"] / vol_cur
        print(f"  vol-target: book vol {vol_cur:.1f}% > {RISK_LIMITS['max_annual_vol_pct']:.0f}% limit "
              f"⇒ a {(1-k)*100:.0f}% cash buffer (scale ×{k:.2f}) brings it to target.")


if __name__ == "__main__":
    main()
