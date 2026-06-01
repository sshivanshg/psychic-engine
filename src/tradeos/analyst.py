"""The multi-angle analyst — symbol in → every fetched detail → a small, cheap AI verdict.

Token discipline (the whole point): the DETAIL is 100% deterministic (free — no LLM). News-event
tagging (events.py) and the quarterly-results trend are computed for free too. Everything is then
compressed into a tiny DIGEST and the verdict is ONE small Haiku call (was 3 fat calls). The call
steelmans bull AND bear, then reconciles — adversarial framing, single round-trip. Per-call usage is
printed so the cost is never a mystery.

Bright lines: DESCRIPTIVE only (no buy/sell/hold, no targets); cite the provided number/headline;
missing data ⇒ "no data", never invented. The LLM never computes — it reads the pure agents' numbers.

Run on ANY symbol in the DB: `python -m tradeos.analyst RELIANCE.NS`.
"""

import argparse
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from pydantic import BaseModel

from .agents import REGISTRY
from .config import BENCHMARK, CLAUDE_MODEL, Position
from .context import AnalysisContext
from .events import catalysts
from .extraction import load_all_guidance
from .fundamental import load_fundamentals
from .log import get_logger
from .macro import load_sectors
from .orchestrator import _build_card, _get_client
from .ownership import load_ownership
from .risk import _load_panels
from .sentiment import load_sentiment

log = get_logger()

# 1 call/name now, but still the cheap model by default. Override with ANALYST_MODEL.
ANALYST_MODEL = os.getenv("ANALYST_MODEL", "claude-haiku-4-5")
# Approx Haiku 4.5 pricing ($/Mtok) for the cost line — override if rates change.
_PRICE_IN, _PRICE_OUT = 1.0, 5.0

# The DEEP read runs a small multi-agent debate (bull · bear · sector → judge) on a stronger model
# than the one-line verdict — richer reasoning, still one round-trip per agent. Sonnet by default.
DEEP_MODEL = os.getenv("DEEP_MODEL", "claude-sonnet-4-6")
_DEEP_PRICE_IN, _DEEP_PRICE_OUT = 3.0, 15.0   # approx Sonnet 4.6 $/Mtok


# ----------------------------- per-symbol facts (free) -----------------------------

def _load_headlines(symbol: str, limit: int = 12) -> list[dict]:
    """Headline TITLES (+polarity/date) for citation — the shared sentiment loader omits the title."""
    from .db import get_connection
    with get_connection() as c, c.cursor() as cur:
        cur.execute("SELECT title, polarity, published FROM sentiment WHERE symbol=%s "
                    "ORDER BY published DESC NULLS LAST LIMIT %s", (symbol, limit))
        return [{"title": t, "polarity": float(p) if p is not None else None,
                 "published": str(pub)[:10] if pub else None} for t, p, pub in cur.fetchall()]


def _quarterly_trend(fund_df, n: int = 6) -> list[dict]:
    """Last n quarters: revenue, net income, net margin — the quarterly RESULTS, in account (free)."""
    if fund_df is None or getattr(fund_df, "empty", True):
        return []
    d = fund_df.dropna(subset=["period_end"]).sort_values("period_end").tail(n)
    rows = []
    for _, r in d.iterrows():
        rev, ni = r.get("total_revenue"), r.get("net_income")
        rev = float(rev) if rev is not None and rev == rev else None
        ni = float(ni) if ni is not None and ni == ni else None
        margin = round(ni / rev * 100, 1) if (rev and ni is not None and rev != 0) else None
        rows.append({"q": str(r["period_end"])[:10], "revenue": rev, "net_income": ni,
                     "net_margin_pct": margin})
    return rows


def build_context_for(symbols, as_of=None, horizon: str = "annual") -> AnalysisContext:
    """An AnalysisContext for an ARBITRARY symbol set (qty=1) — analyse a name without holding it."""
    positions = [Position(s, 1.0, None) for s in symbols]
    close, adj, volume = _load_panels(symbols + [BENCHMARK], as_of)
    return AnalysisContext(
        as_of, horizon, positions, close, adj, volume,
        load_fundamentals(symbols, as_of), load_sectors(symbols),
        load_all_guidance(symbols, as_of), load_sentiment(symbols, as_of),
        load_ownership(symbols, as_of),
    )


def assemble_facts(symbol: str, as_of=None, horizon: str = "annual") -> dict:
    """Run the 6 pure analyzers + quarterly trend + catalyst tags on one name. No LLM (all free)."""
    ctx = build_context_for([symbol], as_of, horizon)
    if symbol not in ctx.close.columns:
        raise SystemExit(f"No price data for {symbol}. Run `tradeos ingest` (check the .NS/.BO suffix).")
    results = {a.name: a.run(ctx) for a in REGISTRY}
    risk_by_sym = {p["symbol"]: p for p in results["risk"]["positions"]}
    macro_by_sym = results["macro"].get("by_symbol", {})
    # single-name view: portfolio-RELATIVE fields (weight/risk-contribution/sector) are 100% by
    # construction — null them so the AI can't read "100% concentration" as real risk; keep vol/beta.
    risk_pos = dict(risk_by_sym.get(symbol, {}))
    risk_pos["weight_pct"] = risk_pos["risk_contribution_pct"] = None
    macro_one = dict(macro_by_sym.get(symbol) or {})
    macro_one.pop("sector_weight_pct", None)
    card = _build_card(symbol, results["technical"][symbol], risk_pos,
                       results["fundamental"].get(symbol), macro_one,
                       results["sentiment"].get(symbol), results["ownership"].get(symbol))
    card["news_headlines"] = _load_headlines(symbol)
    card["catalysts"] = catalysts(card["news_headlines"])          # P2: free event tags
    card["quarterly_trend"] = _quarterly_trend(ctx.fundamentals.get(symbol))  # P3: free results trend
    return card


