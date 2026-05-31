"""Phase 4 — the honest eval harness: do the signals actually predict forward returns?

This is the differentiator. Most "AI trading" projects assert their signals work; this one
*measures* it, and reports the uncertainty so the number can't quietly lie to you.

What we measure, for each price signal, over a point-in-time history:

  * **Cross-sectional IC** — the desk-standard metric. On each date we rank the names by the
    signal and by their realised `horizon`-day forward return, take the Spearman rank correlation
    ACROSS NAMES, and average those daily ICs over time. That isolates "does the signal rank the
    cross-section correctly today", which is the question a stock-selection signal must answer.
  * **ICIR** = mean(IC) / std(IC): the raw information ratio (descriptive — NOT overlap-adjusted).
  * **t-stat (Newey-West)** — the honest significance test. Daily ICs built from *overlapping*
    `horizon`-day forward windows are autocorrelated, so a naive standard error overstates
    significance. We use a Newey-West (Bartlett-kernel) HAC estimator with lag = horizon-1 to
    correct it. |t| ≳ 2 ≈ significant at ~5%. This is the single thing that turns a backtest that
    flatters you into one that doesn't.
  * **Hit rate** vs a 50% null (above-median signal → above-median forward return), plus the
    period **base rate** P(fwd>0) so you can see how much of any hit is just market drift.
  * **Long-short tercile spread** — per date, mean forward return of the top-signal names minus the
    bottom, averaged over time: an illustrative (gross, pre-cost) long-short return per horizon.
  * **Pooled IC** — the old metric (all (name,date) obs stacked, subsampled every `step` days) kept
    only as a *diagnostic*. It conflates time-series and cross-sectional variation and inflates the
    apparent sample, so it is reported but never the headline.

Honesty caveats baked in:
- **No look-ahead:** signals use only trailing data; the forward return is the (future) label.
  `as_of` hard-stops the price panel so a replay never sees beyond the simulated date.
- **Small universe ⇒ illustrative, not conclusive.** A 5-name cross-section is dominated by one or
  two names; the cross-sectional IC is honest but underpowered. `n_dates`, `universe` and the
  Newey-West t-stat are printed so you can judge the power yourself — and the t-stat will usually,
  correctly, say "not significant" on a handful of large-caps.
- **Gross of costs.** No transaction costs or turnover are modelled — the tercile spread is an
  upper bound a real book would never capture. Turnover/cost-aware net returns are a TODO.
- **Price-based signals only.** Fundamental signals must apply the results-announcement lag
  (`config.ANNOUNCEMENT_LAG_DAYS`) before they can be evaluated here — a TODO for that extension.
"""

import math

import numpy as np
import pandas as pd

from .config import load_holdings
from .risk import _load_panels

MIN_HISTORY = 250        # a name needs ~1y of daily obs to contribute a meaningful signal
MIN_XS_NAMES = 4         # need at least this many names on a date for a credible cross-section


