"""Live news via the AGENT's own web search — fresh, relevant catalysts at verdict time, replacing the
stale pre-ingested yfinance snapshot (which was empty for most mid/small-caps).

Flow: the model web-searches for the company's recent news (Anthropic `web_search` tool) → returns a
compact list → we score + UPSERT it into the existing `sentiment` table, so every downstream consumer
(analyst headlines, events.py catalyst-tagging) works unchanged, just on LIVE data.

COST-AWARE (the search results balloon context ~30k tokens + a per-search fee ≈ $0.05/fetch): we cache
by writing to `sentiment` with a timestamp and only re-search when the stored news is older than
`NEWS_TTL_HOURS`. So a name is searched at most once/day, and repeat briefs reuse it for free.
"""

import datetime as dt
import json
import os
import re

from .db import get_connection
from .log import get_logger
from .sentiment import score_text

log = get_logger()

NEWS_MODEL = os.getenv("NEWS_MODEL", "claude-haiku-4-5")
NEWS_TTL_HOURS = float(os.getenv("NEWS_TTL_HOURS", "24"))


def _company_name(symbol: str) -> str:
    with get_connection() as c, c.cursor() as cur:
        cur.execute("SELECT name FROM security_meta WHERE symbol=%s", (symbol,))
        r = cur.fetchone()
    return r[0] if r and r[0] else symbol.replace(".NS", "").replace(".BO", "")


def _last_fetch(symbol: str):
    with get_connection() as c, c.cursor() as cur:
        cur.execute("SELECT max(ingested_at) FROM sentiment WHERE symbol=%s", (symbol,))
        row = cur.fetchone()
        return row[0] if row else None


def _is_fresh(symbol: str, ttl_hours: float) -> bool:
    last = _last_fetch(symbol)
    if last is None:
        return False
    now = dt.datetime.now(last.tzinfo) if last.tzinfo else dt.datetime.now()
    return (now - last).total_seconds() < ttl_hours * 3600


def _parse_items(text: str) -> list[dict]:
    """Pull the JSON array of headlines out of the model's final text (lenient)."""
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except (ValueError, TypeError):
        return []
    return [a for a in arr if isinstance(a, dict) and a.get("headline")]


def fetch_live_news(symbol: str, max_items: int = 7):
    """One web-search call → list of {date, headline, source}. Returns (items, usage)."""
    import anthropic
    name = _company_name(symbol)
    bare = symbol.replace(".NS", "").replace(".BO", "")
    prompt = (
        f"Search the web for the most RECENT news about {name} (Indian listed company, NSE:{bare}) "
        f"from roughly the last 6 weeks — results, orders, expansions, ratings, management, legal/"
        f"regulatory, analyst/investor events. Return ONLY a JSON array (max {max_items}), most-recent "
        f'first, each object: {{"date":"YYYY-MM-DD","headline":"one line","source":"publisher"}}. '
        f"JSON only, no prose."
    )
    resp = anthropic.Anthropic().messages.create(
        model=NEWS_MODEL, max_tokens=1024,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}],
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text")
    return _parse_items(text)[:max_items], resp.usage


def _store(symbol: str, items: list[dict]) -> None:
    """Replace this symbol's news with the freshly-fetched set (scored to polarity)."""
    with get_connection() as c, c.cursor() as cur:
        cur.execute("DELETE FROM sentiment WHERE symbol=%s", (symbol,))
        for it in items:
            title = (it.get("headline") or "").strip()[:500]
            if not title:
                continue
            try:
                pub = dt.date.fromisoformat(str(it.get("date"))[:10]) if it.get("date") else None
            except ValueError:
                pub = None
            cur.execute(
                "INSERT INTO sentiment (symbol, title, publisher, published, polarity) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (symbol, title) DO UPDATE SET "
                "publisher=EXCLUDED.publisher, published=EXCLUDED.published, "
                "polarity=EXCLUDED.polarity, ingested_at=now()",
                (symbol, title, it.get("source"), pub, score_text(title)),
            )
        c.commit()


def refresh_news(symbol: str, ttl_hours: float | None = None, force: bool = False) -> dict:
    """Ensure fresh news for `symbol` in the sentiment table. Web-searches only if the stored news is
    stale (> TTL) or `force`; otherwise reuses the cache (free). Returns a small status/usage dict."""
    symbol = symbol.upper()
    ttl = NEWS_TTL_HOURS if ttl_hours is None else ttl_hours
    if not force and _is_fresh(symbol, ttl):
        return {"fetched": False, "reason": "cached (within TTL)"}
    if not os.getenv("ANTHROPIC_API_KEY"):
        return {"fetched": False, "reason": "no ANTHROPIC_API_KEY"}
    try:
        items, usage = fetch_live_news(symbol)
    except Exception as e:  # noqa: BLE001 - live news is best-effort; never break a brief
        log.warning("live news fetch failed for %s: %s", symbol, e)
        return {"fetched": False, "reason": f"search failed: {str(e)[:50]}"}
    _store(symbol, items)
    return {"fetched": True, "n": len(items), "usage": usage}
