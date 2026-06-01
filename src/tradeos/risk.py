"""Portfolio risk engine — pure Python, no LLM (the *facts* layer).

This is the quant core. Everything here is deterministic and unit-testable; the LLM
(risk_agent.py) only ever *explains* these numbers — it never computes them.

Design choices a desk would recognise:
  * Two price series, used correctly:
      - `adj_close` (total-return; dividends reinvested) → all RETURNS / vol / VaR / risk.
      - `close` (split-adjusted price) → market value, liquidity (actual ₹ traded).
  * LOG returns for volatility/covariance (additive); SIMPLE returns for VaR/stress (real P&L).
  * **Volatility** = EWMA covariance (RiskMetrics λ=0.94): conditional, regime-aware.
  * **Beta** = full-sample OLS with Bloomberg-style shrinkage toward 1 (β_adj = ⅔·raw + ⅓·1).
    Beta is a *structural* exposure, so it gets a long window — NOT the short EWMA window
    (that would make it a noisy 32-day sensitivity, not a beta).
  * Risk on the COVARIANCE STRUCTURE: ex-ante vol σ_p=√(wᵀΣw) and each name's COMPONENT
    contribution (MCTR/%CTR), which sums to 100%.
  * Tails via historical VaR & CVaR/Expected Shortfall (no Gaussian assumption).
  * Liquidity (days-to-liquidate), stress (worst historical windows), and limit breaches.

NOTE (documented, intentional): we report tails BOTH ways. Plain historical VaR/CVaR is
*unconditional* (the full 2y empirical tail), while Filtered Historical Simulation (`_fhs_var_cvar`,
the `*_fhs_pct` fields) is *conditional* — it standardises returns by their EWMA vol and rescales to
today's vol, so it tracks the current regime like the EWMA vol does. They answer complementary
questions; both are surfaced. Covariance is EWMA, then shrunk toward a constant-correlation target
with a Ledoit-Wolf intensity (`_ledoit_wolf_shrink`, on by default via COV_SHRINKAGE) — the shrinkage
δ is small at 5 names but matters more as the universe grows, and is reported in `cov_shrinkage`.

HORIZON: estimate once (daily), express at any horizon via σ_T = σ_daily·√T. Vol/VaR/CVaR scale;
correlation, beta and risk-contribution % are horizon-INVARIANT. Limits stay pinned to natural
units (annual vol, 1-day VaR). `as_of` is the point-in-time hook (no look-ahead past that date).
"""

import math

import numpy as np
import pandas as pd

from .config import BENCHMARK, COV_SHRINKAGE, RISK_LIMITS, load_portfolio
from .db import get_connection

DAYS_PER_YEAR = 252
LAMBDA = 0.94            # RiskMetrics EWMA decay (daily) — for volatility/covariance
PARTICIPATION = 0.20     # tradeable fraction of a name's daily traded value per day
MIN_BETA_OBS = 60        # don't compute a structural beta on fewer than ~3 months of data

_HORIZON_DAYS = {
    "d": 1, "1d": 1, "day": 1, "daily": 1,
    "w": 5, "1w": 5, "week": 5, "weekly": 5,
    "m": 21, "1m": 21, "month": 21, "monthly": 21,
    "q": 63, "1q": 63, "quarter": 63, "quarterly": 63,
    "y": 252, "1y": 252, "year": 252, "yearly": 252,
    "annual": 252, "annualised": 252, "annualized": 252,
}
_HORIZON_LABEL = {1: "daily", 5: "weekly", 21: "monthly", 63: "quarterly", 252: "annual"}


def parse_horizon(token) -> tuple[int, str]:
    """Map a horizon token to (trading_days, label). Accepts d/w/m/q/y aliases or 'N'/'Nd'."""
    if token is None:
        return DAYS_PER_YEAR, "annual"
    t = str(token).strip().lower()
    if t in _HORIZON_DAYS:
        days = _HORIZON_DAYS[t]
        return days, _HORIZON_LABEL.get(days, f"{days}d")
    digits = t[:-1] if t.endswith("d") else t
    if digits.isdigit() and int(digits) > 0:
        days = int(digits)
        return days, _HORIZON_LABEL.get(days, f"{days}d")
    raise ValueError(f"Unrecognised horizon {token!r} — use d/w/m/q/y or N days (e.g. '10d').")


