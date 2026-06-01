"""Transcript-grounded CREDIBILITY engine — did management DELIVER what they guided?

The richest agent angle: it pairs each PAST extracted concall guidance (extraction.py → `guidance`
table) with the ACTUAL quarterly results that followed (fundamentals), and judges each one
delivered / partial / missed / too-early — building a management track record ("delivered 2 of 3").

Token-disciplined: a cheap DB read first; the LLM scoring call fires ONLY when guidance actually
exists (so it's free for the 99% of names with no transcript on file). Descriptive + grounded: the
verdict on each guidance is judged ONLY against the provided actual numbers, never invented.

Activate it by ingesting concalls:  `tradeos docs add SYMBOL file.pdf`  →  `tradeos extract SYMBOL`.
Then the analyst brief gains a credibility read for free-of-extra-plumbing.
"""

import os

from pydantic import BaseModel

from .db import get_connection
from .fundamental import load_fundamentals
from .log import get_logger

log = get_logger()


def load_guidance_history(symbol: str, as_of=None) -> list[dict]:
    """ALL stored guidance records for a symbol (oldest → newest), not just the latest. Each row =
    {period, source, revenue_outlook, margin_outlook, demand_commentary, other_guidance, quotes}.
    With `as_of`, only guidance for periods on/before it (a coarse point-in-time guard)."""
    sql = "SELECT period, source, data FROM guidance WHERE symbol=%s"
    params: list = [symbol.upper()]
    if as_of is not None:
        sql += " AND (period IS NULL OR period <= %s)"
        params.append(str(as_of)[:10])
    sql += " ORDER BY period ASC NULLS LAST, extracted_at ASC"
    with get_connection() as c, c.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    out = []
    for p, src, data in rows:
        d = data or {}
        # skip content-free records (e.g. a RESULTS filing yields no forward guidance → all-null) so
        # we never burn a scoring call on an empty promise. Honest no-data, not a wasted LLM round-trip.
        has_content = any(d.get(k) for k in ("revenue_outlook", "margin_outlook", "demand_commentary")) \
            or bool(d.get("other_guidance"))
        if has_content:
            out.append({"period": str(p) if p else None, "source": src, **d})
    return out


def _actuals(symbol: str, n: int = 8) -> list[dict]:
    """Last n quarters of actual results — the yardstick guidance is measured against."""
    df = load_fundamentals([symbol.upper()]).get(symbol.upper())
    if df is None or getattr(df, "empty", True):
        return []
    d = df.dropna(subset=["period_end"]).sort_values("period_end").tail(n)
    out = []
    for _, r in d.iterrows():
        rev, ni = r.get("total_revenue"), r.get("net_income")
        rev = float(rev) if rev is not None and rev == rev else None
        ni = float(ni) if ni is not None and ni == ni else None
        out.append({"q": str(r["period_end"])[:10], "revenue": rev, "net_income": ni,
                    "net_margin_pct": round(ni / rev * 100, 1) if (rev and ni is not None and rev) else None})
    return out


# ----------------------------- the scorecard -----------------------------

class GuidanceCheck(BaseModel):
    period: str        # when the guidance was given (the concall's period)
    promised: str      # short paraphrase of what management guided
    actual: str        # what the subsequent results actually showed (cite the number)
    verdict: str       # delivered | partial | missed | too-early


class CredibilityReport(BaseModel):
    track_record: str          # one line, e.g. "delivered 2 of 3 measurable guidance items"
    checks: list[GuidanceCheck]
    caveat: str                # the honest limitation (qualitative guidance, short history, etc.)


_SYSTEM = (
    "You assess management CREDIBILITY: did they deliver what they guided? You are given a company's "
    "PAST concall guidance (with the period it was given) and the ACTUAL quarterly results that "
    "followed. For each guidance item that is measurable against the actuals, judge it delivered / "
    "partial / missed / too-early — using ONLY the provided actual numbers (cite them). Guidance about "
    "periods with no actual yet is 'too-early'. Be terse and factual. DESCRIPTIVE only — no buy/sell, "
    "no price target. Never invent a number; if guidance is too vague to score, say so in the caveat."
)


def _score(symbol: str, history: list[dict], actuals: list[dict]) -> dict | None:
    """Pure-ish core: one small LLM call to score promised-vs-delivered. Separated from the DB load so
    it is unit-testable with synthetic guidance. Returns the report dict (+ `usage`) or None on failure."""
    from .analyst import ANALYST_MODEL  # reuse the cheap model + its env override
    lines = ["GUIDANCE (oldest first):"]
    for g in history:
        bits = [f"period {g.get('period')}"]
        for fld in ("revenue_outlook", "margin_outlook", "demand_commentary"):
            if g.get(fld):
                bits.append(f"{fld}={g[fld]}")
        if g.get("other_guidance"):
            bits.append("other=" + "; ".join(g["other_guidance"]))
        lines.append("  - " + " | ".join(bits))
    # Pre-format to ₹cr (÷1e7) so the model never does scale arithmetic — Haiku otherwise mis-scales
    # raw rupees (a "confidently-wrong number", which this project forbids). Numbers in, numbers out.
    def _cr(x):
        return f"₹{x / 1e7:,.0f}cr" if x is not None else "n/a"
    lines.append("\nACTUAL RESULTS (oldest first; figures already in ₹crore — use them verbatim, do NOT rescale):")
    for a in actuals:
        lines.append(f"  - {a['q']}: revenue {_cr(a['revenue'])}, net_income {_cr(a['net_income'])}, "
                     f"net_margin {a['net_margin_pct']}%")
    digest = "\n".join(lines)
    try:
        import anthropic
        resp = anthropic.Anthropic().messages.parse(
            model=ANALYST_MODEL, max_tokens=800,
            system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": digest}],
            output_format=CredibilityReport,
        )
        parsed = resp.parsed_output
        if parsed is None:
            raise RuntimeError("structured output returned nothing")
        out = parsed.model_dump()
        out["usage"] = getattr(resp, "usage", None)
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("credibility scoring failed for %s: %s", symbol, e)
        return None


def assess_credibility(symbol: str, as_of=None) -> dict | None:
    """Management credibility for one name, or None when there's no guidance on file (the common case —
    a single cheap DB read, zero tokens). The LLM scoring call fires only when guidance exists."""
    history = load_guidance_history(symbol, as_of)
    if not history:
        return None
    if not os.getenv("ANTHROPIC_API_KEY"):
        return {"track_record": f"{len(history)} guidance record(s) on file — set ANTHROPIC_API_KEY to score delivery",
                "checks": [], "caveat": "no API key", "n_guidance": len(history)}
    report = _score(symbol, history, _actuals(symbol))
    if report is not None:
        report["n_guidance"] = len(history)
    return report
