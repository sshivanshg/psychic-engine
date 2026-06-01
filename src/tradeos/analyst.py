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
import sys

from pydantic import BaseModel

from .agents import REGISTRY
from .config import BENCHMARK, Position
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


def verdict(symbol: str, as_of=None, horizon: str = "annual") -> dict:
    facts = assemble_facts(symbol, as_of, horizon)
    out = {"symbol": symbol, "facts": facts, "verdict": None, "usage": None}
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
    return out


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
    u = out.get("usage")
    if u is not None:
        it, ot = getattr(u, "input_tokens", 0), getattr(u, "output_tokens", 0)
        cost = it / 1e6 * _PRICE_IN + ot / 1e6 * _PRICE_OUT
        print(f"\n  [1 {ANALYST_MODEL} call · {it} in + {ot} out tokens · ~${cost:.4f}]")


def main() -> None:
    ap = argparse.ArgumentParser(description="Multi-angle analyst brief (one small verdict) for one name.")
    ap.add_argument("symbol", help="ticker, e.g. RELIANCE.NS")
    ap.add_argument("--as-of", help="point-in-time date YYYY-MM-DD")
    ap.add_argument("--horizon", default="annual")
    args = ap.parse_args()
    import datetime as dt
    as_of = dt.date.fromisoformat(args.as_of) if args.as_of else None
    _print(verdict(args.symbol.upper(), as_of=as_of, horizon=args.horizon))


if __name__ == "__main__":
    if len(sys.argv) == 1:
        print("usage: python -m tradeos.analyst SYMBOL  (e.g. RELIANCE.NS)")
        raise SystemExit(2)
    main()