# ----------------------------- the tiny digest + one verdict call -----------------------------

def _digest(f: dict) -> str:
    """Compress the full card to a few hundred tokens — only what the verdict needs."""
    t = f.get("technical") or {}
    td = t.get("dials", {})
    fn = f.get("fundamental") or {}
    fd = fn.get("dials", {}) if fn else {}
    rk = f.get("risk") or {}
    sn = f.get("sentiment") or {}
    ow = f.get("ownership") or {}
    L = [f"{f['symbol']} ₹{f.get('last_close')} sector={(f.get('macro') or {}).get('sector')}"]
    L.append(f"TECH: trend={td.get('trend')} mom={td.get('momentum')}(RSI {t.get('rsi_14')}) "
             f"level={td.get('level')} vs200SMA={t.get('price_vs_sma200_pct')}% "
             f"ret1m={t.get('ret_1m_pct')}% ret3m={t.get('ret_3m_pct')}%")
    if fn:
        L.append(f"FUND: rev {fd.get('revenue_growth')} (YoY {fn.get('revenue_yoy_pct')}%) · "
                 f"earn {fd.get('earnings_growth')} (NI YoY {fn.get('net_income_yoy_pct')}%) · "
                 f"margin {fd.get('margin_trend')} (net {fn.get('net_margin_pct')}% Δ{fn.get('net_margin_change_pp')}pp)")
    qt = f.get("quarterly_trend") or []
    if qt:
        L.append("QUARTERS: " + " | ".join(
            f"{q['q']} nm{q['net_margin_pct']}%" + (f" NI{q['net_income']:.0f}" if q['net_income'] is not None else "")
            for q in qt))
    L.append(f"RISK: vol {rk.get('vol_pct')}% beta {rk.get('beta')} | "
             f"OWN: inst {ow.get('institutional_pct')}% insider {ow.get('insider_pct')}%")
    L.append(f"NEWS: {sn.get('label')} ({sn.get('n_articles')} headlines)")
    cats = f.get("catalysts") or []
    if cats:
        L.append("CATALYSTS: " + " ; ".join(f"[{c['event']}] {(c['title'] or '')[:64]}" for c in cats[:6]))
    cred = f.get("credibility")
    if cred and cred.get("track_record"):
        L.append("MGMT CREDIBILITY: " + cred["track_record"])
    return "\n".join(L)


VERDICT_SYSTEM = """You are a senior equity analyst writing a SMALL verdict on one stock from precomputed facts.
Steelman the bull case AND the bear case, then give one honest reconciled read. Be TERSE: at most 3 bull, 3 bear,
3 watch — one short clause each, each citing a specific provided number/headline.
Rules: DESCRIPTIVE only — never say buy/sell/hold and never give a price target. If something has no data, say so.
Never invent a number. quarter_read = one line on what the latest quarterly result shows."""


class AnalystVerdict(BaseModel):
    one_line: str           # the small final read (descriptive)
    quarter_read: str       # the latest quarterly result, one line
    bull: list[str]         # <=3 terse, each with a cited number
    bear: list[str]         # <=3 terse, each with a cited number
    watch: list[str]        # <=3 terse triggers
    confidence: str         # high/medium/low + a few words why


