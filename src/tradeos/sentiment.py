"""Sentiment agent — descriptive news-flow read, lexicon-scored (the FACTS layer, pure compute).

A transparent positive/negative finance lexicon scores each ingested headline to a polarity in
[-1, 1]; the agent reports the book-of-headlines mean as a `news_flow` dial. No black box, no API key.

HONESTY (Prime Directives #2, #4, #8): free news feeds give a CURRENT snapshot with shallow, unstable
history, so this is descriptive-of-NOW — it is NOT reconstructable point-in-time and is therefore
**barred from the eval harness** (never added to the back-test signal set). When a holding has no
ingested headlines the agent returns None (honest "no data"), never a fabricated neutral.
"""

import datetime as dt
import re

from .db import get_connection

# Transparent finance lexicon. Deliberately small and auditable — extend per your own read of signal.
_POS = {
    "beat", "beats", "surge", "surges", "record", "strong", "growth", "grew", "upgrade", "upgrades",
    "profit", "profits", "gain", "gains", "rise", "rises", "win", "wins", "robust", "outperform",
    "jump", "jumps", "expansion", "expanding", "raised", "optimistic", "bullish", "recovery", "rally",
    "approval", "approved", "wins", "secures", "highest", "boost", "boosts",
}
_NEG = {
    "miss", "misses", "fall", "falls", "drop", "drops", "slump", "weak", "decline", "declines",
    "downgrade", "downgrades", "loss", "losses", "cut", "cuts", "plunge", "probe", "fraud", "lawsuit",
    "default", "layoff", "layoffs", "warning", "warns", "bearish", "slowdown", "crisis", "penalty",
    "fine", "resign", "resigns", "scam", "raid", "halts", "recall", "downturn", "concern", "concerns",
}

_TOKEN = re.compile(r"[a-z']+")


def score_text(title: str) -> float:
    """Polarity of one headline in [-1, 1] = (pos - neg) / (pos + neg); 0.0 when no lexicon hit."""
    toks = _TOKEN.findall((title or "").lower())
    pos = sum(t in _POS for t in toks)
    neg = sum(t in _NEG for t in toks)
    return 0.0 if (pos + neg) == 0 else (pos - neg) / (pos + neg)


def _label(mean: float) -> str:
    return "positive" if mean > 0.15 else "negative" if mean < -0.15 else "neutral"


def load_sentiment(symbols, as_of=None) -> dict:
    """{symbol: [article rows]} from the sentiment table. With `as_of`, keep only articles whose
    `published` is on/before it (rows with no timestamp are dropped under as_of — honest gap)."""
    if not symbols:
        return {}
    ph = ",".join(["%s"] * len(symbols))
    sql = (f"SELECT symbol, polarity, published FROM sentiment WHERE symbol IN ({ph})")
    params: list = list(symbols)
    if as_of is not None:
        cutoff = as_of if isinstance(as_of, dt.date) else dt.date.fromisoformat(str(as_of)[:10])
        sql += " AND published IS NOT NULL AND published <= %s"
        params.append(cutoff)
    out: dict[str, list] = {s: [] for s in symbols}
    with get_connection() as c, c.cursor() as cur:
        cur.execute(sql, params)
        for sym, pol, pub in cur.fetchall():
            out.setdefault(sym, []).append({"polarity": float(pol), "published": pub})
    return out


def load_headlines(symbols, as_of=None, *, limit_per: int | None = 60) -> dict[str, list[dict]]:
    """{symbol: [{title, publisher, published, polarity}]} newest-first — the RAW headlines for the
    news UI. The analyzer only needs the polarity (`load_sentiment`); this carries the human-readable
    title/publisher for display. Point-in-time: with `as_of`, only headlines published on/before it
    (rows with no timestamp are dropped, exactly like `load_sentiment` — an honest gap, never invented).
    Without `as_of` we keep untimestamped rows too (NULLS last) so a fresh snapshot still shows up.

    NOTE (Prime Directive #2/#8): like the sentiment dial, this is a CURRENT snapshot — descriptive,
    not reconstructable point-in-time — so the UI must badge it `snapshot · eval-barred`."""
    syms = list(symbols)
    if not syms:
        return {}
    ph = ",".join(["%s"] * len(syms))
    sql = f"SELECT symbol, title, publisher, published, polarity FROM sentiment WHERE symbol IN ({ph})"
    params: list = list(syms)
    if as_of is not None:
        cutoff = as_of if isinstance(as_of, dt.date) else dt.date.fromisoformat(str(as_of)[:10])
        sql += " AND published IS NOT NULL AND published <= %s"
        params.append(cutoff)
    sql += " ORDER BY symbol, published DESC NULLS LAST"
    out: dict[str, list[dict]] = {s: [] for s in syms}
    with get_connection() as c, c.cursor() as cur:
        cur.execute(sql, params)
        for sym, title, publisher, published, polarity in cur.fetchall():
            bucket = out.setdefault(sym, [])
            if limit_per is not None and len(bucket) >= limit_per:
                continue
            bucket.append({"title": title, "publisher": publisher,
                           "published": str(published)[:10] if published else None,
                           "polarity": round(float(polarity), 3) if polarity is not None else None})
    return out


def compute_sentiment(symbol: str, as_of=None, *, articles=None) -> dict | None:
    arts = load_sentiment([symbol], as_of).get(symbol, []) if articles is None else articles
    if not arts:
        return None
    pols = [a["polarity"] for a in arts]
    mean = sum(pols) / len(pols)
    label = _label(mean)
    return {
        "n_articles": len(pols),
        "mean_polarity": round(mean, 3),
        "label": label,
        "pos_share_pct": round(sum(p > 0 for p in pols) / len(pols) * 100, 1),
        "neg_share_pct": round(sum(p < 0 for p in pols) / len(pols) * 100, 1),
        "dials": {"news_flow": label},
        "note": "current news snapshot — descriptive, not point-in-time; barred from the eval harness",
    }


def compute_all_sentiment(as_of=None, *, sentiment=None, positions=None) -> dict:
    """Per-symbol news-flow read. `sentiment`/`positions` injectable (shared AnalysisContext)."""
    if positions is None:
        from .config import load_portfolio
        positions = load_portfolio()
    symbols = [p.symbol for p in positions]
    if sentiment is None:
        sentiment = load_sentiment(symbols, as_of)
    out = {}
    for s in symbols:
        r = compute_sentiment(s, as_of, articles=sentiment.get(s, []))
        if r is not None:
            out[s] = r
    return out
