"""Phase 3 — structured guidance extraction from concalls, feeding the Fundamental agent.

RAG-retrieve the guidance-relevant chunks of a holding's concall, then force the LLM to return
CLEAN, SCHEMA-CONSTRAINED fields *with the verbatim source quotes it used* (Pydantic +
`messages.parse`). One wrong number kills trust, so: extraction is grounded in retrieved text only,
the supporting quotes are kept for audit, and the result is STORED — the (cheap) Fundamental agent
reads it on every run without re-paying for the (costly) LLM call. No API key ⇒ returns the
retrieved evidence and extracts nothing.

This is the document→fundamental loop the roadmap calls for: the Fundamental agent stops being
yfinance-numbers-only and gains management's own forward-looking commentary, cited.
"""

import datetime as dt
import os

from .config import ANNOUNCEMENT_LAG_DAYS, CLAUDE_MODEL
from .db import get_connection
from .docs import search
from .log import get_logger
from .trace import RunTrace, timed_call

log = get_logger()

# Multi-query retrieval: guidance is scattered across a transcript, so sweep a few angles and merge.
_GUIDANCE_QUERIES = [
    "management guidance and outlook for revenue growth",
    "operating margin guidance and outlook",
    "demand environment, deal wins and pipeline commentary",
]

_EXTRACT_SYSTEM = (
    "You extract a company's MANAGEMENT GUIDANCE from the provided numbered concall excerpts, using "
    "ONLY those excerpts. Fill each field with management's forward-looking statement if present, "
    "else null — never infer or use outside knowledge. `quotes` must be verbatim snippets from the "
    "excerpts that support what you extracted (for audit). Be factual and concise; no investment advice."
)

GUIDANCE_FIELDS = ("revenue_outlook", "margin_outlook", "demand_commentary", "other_guidance", "quotes")


def _gather(symbol: str, k: int) -> list[dict]:
    """Retrieve + dedup guidance-relevant chunks across the query set, nearest first."""
    seen: set[tuple] = set()
    hits: list[dict] = []
    for q in _GUIDANCE_QUERIES:
        for h in search(symbol, q, k=3):
            key = (h["source"], h["chunk"])
            if key not in seen:
                seen.add(key)
                hits.append(h)
    hits.sort(key=lambda h: h["distance"])
    return hits[:k]


def _store(symbol: str, source: str, period, data: dict) -> None:
    from psycopg.types.json import Json
    with get_connection() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO guidance (symbol, source, period, data) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (symbol, source) DO UPDATE SET period=EXCLUDED.period, data=EXCLUDED.data, "
            "extracted_at=now()",
            (symbol, source, period, Json(data)),
        )
        c.commit()


def extract_guidance(symbol: str, period=None, k: int = 6, trace: RunTrace | None = None) -> dict:
    """Retrieve → LLM structured-extract → store guidance for a holding. Returns the result dict."""
    symbol = symbol.upper()
    hits = _gather(symbol, k)
    if not hits:
        return {"note": f"No documents for {symbol}. Add one:  tradeos docs add {symbol} <file.pdf>",
                "stored": False}
    if not os.getenv("ANTHROPIC_API_KEY"):
        return {"note": "No ANTHROPIC_API_KEY — showing retrieved evidence; nothing extracted.",
                "hits": hits, "stored": False}

    import anthropic
    from pydantic import BaseModel

    class GuidanceExtract(BaseModel):
        revenue_outlook: str | None
        margin_outlook: str | None
        demand_commentary: str | None
        other_guidance: list[str]
        quotes: list[str]

    context = "\n\n".join(f"[{i + 1}] (source: {h['source']})\n{h['content']}"
                          for i, h in enumerate(hits))
    own = trace is None
    trace = trace or RunTrace()
    try:
        msg = timed_call(
            trace, f"extract:{symbol}", CLAUDE_MODEL,
            lambda: anthropic.Anthropic().messages.parse(
                model=CLAUDE_MODEL, max_tokens=900,
                system=[{"type": "text", "text": _EXTRACT_SYSTEM, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": f"Excerpts:\n{context}\n\nExtract management guidance."}],
                output_format=GuidanceExtract,
            ),
        )
        out = msg.parsed_output
        if out is None:
            raise RuntimeError("structured output parse returned nothing")
        data = out.model_dump()
    except Exception as e:  # noqa: BLE001 - a failed extraction shouldn't lose the evidence
        log.warning("guidance extraction failed for %s: %s", symbol, e)
        return {"note": "Extraction failed; showing retrieved evidence.", "hits": hits, "stored": False}
    finally:
        if own:
            trace.print_summary()

    source = hits[0]["source"]
    _store(symbol, source, period, data)
    return {"guidance": data, "hits": hits, "source": source, "stored": True}


def _coerce_date(as_of):
    if isinstance(as_of, dt.datetime):
        return as_of.date()
    if isinstance(as_of, dt.date):
        return as_of
    return dt.date.fromisoformat(str(as_of)[:10])


def load_all_guidance(symbols, as_of=None) -> dict:
    """{symbol: guidance dict (+ source/period)} — latest stored extraction per symbol.

    Point-in-time (Prime Directive #2): with `as_of` set, only guidance whose source document was
    *public* by then is returned. Availability = the document's `filing_date` (from `doc_chunks`);
    when a doc is untagged we fall back to `period + ANNOUNCEMENT_LAG_DAYS` (a concall lands ~a
    results-lag after period-end). Unknown availability ⇒ excluded — an honest gap, never a
    look-ahead. With `as_of=None` (live) every stored extraction is eligible.
    """
    if not symbols:
        return {}
    ph = ",".join(["%s"] * len(symbols))
    if as_of is None:
        sql = (f"SELECT DISTINCT ON (symbol) symbol, data, period, source FROM guidance "
               f"WHERE symbol IN ({ph}) ORDER BY symbol, period DESC NULLS LAST, extracted_at DESC")
        params: list = list(symbols)
    else:
        # COALESCE(filing_date, period + lag) is the doc's public date; `date + int` adds days in PG.
        sql = (f"SELECT DISTINCT ON (g.symbol) g.symbol, g.data, g.period, g.source FROM guidance g "
               f"LEFT JOIN (SELECT symbol, source, MAX(filing_date) AS filing_date FROM doc_chunks "
               f"           GROUP BY symbol, source) d ON d.symbol = g.symbol AND d.source = g.source "
               f"WHERE g.symbol IN ({ph}) AND COALESCE(d.filing_date, g.period + %s) <= %s "
               f"ORDER BY g.symbol, g.period DESC NULLS LAST, g.extracted_at DESC")
        params = [*symbols, ANNOUNCEMENT_LAG_DAYS, _coerce_date(as_of)]
    with get_connection() as c, c.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return {r[0]: {**r[1], "source": r[3], "period": str(r[2]) if r[2] else None} for r in rows}


def load_guidance(symbol: str) -> dict | None:
    return load_all_guidance([symbol.upper()]).get(symbol.upper())