def verdict(symbol: str, as_of=None, horizon: str = "annual", live_news: bool = True,
            save: bool = True) -> dict:
    # Live web news ONLY for a live read — fetching today's news for a historical `as_of` would be
    # look-ahead (Prime Directive #2). Cached within NEWS_TTL_HOURS so repeat briefs don't re-pay.
    news_status = None
    if live_news and as_of is None:
        from .news import refresh_news
        news_status = refresh_news(symbol)
    facts = assemble_facts(symbol, as_of, horizon)
    from .credibility import assess_credibility
    facts["credibility"] = assess_credibility(symbol, as_of)  # None unless concalls are ingested (free DB read)
    out = {"symbol": symbol, "facts": facts, "verdict": None, "usage": None, "news_status": news_status}
    try:
        resp = _get_client().messages.parse(
            model=ANALYST_MODEL, max_tokens=700,
            system=[{"type": "text", "text": VERDICT_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": _digest(facts)}],
            output_format=AnalystVerdict,
        )
        out["verdict"] = resp.parsed_output
        out["usage"] = getattr(resp, "usage", None)
    except Exception as e:  # noqa: BLE001
        log.warning("verdict failed for %s: %s", symbol, e)
    if save and out.get("verdict") is not None:
        _save_run(out)        # persist to analyst_runs so the dashboard can show past briefs
    return out


# ----------------------------- brief history (persistence) -----------------------------

def _json_safe(o):
    """Coerce engine output (numpy scalars, dates) to JSON-storable primitives for the JSONB payload."""
    if o is None or isinstance(o, (str, bool, int, float)):
        return o
    if hasattr(o, "item") and not isinstance(o, (list, tuple, dict)):
        try:
            return o.item()
        except Exception:  # noqa: BLE001
            return str(o)
    if isinstance(o, dict):
        return {str(k): _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_safe(v) for v in o]
    return str(o)


def _facts_snapshot(f: dict) -> dict:
    """A compact snapshot of the dials/numbers behind a brief — enough to render a history row."""
    t = f.get("technical") or {}
    td = t.get("dials", {})
    fd = (f.get("fundamental") or {}).get("dials", {}) if f.get("fundamental") else {}
    sn = f.get("sentiment") or {}
    return {
        "last_close": f.get("last_close"), "sector": (f.get("macro") or {}).get("sector"),
        "trend": td.get("trend"), "momentum": td.get("momentum"), "level": td.get("level"),
        "rsi_14": t.get("rsi_14"),
        "revenue_growth": fd.get("revenue_growth"), "earnings_growth": fd.get("earnings_growth"),
        "margin_trend": fd.get("margin_trend"),
        "attention": (f.get("attention") or {}).get("score"),
        "confidence": (f.get("confidence") or {}).get("level"),
        "news_label": sn.get("label"), "n_headlines": sn.get("n_articles"),
        "catalysts": [{"event": c.get("event"), "title": c.get("title")}
                      for c in (f.get("catalysts") or [])[:4]],
    }


def _run_cost(out: dict) -> float:
    """Total $ for one brief: the verdict call + (if a live search ran) the news fetch + search fee."""
    cost = 0.0
    u = out.get("usage")
    if u is not None:
        cost += getattr(u, "input_tokens", 0) / 1e6 * _PRICE_IN + getattr(u, "output_tokens", 0) / 1e6 * _PRICE_OUT
    ns = out.get("news_status") or {}
    if ns.get("fetched"):
        nu = ns.get("usage")
        if nu is not None:
            cost += getattr(nu, "input_tokens", 0) / 1e6 * _PRICE_IN + getattr(nu, "output_tokens", 0) / 1e6 * _PRICE_OUT
        cost += 0.02
    return round(cost, 4)


def _save_run(out: dict) -> None:
    """Persist one brief to analyst_runs. Best-effort — a storage hiccup must never break a brief."""
    v = out.get("verdict")
    if v is None:
        return
    vd = v.model_dump() if hasattr(v, "model_dump") else v
    f = out.get("facts") or {}
    cred = f.get("credibility") or {}
    ns = out.get("news_status") or {}
    payload = _json_safe({
        "verdict": vd,
        "snapshot": _facts_snapshot(f),
        "credibility": {"track_record": cred.get("track_record"), "checks": cred.get("checks")} if cred else None,
        "news": {"fetched": ns.get("fetched"), "n": ns.get("n"), "reason": ns.get("reason")},
    })
    try:
        from psycopg.types.json import Json

        from .db import get_connection
        with get_connection() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO analyst_runs (symbol, model, cost_usd, one_line, payload) VALUES (%s,%s,%s,%s,%s)",
                (out["symbol"], ANALYST_MODEL, _run_cost(out), vd.get("one_line"), Json(payload)),
            )
            c.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("could not save analyst run for %s: %s", out.get("symbol"), e)


def load_history(symbol: str, limit: int = 20) -> list[dict]:
    """Past briefs for one name, newest first (the history view's data)."""
    from .db import get_connection
    with get_connection() as c, c.cursor() as cur:
        cur.execute("SELECT id, run_at, model, cost_usd, one_line, payload FROM analyst_runs "
                    "WHERE symbol=%s ORDER BY run_at DESC LIMIT %s", (symbol.upper(), limit))
        return [{"id": i, "run_at": ra.isoformat(), "model": m, "cost_usd": cost, "one_line": ol,
                 **(pl or {})} for i, ra, m, cost, ol, pl in cur.fetchall()]


def load_recent(limit: int = 30) -> list[dict]:
    """Most recent briefs across ALL names (for a global runs feed)."""
    from .db import get_connection
    with get_connection() as c, c.cursor() as cur:
        cur.execute("SELECT id, symbol, run_at, model, cost_usd, one_line FROM analyst_runs "
                    "ORDER BY run_at DESC LIMIT %s", (limit,))
        return [{"id": i, "symbol": s, "run_at": ra.isoformat(), "model": m, "cost_usd": cost,
                 "one_line": ol} for i, s, ra, m, cost, ol in cur.fetchall()]


def _deterministic_verdict(f: dict) -> str:
    """Descriptive read from the dials — shown when no API key is set (always a final read)."""
    td = (f.get("technical") or {}).get("dials", {})
    fd = (f.get("fundamental") or {}).get("dials", {}) if f.get("fundamental") else {}
    sn = f.get("sentiment") or {}
    bits = []
    if td:
        bits.append(f"technically {td.get('trend')}/{td.get('momentum')} at {td.get('level')}")
    if fd:
        bits.append(f"fundamentals {fd.get('revenue_growth')} rev / {fd.get('earnings_growth')} earnings / "
                    f"{fd.get('margin_trend')} margins")
    if sn.get("label"):
        bits.append(f"{sn['label']} news")
    return "; ".join(bits) + "."


# ===================== the DEEP, multi-agent read (bull · bear · sector → judge) =====================
# The one-line `verdict` above is the fast, cheap read. `deep_analysis` is the "show me everything"
# read: three specialist agents (bull · bear · sector) reason over the SAME free digest IN PARALLEL,
# then a judge reconciles them into a structured DeepAnalysis — what's genuinely right/wrong, how the
# sector bears on the name, and DESCRIPTIVE conditional scenarios. Still bright-line clean: each agent
# reads the pure numbers, cites a provided figure/headline, never advises and never invents.

class CasePoint(BaseModel):
    point: str        # the claim, one clause
    evidence: str     # the specific provided number/headline that backs it


class SideCase(BaseModel):
    summary: str             # one-line framing of this side
    points: list[CasePoint]  # the cited points (<=5)


class SectorRead(BaseModel):
    sector: str
    backdrop: str       # how the sector is positioned right now (CONTEXT, not a live feed)
    company_fit: str    # how THIS name sits within that sector
    sensitivity: str    # the sector factors it is most exposed to (descriptive)


class Scenario(BaseModel):
    label: str          # e.g. "If demand recovers and margins hold"
    drivers: list[str]  # the conditions that would have to occur (descriptive, NOT predictions)
    implication: str    # what the setup would descriptively imply under that scenario


class DeepAnalysis(BaseModel):
    headline: str               # the one-line reconciled read (descriptive)
    thesis: str                 # 2-4 sentences: what this stock IS right now
    whats_right: list[str]      # what is genuinely working — each cites a number
    whats_wrong: list[str]      # what is genuinely concerning — each cites a number
    sector_context: str         # how the sector backdrop bears on the name (descriptive)
    quarter_read: str           # the latest quarterly result, explained
    scenarios: list[Scenario]   # bull / base / bear, descriptive conditionals
    what_to_watch: list[str]    # concrete factual triggers
    confidence: str             # high/medium/low + a few words why
    bottom_line: str            # the honest reconciled read (descriptive)


# Shared bright-line clause stitched into every deep prompt — the Prime Directives in prose.
_DESCRIPTIVE_RULES = (
    "DESCRIPTIVE only: never say buy/sell/hold and never give a price target or a return forecast. "
    "Cite a SPECIFIC provided number or headline for every claim. Never invent a number — if "
    "something has no data, say so plainly. You read the precomputed facts; you do not compute."
)

BULL_SYSTEM = ("You are the BULL analyst. From the precomputed facts, steelman the strongest HONEST "
               "bull case — what is genuinely working (growth, margins, momentum, ownership, "
               f"catalysts). At most 5 points, each citing a provided number/headline. {_DESCRIPTIVE_RULES}")

BEAR_SYSTEM = ("You are the BEAR analyst. From the precomputed facts, steelman the strongest HONEST "
               "bear case — what is genuinely concerning (deteriorating fundamentals, stretched "
               "technicals, risk, weak news/ownership). At most 5 points, each citing a provided "
               f"number/headline. {_DESCRIPTIVE_RULES}")

SECTOR_SYSTEM = ("You are the SECTOR/MACRO analyst. Given the name's sector tag and its own facts "
                 "(fundamental trend, catalysts, news flow), describe how the SECTOR backdrop bears "
                 "on this company and which sector factors it is most exposed to. We have NO live "
                 "sector-index or peer feed — treat any general sector view as CONTEXT, not data, and "
                 f"say so. {_DESCRIPTIVE_RULES}")

JUDGE_SYSTEM = ("You are the senior analyst writing the final DEEP read on one stock. You are given "
                "the precomputed facts plus a BULL case, a BEAR case and a SECTOR read from your team. "
                "Reconcile them honestly into: a thesis (what this stock IS now), what's genuinely "
                "RIGHT, what's genuinely WRONG, the sector context, the latest quarter, and 2-3 "
                "DESCRIPTIVE scenarios framed as conditionals ('IF x and y, THEN the setup descriptively "
                "implies z') — these describe what would have to happen, they are NOT predictions or "
                f"advice. End with an honest bottom line. {_DESCRIPTIVE_RULES}")


def _parse_call(model: str, system: str, user: str, output_format, max_tokens: int = 900):
    """One structured Claude call → (parsed_output, usage). Raises on failure (the caller decides)."""
    resp = _get_client().messages.parse(
        model=model, max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        output_format=output_format,
    )
    return resp.parsed_output, getattr(resp, "usage", None)


def _agg_usage(usages: list) -> dict:
    """Sum input/output tokens across the multi-agent calls."""
    it = sum(getattr(u, "input_tokens", 0) or 0 for u in usages if u is not None)
    ot = sum(getattr(u, "output_tokens", 0) or 0 for u in usages if u is not None)
    return {"input_tokens": it, "output_tokens": ot}


def _case_text(c) -> str:
    """A SideCase → plain text for the judge prompt (None ⇒ honest 'no data')."""
    if c is None:
        return "(no data)"
    pts = "\n".join(f"  - {p.point} [{p.evidence}]" for p in c.points)
    return f"{c.summary}\n{pts}"


def _sector_text(s) -> str:
    """A SectorRead → plain text for the judge prompt."""
    if s is None:
        return "(no data)"
    return f"sector={s.sector}\nbackdrop: {s.backdrop}\nfit: {s.company_fit}\nsensitivity: {s.sensitivity}"


def _deep_cost(out: dict) -> float:
    """Total $ for one deep read: the 4 Sonnet calls + (if it ran) the live-news fetch + search fee."""
    cost = 0.0
    u = out.get("usage")
    if u:
        cost += u.get("input_tokens", 0) / 1e6 * _DEEP_PRICE_IN + u.get("output_tokens", 0) / 1e6 * _DEEP_PRICE_OUT
    ns = out.get("news_status") or {}
    if ns.get("fetched"):
        nu = ns.get("usage")
        if nu is not None:
            cost += getattr(nu, "input_tokens", 0) / 1e6 * _PRICE_IN + getattr(nu, "output_tokens", 0) / 1e6 * _PRICE_OUT
        cost += 0.02
    return round(cost, 4)


def deep_analysis(symbol: str, as_of=None, horizon: str = "annual", live_news: bool = True,
                  save: bool = True) -> dict:
    """The deep, multi-agent read: bull · bear · sector specialists (parallel) → judge reconciliation.
    Descriptive only; degrades to facts (+ a deterministic read) when no API key is set. Persists each
    run to analyst_runs so it shows in the History tab. Live news/web only for a live read (as_of=None)."""
    news_status = None
    if live_news and as_of is None:
        from .news import refresh_news
        news_status = refresh_news(symbol)
    facts = assemble_facts(symbol, as_of, horizon)
    from .credibility import assess_credibility
    facts["credibility"] = assess_credibility(symbol, as_of)
    out: dict = {"symbol": symbol, "facts": facts, "deep": None, "debate": None, "usage": None,
                 "cost_usd": None, "model": DEEP_MODEL, "news_status": news_status}
    if not os.getenv("ANTHROPIC_API_KEY"):
        return out                      # facts are complete and free; the UI shows a deterministic read

    digest = _digest(facts)
    usages: list = []
    debate: dict = {}
    try:
        # 1) three specialists in parallel — independent reads over the same free digest
        specs = {"bull": (BULL_SYSTEM, SideCase), "bear": (BEAR_SYSTEM, SideCase),
                 "sector": (SECTOR_SYSTEM, SectorRead)}
        with ThreadPoolExecutor(max_workers=3) as ex:
            # generous ceilings — a truncated structured response fails the parse, not just shortens it
            futs = {ex.submit(_parse_call, DEEP_MODEL, sys_, digest, fmt, 1600): name
                    for name, (sys_, fmt) in specs.items()}
            for fut in as_completed(futs):
                parsed, usage = fut.result()
                debate[futs[fut]] = parsed
                usages.append(usage)
        # 2) the judge reconciles the three reads + the facts into the structured DeepAnalysis. This is
        # the largest output (10 fields incl. lists + scenarios) — give it room or the JSON gets cut off.
        judge_input = (f"{digest}\n\nBULL CASE:\n{_case_text(debate.get('bull'))}\n\n"
                       f"BEAR CASE:\n{_case_text(debate.get('bear'))}\n\n"
                       f"SECTOR READ:\n{_sector_text(debate.get('sector'))}")
        deep, usage = _parse_call(DEEP_MODEL, JUDGE_SYSTEM, judge_input, DeepAnalysis, max_tokens=4000)
        usages.append(usage)
        out["deep"] = deep
        out["debate"] = {k: (v.model_dump() if hasattr(v, "model_dump") else v) for k, v in debate.items()}
    except Exception as e:  # noqa: BLE001 - a failed deep read degrades to facts, never crashes a brief
        log.warning("deep_analysis failed for %s: %s", symbol, e)
        return out
    out["usage"] = _agg_usage(usages)
    out["cost_usd"] = _deep_cost(out)
    if save:
        _save_deep_run(out)
    return out


def _save_deep_run(out: dict) -> None:
    """Persist one deep read to analyst_runs (payload.deep + payload.debate). Best-effort — a storage
    hiccup must never break the read. one_line = the headline so the History list stays uniform."""
    deep = out.get("deep")
    if deep is None:
        return
    dd = deep.model_dump() if hasattr(deep, "model_dump") else deep
    f = out.get("facts") or {}
    cred = f.get("credibility") or {}
    ns = out.get("news_status") or {}
    payload = _json_safe({
        "deep": dd,
        "debate": out.get("debate"),
        "snapshot": _facts_snapshot(f),
        "credibility": {"track_record": cred.get("track_record"), "checks": cred.get("checks")} if cred else None,
        "news": {"fetched": ns.get("fetched"), "n": ns.get("n"), "reason": ns.get("reason")},
    })
    try:
        from psycopg.types.json import Json

        from .db import get_connection
        with get_connection() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO analyst_runs (symbol, model, cost_usd, one_line, payload) VALUES (%s,%s,%s,%s,%s)",
                (out["symbol"], out.get("model") or DEEP_MODEL, out.get("cost_usd"), dd.get("headline"), Json(payload)),
            )
            c.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("could not save deep analyst run for %s: %s", out.get("symbol"), e)


