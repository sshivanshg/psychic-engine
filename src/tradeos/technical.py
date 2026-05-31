"""Technical Agent — per-stock price/momentum indicators, pure Python (the FACTS layer).

Computable entirely from the daily OHLCV we already ingest. Like risk.py, this computes only
deterministic facts; the orchestrator's LLM explains them. Everything is DESCRIPTIVE (an RSI of 72
is "in overbought territory", never "sell") — we surface the state, the human reads it.
"""

import pandas as pd

from .config import load_portfolio
from .risk import _load_panels


def _last(series):
    return float(series.iloc[-1]) if len(series) else None


def _sma(s: pd.Series, n: int):
    return float(s.rolling(n).mean().iloc[-1]) if len(s) >= n else None


def _ret(s: pd.Series, n: int):
    return float(s.iloc[-1] / s.iloc[-1 - n] - 1) if len(s) > n else None


def _rsi(s: pd.Series, period: int = 14):
    """Wilder's RSI (EWMA approximation)."""
    if len(s) < period + 1:
        return None
    delta = s.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean().iloc[-1]
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean().iloc[-1]
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return float(100 - 100 / (1 + rs))


def _round(x, n=2):
    return round(float(x), n) if x is not None else None


def compute_technical(close: pd.Series, volume: pd.Series) -> dict | None:
    s = close.dropna()
    if len(s) < 30:
        return None  # not enough history to be meaningful

    last = float(s.iloc[-1])
    sma20, sma50, sma200 = _sma(s, 20), _sma(s, 50), _sma(s, 200)

    macd_line = s.ewm(span=12, adjust=False).mean() - s.ewm(span=26, adjust=False).mean()
    macd = float(macd_line.iloc[-1])
    signal = float(macd_line.ewm(span=9, adjust=False).mean().iloc[-1])
    macd_hist = macd - signal

    rsi = _rsi(s)
    win = s.tail(252)
    pct_from_high = last / float(win.max()) - 1
    ret_1m, ret_3m = _ret(s, 21), _ret(s, 63)

    # volume trend: last 20 sessions vs the 20 before that
    v = volume.dropna()
    vol_trend = None
    if len(v) >= 40:
        recent, prior = v.tail(20).mean(), v.tail(40).head(20).mean()
        vol_trend = "rising" if recent > prior * 1.05 else "falling" if recent < prior * 0.95 else "flat"

    # --- descriptive dials (categorical, threshold-based) ---
    if sma200 is not None:
        trend = "uptrend" if last > sma200 else "downtrend"
        if sma50 is not None and abs(last / sma200 - 1) < 0.02:
            trend = "sideways"
    elif sma50 is not None:
        trend = "uptrend" if last > sma50 else "downtrend"
    else:
        trend = "—"

    momentum = "neutral"
    if rsi is not None:
        momentum = ("overbought" if rsi >= 70 else "strong" if rsi >= 55
                    else "oversold" if rsi <= 30 else "weak" if rsi <= 45 else "neutral")

    level = "at highs" if pct_from_high >= -0.05 else "near lows" if pct_from_high <= -0.25 else "mid-range"

    return {
        "last_close": _round(last),
        "sma20": _round(sma20),
        "sma50": _round(sma50),
        "sma200": _round(sma200),
        "price_vs_sma200_pct": _round((last / sma200 - 1) * 100) if sma200 else None,
        "rsi_14": _round(rsi, 1),
        "macd_hist": _round(macd_hist),
        "ret_1m_pct": _round(ret_1m * 100) if ret_1m is not None else None,
        "ret_3m_pct": _round(ret_3m * 100) if ret_3m is not None else None,
        "pct_from_52w_high": _round(pct_from_high * 100),
        "volume_trend": vol_trend,
        "dials": {"trend": trend, "momentum": momentum, "level": level},
    }


def compute_all_technical(as_of=None, *, panels=None, positions=None) -> dict:
    """Per-symbol technical reads for every holding with enough history.
    `panels`/`positions` can be injected (shared AnalysisContext) to avoid re-querying."""
    if positions is None:
        positions = load_portfolio()
    symbols = [p.symbol for p in positions]
    # Technicals use the split-adjusted PRICE (chart levels), not the total-return series.
    if panels is None:
        close, _adj, volume = _load_panels(symbols, as_of)
    else:
        close, _adj, volume = panels
    if close.empty:
        return {}
    out = {}
    for s in symbols:
        if s in close.columns:
            vol = volume[s] if s in volume.columns else pd.Series(dtype=float)
            t = compute_technical(close[s], vol)
            if t:
                out[s] = t
    return out