def _rsi_series(s: pd.Series, period: int = 14) -> pd.Series:
    d = s.diff()
    ag = d.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    al = (-d.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = ag / al.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


# Each signal maps an adjusted-close series -> a point-in-time (trailing-only) signal series.
SIGNALS = {
    "momentum_3m": lambda s: s / s.shift(63) - 1,                  # trailing 3-month return
    "trend_vs_200sma": lambda s: s / s.rolling(200).mean() - 1,
    "rsi_14": lambda s: _rsi_series(s),                            # mean-reversion: expect NEGATIVE IC
    "low_volatility": lambda s: -s.pct_change().rolling(63).std(),  # low-vol anomaly
}


def _spearman(x: pd.Series, y: pd.Series):
    """Spearman rank correlation = Pearson on ranks. None if degenerate (ties/too few)."""
    rx, ry = x.rank(), y.rank()
    if len(rx) < 3 or rx.std(ddof=0) == 0 or ry.std(ddof=0) == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def _newey_west_tstat(series: pd.Series, lag: int):
    """(mean, t) for the mean of a serially-correlated series, via a Newey-West HAC variance.

    The IC series is built from overlapping `horizon`-day forward windows, so adjacent ICs are
    autocorrelated; ignoring that would shrink the standard error and overstate significance. The
    Bartlett-kernel HAC estimator with `lag = horizon-1` accounts for it. Returns (None, None) if
    there's too little data to estimate a variance.
    """
    x = series.dropna().to_numpy(dtype=float)
    m = len(x)
    if m < 8:
        return (float(x.mean()) if m else None), None
    mean = float(x.mean())
    e = x - mean
    gamma0 = float(e @ e) / m
    s = gamma0
    for k in range(1, min(lag, m - 1) + 1):
        w = 1.0 - k / (lag + 1)                     # Bartlett weight
        s += 2.0 * w * float(e[k:] @ e[:-k]) / m
    var_mean = s / m
    if var_mean <= 0:
        return mean, None
    return mean, mean / math.sqrt(var_mean)


def _signal_panels(adj: pd.DataFrame, symbols, fn, horizon: int):
    """Build aligned (signal, forward-return) wide panels [date x symbol] for one signal.

    Only names with >= MIN_HISTORY observations contribute. Both panels are point-in-time: the
    signal is trailing-only; the forward return s(t+h)/s(t)-1 is the (future) label we score against.
    """
    sig_cols, fwd_cols = {}, {}
    for sym in symbols:
        if sym not in adj.columns:
            continue
        s = adj[sym].dropna()
        if len(s) < MIN_HISTORY:
            continue
        sig_cols[sym] = fn(s)
        fwd_cols[sym] = s.shift(-horizon) / s - 1
    if not sig_cols:
        return pd.DataFrame(), pd.DataFrame()
    return pd.DataFrame(sig_cols), pd.DataFrame(fwd_cols)


def _cross_sectional_ic(sig: pd.DataFrame, fwd: pd.DataFrame, min_names: int = MIN_XS_NAMES):
    """Daily cross-sectional rank IC: Spearman across names, per date. Returns a date-indexed Series."""
    ics = {}
    for date in sig.index:
        row = pd.DataFrame({"sig": sig.loc[date], "fwd": fwd.loc[date]}).dropna()
        if len(row) >= min_names:
            ic = _spearman(row["sig"], row["fwd"])
            if ic is not None:
                ics[date] = ic
    return pd.Series(ics, dtype=float).sort_index()


def _tercile_spread(sig: pd.DataFrame, fwd: pd.DataFrame, min_names: int = MIN_XS_NAMES):
    """Per-date long-short: mean forward return of top-signal names minus bottom, averaged over dates."""
    spreads = []
    for date in sig.index:
        row = pd.DataFrame({"sig": sig.loc[date], "fwd": fwd.loc[date]}).dropna()
        n = len(row)
        if n < min_names:
            continue
        k = max(1, int(round(n / 3)))
        ranked = row.sort_values("sig")
        bottom = ranked["fwd"].iloc[:k].mean()
        top = ranked["fwd"].iloc[-k:].mean()
        spreads.append(top - bottom)
    return float(np.mean(spreads)) if spreads else None


def _hit_and_base(sig: pd.DataFrame, fwd: pd.DataFrame, step: int):
    """Directional hit-rate vs a 50% null + the period base rate P(fwd>0).

    Pooled over (name, date), subsampled every `step` days to limit forward-window overlap. The hit
    splits BOTH signal and forward return at their medians, so 50% is the no-skill null (this strips
    out the market-drift bias that an absolute fwd>0 test would smuggle in). The base rate is
    reported separately so you can still see how bullish the sampled window was.
    """
    obs = pd.DataFrame({"sig": sig.stack(), "fwd": fwd.stack()}).dropna()
    obs = obs.iloc[::step]
    if len(obs) < 10:
        return None, None, len(obs)
    base = float((obs["fwd"] > 0).mean()) * 100
    hit = float(((obs["sig"] > obs["sig"].median()) == (obs["fwd"] > obs["fwd"].median())).mean()) * 100
    return hit, base, len(obs)


def _pooled_ic(sig: pd.DataFrame, fwd: pd.DataFrame, step: int):
    """Diagnostic only: rank IC over ALL (name, date) obs stacked together (subsampled every `step`).
    Mixes time-series and cross-sectional variation and inflates the sample — never the headline."""
    obs = pd.DataFrame({"sig": sig.stack(), "fwd": fwd.stack()}).dropna().iloc[::step]
    return _spearman(obs["sig"], obs["fwd"]), len(obs)


def _round(x, n=3):
    return round(float(x), n) if x is not None and not (isinstance(x, float) and math.isnan(x)) else None


def evaluate(horizon: int = 21, step: int = 5, as_of=None) -> dict:
    symbols = load_holdings()
    _close, adj, _vol = _load_panels(symbols, as_of)
    if adj.empty:
        raise RuntimeError("No price data. Run `tradeos ingest` first.")

    nw_lag = max(1, horizon - 1)         # overlapping h-day windows ⇒ autocorrelation up to ~h lags
    universe = sorted(s for s in symbols if s in adj.columns)

    results = {}
    for name, fn in SIGNALS.items():
        sig, fwd = _signal_panels(adj, universe, fn, horizon)
        if sig.empty:
            continue

        ic_series = _cross_sectional_ic(sig, fwd)
        n_dates = len(ic_series)
        mean_ic = t_stat = icir = None
        if n_dates >= 8:
            mean_ic, t_stat = _newey_west_tstat(ic_series, nw_lag)
            sd = float(ic_series.std(ddof=1))
            icir = (mean_ic / sd) if (mean_ic is not None and sd > 0) else None

        hit, base, n_obs = _hit_and_base(sig, fwd, step)
        spread = _tercile_spread(sig, fwd)
        pooled, _ = _pooled_ic(sig, fwd, step)

        results[name] = {
            "n_dates": n_dates,
            "n_obs": n_obs,
            "ic": _round(mean_ic),
            "icir": _round(icir, 2),
            "t_stat": _round(t_stat, 2),
            "hit_rate_pct": _round(hit, 1),
            "base_rate_pct": _round(base, 1),
            "ls_spread_pct": _round(spread * 100, 2) if spread is not None else None,
            "pooled_ic": _round(pooled),
        }

    return {
        "horizon_days": horizon,
        "step_days": step,
        "universe": len(universe),
        "nw_lag": nw_lag,
        "signals": results,
    }