def _round(x, n: int = 2):
    if x is None:
        return None
    try:
        if math.isnan(x):
            return None
    except TypeError:
        return x
    return round(float(x), n)


def _scale(x, factor):
    return x * factor if x is not None else None


def _load_panels(symbols, as_of=None):
    """Return (close, adj_close, volume) wide DataFrames: index=date, one column per symbol.

    `close` is the split-adjusted price (levels/value); `adj_close` is total-return (returns/risk).
    COALESCE lets older rows (no adj_close) fall back to close.
    """
    placeholders = ",".join(["%s"] * len(symbols))
    sql = (f"SELECT date, symbol, close, COALESCE(adj_close, close) AS adj_close, volume "
           f"FROM prices WHERE symbol IN ({placeholders})")
    params: list = list(symbols)
    if as_of is not None:
        sql += " AND date <= %s"
        params.append(as_of)
    sql += " ORDER BY date"
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    if not rows:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    df = pd.DataFrame(rows, columns=["date", "symbol", "close", "adj_close", "volume"])
    close = df.pivot(index="date", columns="symbol", values="close").sort_index()
    adj = df.pivot(index="date", columns="symbol", values="adj_close").sort_index()
    volume = df.pivot(index="date", columns="symbol", values="volume").sort_index()
    return close, adj, volume


def _as_date(x):
    import datetime as _dt
    if isinstance(x, _dt.datetime):
        return x.date()
    if isinstance(x, _dt.date):
        return x
    return _dt.date.fromisoformat(str(x)[:10])


def _latest_vintage_by_cell(rows):
    """Pure: from price_vintages rows (symbol, date, vintage_date, close, adj_close, volume) already
    filtered to `vintage_date <= target`, keep ONLY the latest vintage per (symbol, date) — the value
    as it was KNOWN at the target. Reproducible-replay core; unit-tested without a DB."""
    best: dict = {}
    for sym, date, vd, c, a, v in rows:
        key = (sym, date)
        cur = best.get(key)
        if cur is None or vd > cur[2]:
            best[key] = (sym, date, vd, c, a, v)
    return list(best.values())


def load_panels_asof(symbols, as_of, vintage_asof):
    """Reproducible read: reconstruct (close, adj_close, volume) panels AS THEY WERE KNOWN at
    `vintage_asof` (from the append-only price-revision log), for trading dates <= `as_of`.

    A back-test replayed months later sees the same numbers it saw at decision time, even after the
    vendor retroactively re-adjusts history. Empty panels when no vintages cover the request (e.g.
    dates before the project's first ingest — they were never observed, an honest gap, not a fabricated
    number). `as_of=None` ⇒ no trading-date bound. Requires the `price_vintages` table (Phase-5 schema).
    """
    if not symbols:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    placeholders = ",".join(["%s"] * len(symbols))
    sql = (f"SELECT symbol, date, vintage_date, close, COALESCE(adj_close, close) AS adj_close, volume "
           f"FROM price_vintages WHERE symbol IN ({placeholders}) AND vintage_date <= %s")
    params: list = [*symbols, _as_date(vintage_asof)]
    if as_of is not None:
        sql += " AND date <= %s"
        params.append(_as_date(as_of))
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    rows = _latest_vintage_by_cell(rows)
    if not rows:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    df = pd.DataFrame([(s, d, c, a, v) for s, d, _vd, c, a, v in rows],
                      columns=["symbol", "date", "close", "adj_close", "volume"])
    close = df.pivot(index="date", columns="symbol", values="close").sort_index()
    adj = df.pivot(index="date", columns="symbol", values="adj_close").sort_index()
    volume = df.pivot(index="date", columns="symbol", values="volume").sort_index()
    return close, adj, volume


def _ewma_cov(returns: pd.DataFrame, lam: float = LAMBDA) -> pd.DataFrame:
    """EWMA covariance (daily) on a common-sample return matrix. Most-recent obs weighted most."""
    r = returns.dropna(how="any")
    x = r.values
    t = x.shape[0]
    tw = lam ** np.arange(t - 1, -1, -1)
    tw = tw / tw.sum()
    mu = (tw[:, None] * x).sum(axis=0)
    xc = x - mu
    cov = xc.T @ (tw[:, None] * xc)
    return pd.DataFrame(cov, index=r.columns, columns=r.columns)