# ===================== ask-the-analyst: a follow-up over the WHOLE research =====================
# Distinct from docs.ask (RAG over filings ONLY). This agent sees the assembled facts + the latest
# deep read + retrieved filing chunks AND may web-search for current developments — so the desk can
# interrogate the whole research, not just the documents. Descriptive, cited, look-ahead-barred.

_ASK_RESEARCH_SYSTEM = (
    "You are the TradeOS analyst answering a follow-up question about ONE stock, grounded in the "
    "RESEARCH provided: precomputed point-in-time facts (technical/fundamental/risk/news/sector/"
    "ownership), the latest deep read if present, and numbered DOCUMENT EXCERPTS from the company's "
    "filings/concalls. You MAY use web_search for recent developments when the question needs current "
    "information, and should say when you did. Cite document excerpts inline as [1], [2] when you rely "
    "on them. DESCRIPTIVE only: never say buy/sell/hold and never give a price target or return "
    "forecast. Ground every claim in the provided facts, excerpts or your search results; never invent "
    "a number — if something is unknown, say so plainly."
)


def _latest_deep_summary(symbol: str) -> str | None:
    """Most recent persisted deep headline + bottom line, for follow-up context (one cheap DB read)."""
    try:
        from .db import get_connection
        with get_connection() as c, c.cursor() as cur:
            cur.execute("SELECT payload FROM analyst_runs WHERE symbol=%s AND payload->'deep' IS NOT NULL "
                        "ORDER BY run_at DESC LIMIT 1", (symbol.upper(),))
            row = cur.fetchone()
    except Exception:  # noqa: BLE001 - missing context is fine; the facts digest still grounds the answer
        return None
    if not row or not row[0]:
        return None
    d = (row[0] or {}).get("deep") or {}
    bits = [b for b in (d.get("headline"), d.get("bottom_line")) if b]
    return " — ".join(bits) or None