def _ewma_vol_series(returns: pd.Series, lam: float = LAMBDA) -> pd.Series:
    """Per-day EWMA(λ) conditional vol on DE-MEANED returns — the same λ and de-meaning convention as
    `_ewma_cov`, so the FHS filter's vol is consistent with the covariance diagonal rather than a
    second, zero-mean estimator. FHS needs the vol at EVERY historical day to standardise that day's
    return (which the single-point windowed cov estimator can't give), hence a recursive series — but
    computed on the same centred returns so the two paths don't silently disagree."""
    centred = returns - returns.mean()
    return np.sqrt(centred.pow(2).ewm(alpha=1 - lam, adjust=False).mean())


def _fhs_var_cvar(port_ret: pd.Series, conf: float, lam: float = LAMBDA):
    """Filtered Historical Simulation VaR/CVaR — *conditional* (current-regime) tail.
    Standardise returns by their EWMA vol, rescale to the latest vol, take the empirical quantile.
    Unlike plain historical VaR (unconditional), this reflects today's volatility regime."""
    r = port_ret.dropna()
    if len(r) < 40:
        return None, None
    sigma = _ewma_vol_series(r, lam)
    if sigma.iloc[-1] == 0:
        return None, None
    z = (r / sigma).replace([np.inf, -np.inf], np.nan).dropna()
    if len(z) < 40:
        return None, None
    return _var_cvar(z * float(sigma.iloc[-1]), conf)


def _ledoit_wolf_shrink(cov: np.ndarray, returns: np.ndarray):
    """Shrink a covariance toward the constant-correlation target with a Ledoit-Wolf intensity.
    `returns` is the T×N matrix used to estimate the optimal intensity δ ∈ [0,1]. Reduces estimation
    noise (most valuable as the universe grows). Conservative ρ term (diagonal only)."""
    n = cov.shape[0]
    if n < 2:
        return cov, 0.0
    d = np.sqrt(np.clip(np.diag(cov), 1e-18, None))
    corr = cov / np.outer(d, d)
    r_bar = (corr.sum() - n) / (n * (n - 1))           # mean off-diagonal correlation
    target = r_bar * np.outer(d, d)
    np.fill_diagonal(target, np.diag(cov))             # constant-correlation target keeps variances

    gamma = float(np.sum((target - cov) ** 2))         # Frobenius distance to target
    if gamma <= 0:
        return cov, 0.0
    x = returns - returns.mean(axis=0)
    t = x.shape[0]
    # π_ij = mean_t (x_ti·x_tj − cov_ij)²  — vectorised (was an O(n²) Python double loop).
    prod = np.einsum("ti,tj->tij", x, x)
    pi_mat = ((prod - cov) ** 2).mean(axis=0)
    pi = float(pi_mat.sum())
    rho = float(np.trace(pi_mat))                       # diagonal term (off-diag omitted → conservative)
    delta = float(np.clip((pi - rho) / gamma / t, 0.0, 1.0))
    return (1 - delta) * cov + delta * target, delta


def _adjusted_beta(asset_ret: pd.Series, bench_ret: pd.Series):
    """Full-sample OLS beta with Bloomberg-style shrinkage toward the market beta of 1."""
    pair = pd.concat([asset_ret, bench_ret], axis=1).dropna()
    pair.columns = ["a", "b"]
    if len(pair) < MIN_BETA_OBS or pair["b"].var(ddof=1) == 0:
        return None
    raw = pair["a"].cov(pair["b"]) / pair["b"].var(ddof=1)
    return 0.67 * raw + 0.33 * 1.0


def _var_cvar(series: pd.Series, conf: float):
    """1-day historical VaR and CVaR (Expected Shortfall) as positive loss fractions."""
    if len(series) < 20:
        return None, None
    cutoff = np.quantile(series.values, 1 - conf)
    var = -float(cutoff)
    tail = series[series <= cutoff]
    cvar = -float(tail.mean()) if len(tail) else var
    return var, cvar


def _worst_window(port_ret: pd.Series, n: int):
    if len(port_ret) < n:
        return None
    roll = (1 + port_ret).rolling(n).apply(np.prod, raw=True) - 1.0
    return float(roll.min())


def compute_risk(as_of=None, horizon: str = "annual", *, panels=None, positions=None) -> dict:
    """Portfolio risk snapshot. `panels`/`positions` can be injected (shared AnalysisContext)
    to avoid re-querying the DB; otherwise they're loaded here."""
    horizon_days, horizon_label = parse_horizon(horizon)
    hf = math.sqrt(horizon_days)
    af = math.sqrt(DAYS_PER_YEAR)

    if positions is None:
        positions = load_portfolio()
    symbols = [p.symbol for p in positions]
    if panels is None:
        close, adj, volume = _load_panels(symbols + [BENCHMARK], as_of)
    else:
        close, adj, volume = panels
    if close.empty:
        raise RuntimeError("No price data found. Run `tradeos ingest` first.")

    have_bench = BENCHMARK in adj.columns
    adj_pos = adj.where(adj > 0)        # non-positive vendor ticks ⇒ NaN, never -inf in log / inf in pct_change
    log_ret = np.log(adj_pos).diff()    # total-return → vol/covariance
    simple_ret = adj_pos.pct_change()   # total-return → VaR/stress/beta
    last_close = close.ffill().iloc[-1]  # actual (split-adjusted) price → value/liquidity
    as_of_date = close.index[-1]

    market_values: dict[str, float] = {}
    total_value = 0.0
    for p in positions:
        lc = last_close.get(p.symbol)
        if p.symbol in close.columns and lc is not None and not pd.isna(lc) and p.quantity:
            market_values[p.symbol] = float(lc) * p.quantity
            total_value += market_values[p.symbol]
    weights = {s: mv / total_value for s, mv in market_values.items()} if total_value else {}
    asset_syms = [s for s in symbols if s in weights]

    # EWMA covariance over the assets only (beta is computed separately, on a long window).
    cov_d = _ewma_cov(log_ret[asset_syms]) if asset_syms else pd.DataFrame()

    sigma_p_daily = 0.0
    pct_ctr: dict[str, float] = {}
    asset_vol_daily: dict[str, float] = {}
    betas: dict[str, float] = {}
    corr_matrix: dict[str, dict] = {}
    avg_pairwise = None
    shrinkage_delta = 0.0
    cov_obs: int | None = None   # common-sample length behind the EWMA covariance (provenance)
    var_obs: int | None = None   # common-sample length behind the historical VaR/CVaR P&L

    if asset_syms:
        sig_d = cov_d.loc[asset_syms, asset_syms].values
        cov_sample = log_ret[asset_syms].dropna(how="any")   # common sample (dropna how="any")
        cov_obs = int(len(cov_sample))
        if COV_SHRINKAGE and len(asset_syms) >= 2:
            sig_d, shrinkage_delta = _ledoit_wolf_shrink(sig_d, cov_sample.to_numpy())
        pw = np.array([weights[s] for s in asset_syms])

        var_p_daily = float(pw @ sig_d @ pw)
        sigma_p_daily = math.sqrt(var_p_daily) if var_p_daily > 0 else 0.0
        asset_vol_daily = dict(zip(asset_syms, np.sqrt(np.clip(np.diag(sig_d), 0, None))))

        if sigma_p_daily > 0:
            m = (sig_d @ pw) / sigma_p_daily
            c = pw * m
            pct_ctr = {s: float(c[i] / sigma_p_daily * 100) for i, s in enumerate(asset_syms)}

        d = np.sqrt(np.clip(np.diag(sig_d), 1e-18, None))
        corr = sig_d / np.outer(d, d)
        corr_matrix = {
            a: {b: _round(corr[i, j], 2) for j, b in enumerate(asset_syms)}
            for i, a in enumerate(asset_syms)
        }
        if len(asset_syms) > 1:
            iu = np.triu_indices(len(asset_syms), k=1)
            avg_pairwise = float(corr[iu].mean())

        if have_bench:
            bench_ret = simple_ret[BENCHMARK]
            for s in asset_syms:
                beta = _adjusted_beta(simple_ret[s], bench_ret)
                if beta is not None:
                    betas[s] = beta

    port_beta = sum(weights[s] * betas[s] for s in betas) if betas else None
    sigma_p_h = sigma_p_daily * hf
    sigma_p_annual = sigma_p_daily * af

    # tails (1-day base) & stress (empirical) on current-weights P&L
    v95 = c95 = v99 = c99 = None
    vf95 = vf99 = cf99 = None
    worst = {1: None, 5: None, 10: None, 21: None}
    if asset_syms:
        ps = simple_ret[asset_syms].dropna(how="any")
        if not ps.empty:
            var_obs = int(len(ps))
            pw = np.array([weights[s] for s in asset_syms])
            port_ret = pd.Series(ps.values @ pw, index=ps.index)
            v95, c95 = _var_cvar(port_ret, 0.95)
            v99, c99 = _var_cvar(port_ret, 0.99)
            vf95, _ = _fhs_var_cvar(port_ret, 0.95)   # conditional (current-regime) VaR
            vf99, cf99 = _fhs_var_cvar(port_ret, 0.99)
            worst = {n: _worst_window(port_ret, n) for n in (1, 5, 10, 21)}

    # liquidity (actual traded value = price × volume)
    dtl: dict[str, float] = {}
    for s in asset_syms:
        if s in volume.columns:
            traded = (close[s] * volume[s]).dropna().tail(20)
            advv = float(traded.median()) if len(traded) else 0.0
            if advv > 0:
                dtl[s] = market_values[s] / (PARTICIPATION * advv)

    positions_out = []
    for p in positions:
        s = p.symbol
        lc = last_close.get(s)
        lc = float(lc) if (lc is not None and not pd.isna(lc)) else None
        vol_h = asset_vol_daily.get(s)
        positions_out.append({
            "symbol": s,
            "quantity": p.quantity,
            "last_close": _round(lc),
            "market_value": _round(market_values.get(s)),
            "weight_pct": _round(weights.get(s, 0) * 100 if s in weights else None),
            "risk_contribution_pct": _round(pct_ctr.get(s)),
            "vol_pct": _round(vol_h * hf * 100) if vol_h is not None else None,
            "beta": _round(betas.get(s)),
            "days_to_liquidate": _round(dtl.get(s)),
            "unrealized_pnl_pct": _round((lc / p.avg_cost - 1) * 100 if (lc and p.avg_cost) else None),
        })
    positions_out.sort(
        key=lambda r: (r["risk_contribution_pct"] is not None, r["risk_contribution_pct"] or 0),
        reverse=True,
    )

    top = positions_out[0] if positions_out and positions_out[0]["risk_contribution_pct"] is not None else None
    w = np.array(list(weights.values()))
    hhi = float((w ** 2).sum()) if len(w) else None

    portfolio = {
        "total_value": _round(total_value),
        "num_holdings": len(positions),
        "effective_holdings": _round(1 / hhi, 2) if hhi else None,
        "vol_pct": _round(sigma_p_h * 100) if sigma_p_daily else None,
        "vol_annual_pct": _round(sigma_p_annual * 100) if sigma_p_daily else None,
        "beta": _round(port_beta),
        "avg_pairwise_corr": _round(avg_pairwise),
        "var_95_pct": _round(_scale(v95, hf) * 100) if v95 is not None else None,
        "cvar_95_pct": _round(_scale(c95, hf) * 100) if c95 is not None else None,
        "var_99_pct": _round(_scale(v99, hf) * 100) if v99 is not None else None,
        "cvar_99_pct": _round(_scale(c99, hf) * 100) if c99 is not None else None,
        "var_99_1d_pct": _round(v99 * 100) if v99 is not None else None,
        "var_95_fhs_pct": _round(_scale(vf95, hf) * 100) if vf95 is not None else None,
        "var_99_fhs_pct": _round(_scale(vf99, hf) * 100) if vf99 is not None else None,
        "cvar_99_fhs_pct": _round(_scale(cf99, hf) * 100) if cf99 is not None else None,
        "cov_shrinkage": _round(shrinkage_delta, 3),
        "cov_obs": cov_obs,          # # of common trading days behind the covariance (provenance)
        "var_obs": var_obs,          # # of common trading days behind the historical VaR/CVaR
        "worst_1d_pct": _round(worst[1] * 100 if worst[1] is not None else None),
        "worst_5d_pct": _round(worst[5] * 100 if worst[5] is not None else None),
        "worst_10d_pct": _round(worst[10] * 100 if worst[10] is not None else None),
        "worst_21d_pct": _round(worst[21] * 100 if worst[21] is not None else None),
        "top_risk_contributor": top["symbol"] if top else None,
        "top_risk_pct": top["risk_contribution_pct"] if top else None,
        "max_days_to_liquidate": _round(max(dtl.values())) if dtl else None,
    }

    # Provenance: a holding's short history truncates the COMMON sample (dropna how="any"), so the
    # whole book's vol/tail can quietly rest on far fewer days than "2y" implies. Surface it rather
    # than letting an authoritative-looking number hide the thin sample behind it (Directives #7/#8).
    data_warnings: list[str] = []
    if var_obs is not None and var_obs < DAYS_PER_YEAR:
        data_warnings.append(
            f"tail/VaR estimated on only {var_obs} common trading days (a holding's short history "
            "truncates the common sample) — treat horizon tails as low-confidence")
    if cov_obs is not None and cov_obs < MIN_BETA_OBS:
        data_warnings.append(f"covariance estimated on only {cov_obs} common observations")

    return {
        "as_of": str(as_of_date),
        "benchmark": BENCHMARK if have_bench else None,
        "horizon": horizon_label,
        "horizon_days": horizon_days,
        "tail_scaling": "sqrt-time (iid approximation)",
        "method": (
            "total-return prices for returns, split-adjusted for levels/value; "
            "log-return EWMA(λ=0.94) covariance + Ledoit-Wolf shrinkage; adjusted full-sample beta "
            f"(⅔·raw+⅓); historical + FHS-conditional VaR/CVaR; σ & VaR scaled by √T to the "
            f"{horizon_label} horizon — an iid approximation (the 1-day VaR limit check is exact)"
        ),
        "data_warnings": data_warnings,
        "portfolio": portfolio,
        "positions": positions_out,
        "correlation": corr_matrix,
        "limits": _evaluate_limits(portfolio, positions_out),
    }