def _web_sources(resp) -> list[dict]:
    """Extract {title, url} from any web_search_tool_result blocks in a response (best-effort, deduped)."""
    out: list[dict] = []
    seen: set[str] = set()
    for b in getattr(resp, "content", []) or []:
        if getattr(b, "type", None) != "web_search_tool_result":
            continue
        for r in (getattr(b, "content", None) or []):
            url = getattr(r, "url", None)
            if url and url not in seen:
                seen.add(url)
                out.append({"title": getattr(r, "title", None) or url, "url": url})
    return out


def ask_research(symbol: str, question: str, *, as_of=None, allow_web: bool = True,
                 horizon: str = "annual", k: int = 5) -> dict:
    """Answer a follow-up about the WHOLE research for one name — grounded in the assembled facts + the
    latest deep read + retrieved filing chunks (RAG) + (live read only) web_search. Returns
    {answer, citations, hits, web_used, web_sources, note}. Degrades to retrieved excerpts + a note
    when no API key is set, exactly like docs.ask."""
    sym = symbol.upper()
    from .cache import memo
    facts = memo(("analyst-facts", sym, str(as_of), horizon),
                 lambda: assemble_facts(sym, as_of, horizon))   # repeat follow-ups reuse the facts
    from .docs import _valid_citations, search
    try:
        hits = search(sym, question, k)
    except Exception as e:  # noqa: BLE001 - no docs/embeddings shouldn't block a facts-only answer
        log.warning("doc search failed for %s: %s", sym, e)
        hits = []
    if not os.getenv("ANTHROPIC_API_KEY"):
        return {"answer": None, "citations": [], "hits": hits, "web_used": False, "web_sources": [],
                "note": "No ANTHROPIC_API_KEY — showing retrieved excerpts only."}

    deep_summary = _latest_deep_summary(sym)
    excerpts = "\n\n".join(f"[{i + 1}] (source: {h['source']})\n{h['content']}" for i, h in enumerate(hits))
    parts = [f"RESEARCH (point-in-time facts):\n{_digest(facts)}"]
    if deep_summary:
        parts.append(f"LATEST DEEP READ: {deep_summary}")
    parts.append("DOCUMENT EXCERPTS:\n" + (excerpts or "(no filings ingested for this name)"))
    parts.append(f"QUESTION: {question}")

    use_web = bool(allow_web and as_of is None)   # searching 'today' for a past as_of would leak the future
    kwargs: dict = {
        "model": CLAUDE_MODEL, "max_tokens": 1100,
        "system": [{"type": "text", "text": _ASK_RESEARCH_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": "\n\n".join(parts)}],
    }
    if use_web:
        kwargs["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]
    try:
        resp = _get_client().messages.create(**kwargs)
    except Exception as e:  # noqa: BLE001 - a failed answer still returns the retrieved evidence
        log.warning("ask_research failed for %s: %s", sym, e)
        return {"answer": None, "citations": [], "hits": hits, "web_used": False, "web_sources": [],
                "note": f"Answer failed: {str(e)[:80]}. Showing retrieved excerpts."}
    answer = "".join(getattr(b, "text", "") for b in resp.content
                     if getattr(b, "type", None) == "text").strip()
    web_sources = _web_sources(resp)
    cited = _valid_citations([int(m) for m in re.findall(r"\[(\d+)\]", answer)], len(hits))
    return {"answer": answer or None, "citations": cited, "hits": hits,
            "web_used": bool(web_sources), "web_sources": web_sources, "note": None}


# ----------------------------------- CLI -----------------------------------

def _fmt(v, suffix="", dash="—"):
    return dash if v is None else f"{v}{suffix}"


def _section(title: str) -> None:
    print(f"\n  {title}\n  " + "-" * (len(title) + 2))


def _print(out: dict) -> None:
    f = out["facts"]
    t = f.get("technical") or {}
    td = t.get("dials", {})
    fn = f.get("fundamental") or {}
    fd = fn.get("dials", {}) if fn else {}
    rk = f.get("risk") or {}
    sn = f.get("sentiment") or {}
    ow = f.get("ownership") or {}
    att = f.get("attention") or {}
    conf = f.get("confidence") or {}

    print(f"\n{'='*78}\n  {out['symbol']}   ·   ₹{_fmt(f.get('last_close'))}   ·   "
          f"{_fmt((f.get('macro') or {}).get('sector'))}\n{'='*78}")

    _section("TECHNICAL (deep)")
    print(f"    trend {_fmt(td.get('trend'))} · momentum {_fmt(td.get('momentum'))} (RSI {_fmt(t.get('rsi_14'))}) "
          f"· level {_fmt(td.get('level'))} ({_fmt(t.get('pct_from_52w_high'),'%')} from 52w high)")
    print(f"    price vs SMA200 {_fmt(t.get('price_vs_sma200_pct'),'%')} · "
          f"SMA20/50/200 {_fmt(t.get('sma20'))}/{_fmt(t.get('sma50'))}/{_fmt(t.get('sma200'))} · "
          f"MACD-hist {_fmt(t.get('macd_hist'))}")
    print(f"    return 1m {_fmt(t.get('ret_1m_pct'),'%')} / 3m {_fmt(t.get('ret_3m_pct'),'%')} · "
          f"volume {_fmt(t.get('volume_trend'))}")

    _section("FUNDAMENTAL + QUARTERLY RESULTS")
    if fn:
        print(f"    latest {_fmt(fn.get('latest_quarter'))}: revenue {_fmt(fd.get('revenue_growth'))} "
              f"(YoY {_fmt(fn.get('revenue_yoy_pct'),'%')} / QoQ {_fmt(fn.get('revenue_qoq_pct'),'%')}) · "
              f"earnings {_fmt(fd.get('earnings_growth'))} (NI YoY {_fmt(fn.get('net_income_yoy_pct'),'%')})")
        print(f"    margins {_fmt(fd.get('margin_trend'))}: net {_fmt(fn.get('net_margin_pct'),'%')} / "
              f"op {_fmt(fn.get('op_margin_pct'),'%')} (Δ {_fmt(fn.get('net_margin_change_pp'),'pp')})")
    else:
        print("    no quarterly data (ingest concall PDFs for depth)")
    for q in (f.get("quarterly_trend") or []):
        ni = q["net_income"]
        print(f"      {q['q']}  net margin {_fmt(q['net_margin_pct'],'%')}"
              + (f"  ·  net income {ni:,.0f}" if ni is not None else ""))

    _section("NEWS · CATALYSTS (auto-tagged)")
    print(f"    flow {_fmt(sn.get('label'))} · polarity {_fmt(sn.get('mean_polarity'))} "
          f"({_fmt(sn.get('pos_share_pct'),'%')} pos / {_fmt(sn.get('neg_share_pct'),'%')} neg, "
          f"{_fmt(sn.get('n_articles'))} headlines)")
    for c in (f.get("catalysts") or [])[:8]:
        print(f"      • [{_fmt(c['event'])}] [{_fmt(c['date'])}] {(c['title'] or '')[:82]}")
    if not (f.get("catalysts")):
        print("      • (no catalysts tagged)")

    _section("RISK · OWNERSHIP · DERIVED")
    print(f"    vol {_fmt(rk.get('vol_pct'),'%')} · beta {_fmt(rk.get('beta'))} | "
          f"institutional {_fmt(ow.get('institutional_pct'),'%')} · insider {_fmt(ow.get('insider_pct'),'%')}")
    drv = ("  — " + "; ".join(att["drivers"])) if att.get("drivers") else ""
    print(f"    attention {_fmt(att.get('score'))}/100{drv} · read-confidence {_fmt(conf.get('level'))}")

    cred = f.get("credibility")
    if cred:
        _section("MANAGEMENT CREDIBILITY (guidance → delivered)")
        print(f"    {_fmt(cred.get('track_record'))}")
        for ch in (cred.get("checks") or [])[:5]:
            print(f"      • [{ch.get('verdict')}] {ch.get('period')}: promised {ch.get('promised')} "
                  f"→ actual {ch.get('actual')}")
        if cred.get("caveat"):
            print(f"      caveat: {cred['caveat']}")

    # ----- the small final verdict -----
    print(f"\n{'='*78}\n  FINAL VERDICT\n{'='*78}")
    v = out["verdict"]
    if not v:
        print(f"  ▸ {_deterministic_verdict(f)}  (deterministic — set ANTHROPIC_API_KEY for the AI verdict)")
        return
    print(f"  ▸ {v.one_line}")
    print(f"  quarter: {v.quarter_read}\n")
    print("  BULL:  " + " | ".join(v.bull))
    print("  BEAR:  " + " | ".join(v.bear))
    print("  WATCH: " + " | ".join(v.watch))
    print(f"  confidence: {v.confidence}")
    ns = out.get("news_status") or {}
    nbits = ""
    if ns.get("fetched"):
        nu = ns.get("usage")
        ni = getattr(nu, "input_tokens", 0) if nu else 0
        no = getattr(nu, "output_tokens", 0) if nu else 0
        ncost = ni / 1e6 * _PRICE_IN + no / 1e6 * _PRICE_OUT + 0.02  # +~web-search fee
        nbits = f"  ·  live news: {ns.get('n')} items, {ni + no} tok +search ~${ncost:.3f}"
    elif ns.get("reason"):
        nbits = f"  ·  news: {ns['reason']}"
    u = out.get("usage")
    if u is not None:
        it, ot = getattr(u, "input_tokens", 0), getattr(u, "output_tokens", 0)
        cost = it / 1e6 * _PRICE_IN + ot / 1e6 * _PRICE_OUT
        print(f"\n  [verdict: 1 {ANALYST_MODEL} call · {it}+{ot} tok · ~${cost:.4f}{nbits}]")


def _print_deep(out: dict) -> None:
    """Print the deep multi-agent read (bull · bear · sector → judge)."""
    f = out["facts"]
    print(f"\n{'='*78}\n  {out['symbol']}   ·   ₹{_fmt(f.get('last_close'))}   ·   "
          f"{_fmt((f.get('macro') or {}).get('sector'))}   ·   DEEP READ\n{'='*78}")
    deep = out.get("deep")
    if not deep:
        print(f"  ▸ {_deterministic_verdict(f)}  (deterministic — set ANTHROPIC_API_KEY for the deep read)")
        return
    dbt = out.get("debate") or {}
    for side in ("bull", "bear"):
        c = dbt.get(side) or {}
        if c.get("points"):
            _section(f"{side.upper()} AGENT")
            print(f"    {c.get('summary', '')}")
            for p in c["points"]:
                print(f"      • {p.get('point')}  [{p.get('evidence')}]")
    sec = dbt.get("sector") or {}
    if sec:
        _section("SECTOR AGENT")
        print(f"    backdrop: {_fmt(sec.get('backdrop'))}")
        print(f"    fit: {_fmt(sec.get('company_fit'))}")
        print(f"    sensitivity: {_fmt(sec.get('sensitivity'))}")

    print(f"\n{'='*78}\n  JUDGE — DEEP ANALYSIS\n{'='*78}")
    print(f"  ▸ {deep.headline}\n\n  {deep.thesis}\n")
    print("  WHAT'S RIGHT:")
    for x in deep.whats_right:
        print(f"    + {x}")
    print("  WHAT'S WRONG:")
    for x in deep.whats_wrong:
        print(f"    - {x}")
    print(f"\n  SECTOR: {deep.sector_context}")
    print(f"  QUARTER: {deep.quarter_read}\n")
    for s in deep.scenarios:
        print(f"  SCENARIO — {s.label}")
        for d in s.drivers:
            print(f"      · {d}")
        print(f"      ⇒ {s.implication}")
    print("\n  WATCH: " + " | ".join(deep.what_to_watch))
    print(f"  confidence: {deep.confidence}")
    print(f"\n  BOTTOM LINE: {deep.bottom_line}")
    if out.get("cost_usd") is not None:
        u = out.get("usage") or {}
        print(f"\n  [deep: 4 {out.get('model')} calls · "
              f"{u.get('input_tokens', 0)}+{u.get('output_tokens', 0)} tok · ~${out['cost_usd']:.4f}]")


def main() -> None:
    ap = argparse.ArgumentParser(description="Multi-angle analyst brief for one name.")
    ap.add_argument("symbol", help="ticker, e.g. RELIANCE.NS")
    ap.add_argument("--as-of", help="point-in-time date YYYY-MM-DD")
    ap.add_argument("--horizon", default="annual")
    ap.add_argument("--no-live-news", action="store_true", help="skip the live web-news fetch (use cached/none)")
    ap.add_argument("--deep", action="store_true",
                    help="run the deep multi-agent read (bull · bear · sector → judge) instead of the one-liner")
    args = ap.parse_args()
    import datetime as dt
    as_of = dt.date.fromisoformat(args.as_of) if args.as_of else None
    sym, hz, live = args.symbol.upper(), args.horizon, not args.no_live_news
    if args.deep:
        _print_deep(deep_analysis(sym, as_of=as_of, horizon=hz, live_news=live))
    else:
        _print(verdict(sym, as_of=as_of, horizon=hz, live_news=live))


if __name__ == "__main__":
    if len(sys.argv) == 1:
        print("usage: python -m tradeos.analyst SYMBOL  (e.g. RELIANCE.NS)")
        raise SystemExit(2)
    main()