def _evaluate_limits(portfolio: dict, positions: list) -> list[dict]:
    """Check against RISK_LIMITS at NATURAL units (annual vol, 1-day VaR) — horizon-independent."""
    L = RISK_LIMITS
    checks: list[dict] = []

    def add(metric, value, limit, ok, note=""):
        checks.append({"metric": metric, "value": value, "limit": limit, "ok": ok, "note": note})

    weights = [(p["symbol"], p["weight_pct"]) for p in positions if p["weight_pct"] is not None]
    if weights:
        sym, val = max(weights, key=lambda x: x[1])
        add("max single-name weight %", val, L["max_name_weight_pct"],
            val <= L["max_name_weight_pct"], sym)

    risks = [(p["symbol"], p["risk_contribution_pct"]) for p in positions if p["risk_contribution_pct"] is not None]
    if risks:
        sym, val = max(risks, key=lambda x: x[1])
        add("max single-name risk %", val, L["max_name_risk_pct"],
            val <= L["max_name_risk_pct"], sym)

    if portfolio["vol_annual_pct"] is not None:
        v = portfolio["vol_annual_pct"]
        add("annualised vol %", v, L["max_annual_vol_pct"], v <= L["max_annual_vol_pct"])
    if portfolio["var_99_1d_pct"] is not None:
        v = portfolio["var_99_1d_pct"]
        add("1-day 99% VaR %", v, L["max_var99_pct"], v <= L["max_var99_pct"])
    if portfolio["effective_holdings"] is not None:
        v = portfolio["effective_holdings"]
        add("effective holdings", v, L["min_effective_holdings"], v >= L["min_effective_holdings"])
    if portfolio["max_days_to_liquidate"] is not None:
        v = portfolio["max_days_to_liquidate"]
        add("max days-to-liquidate", v, L["max_days_to_liquidate"], v <= L["max_days_to_liquidate"])

    return checks
