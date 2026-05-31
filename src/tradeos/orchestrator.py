"""Orchestrator — the multi-agent core (hand-built, no framework yet).

Pipeline (build-your-own-graph; adopt LangGraph later only if it earns its place):

  1. Run the portfolio RISK agent once (risk.py)            ─┐
  2. Run the per-stock TECHNICAL agent (technical.py)        ─┤ facts (pure Python)
  3. Merge into one per-stock CARD: technical + that stock's ─┘
     risk slice + dials, ranked by risk contribution
  4. SYNTHESISE a reasoning trace per card via Claude, in PARALLEL (ThreadPoolExecutor)

Like the rest of the system: the LLM never computes — it only explains the merged facts, and
stays DESCRIPTIVE (no buy/sell). The factual cards (steps 1–3) work with no API key; step 4 is
added only when ANTHROPIC_API_KEY is set.
"""

import argparse
import datetime as dt
import json
import os
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel

from .agents import REGISTRY
from .config import CLAUDE_MODEL
from .context import AnalysisContext
from .log import get_logger
from .scoring import compute_attention, compute_confidence
from .snapshots import annotate_deltas, save_run
from .trace import RunTrace, timed_call

log = get_logger()


# ----------------------------- structured synthesis output -----------------------------

class StockCard(BaseModel):
    symbol: str
    technical_read: str    # what the trend/momentum/level say
    fundamental_read: str  # revenue/earnings growth, margins (or "no data")
    risk_read: str         # what this name contributes to portfolio risk
    synthesis: str         # the dimensions tied together (descriptive)
    watch_items: list[str]


SYNTH_SYSTEM = """You are an equity analyst writing a one-card read on a single holding in someone's
own portfolio. You are given precomputed TECHNICAL facts (trend/momentum/levels), FUNDAMENTAL facts
(revenue/earnings growth, margins), and the stock's RISK facts (contribution to portfolio risk, vol,
beta). Synthesise them.

Rules:
- Describe and interpret the numbers; cite them. Do NOT give buy / sell / hold recommendations or
  price targets — explain the picture, the human decides.
- Tie the dimensions together (e.g. "growing fundamentals but technically extended AND the book's
  largest risk contributor" is more useful than any one alone).
- If a dimension has no data, say so briefly rather than inventing it.
- An ATTENTION score (0-100) is provided: a DESCRIPTIVE measure of how much this holding warrants a
  look right now (notable risk/technical/fundamental/sector states) — NOT a buy/sell signal and not
  edge-validated. You may reference it and its drivers as a pointer, never as advice.
- watch_items = concrete, factual things to watch (levels, momentum flips, margin trend, risk concentration)."""


def _narrate_card(client, card: dict, trace: RunTrace | None = None) -> StockCard | None:
    try:
        resp = timed_call(
            trace, f"synthesis:{card['symbol']}", CLAUDE_MODEL,
            lambda: client.messages.parse(
                model=CLAUDE_MODEL,
                max_tokens=1200,
                system=[{"type": "text", "text": SYNTH_SYSTEM, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content":
                           f"Holding {card['symbol']}. Facts as JSON:\n\n{json.dumps(card, indent=2)}"}],
                output_format=StockCard,
            ),
        )
        return resp.parsed_output
    except Exception as e:  # noqa: BLE001 - one bad card shouldn't sink the whole run
        log.warning("synthesis failed for %s: %s", card.get("symbol"), e)
        return None


def _narrate_all(cards: list) -> dict:
    if not os.getenv("ANTHROPIC_API_KEY") or not cards:
        return {}
    import anthropic
    client = anthropic.Anthropic()
    trace = RunTrace()
    # Per-stock synthesis is independent → run concurrently (the cards don't depend on each other).
    with ThreadPoolExecutor(max_workers=min(5, len(cards))) as ex:
        pairs = list(ex.map(lambda c: (c["symbol"], _narrate_card(client, c, trace)), cards))
    trace.print_summary()   # per-run token / latency / cost observability
    return {sym: rep for sym, rep in pairs if rep is not None}


def _build_card(symbol: str, tech: dict, risk_pos: dict, fundamental: dict | None,
                macro: dict | None = None, sentiment: dict | None = None,
                ownership: dict | None = None) -> dict:
    card = {
        "symbol": symbol,
        "last_close": tech.get("last_close"),
        "technical": tech,
        "fundamental": fundamental,
        "macro": macro,
        "sentiment": sentiment,
        "ownership": ownership,
        "risk": {
            "weight_pct": risk_pos.get("weight_pct"),
            "risk_contribution_pct": risk_pos.get("risk_contribution_pct"),
            "vol_pct": risk_pos.get("vol_pct"),
            "beta": risk_pos.get("beta"),
            "max_drawdown_pct": risk_pos.get("max_drawdown_pct"),
        },
    }
    card["attention"] = compute_attention(card)
    card["confidence"] = compute_confidence(card)
    return card


def analyze(as_of=None, horizon: str = "annual", narrate: bool = True, snapshot: bool = True) -> dict:
    # One shared context → agents run off a single data load, via the registry.
    ctx = AnalysisContext.build(as_of=as_of, horizon=horizon)
    results = {agent.name: agent.run(ctx) for agent in REGISTRY}
    risk, tech, fund = results["risk"], results["technical"], results["fundamental"]
    macro = results.get("macro", {})
    sent, own = results.get("sentiment", {}), results.get("ownership", {})
    risk_by_sym = {p["symbol"]: p for p in risk["positions"]}
    macro_by_sym = macro.get("by_symbol", {})

    cards = [_build_card(s, t, risk_by_sym.get(s, {}), fund.get(s), macro_by_sym.get(s),
                         sent.get(s), own.get(s))
             for s, t in tech.items()]
    cards.sort(
        key=lambda c: (c["risk"].get("risk_contribution_pct") is not None,
                       c["risk"].get("risk_contribution_pct") or 0),
        reverse=True,
    )

    # what-changed delta: diff against the PRIOR run, then persist this run's snapshot.
    if snapshot:
        annotate_deltas(cards)
        save_run(cards, risk["as_of"])

    return {
        "as_of": risk["as_of"],
        "horizon": risk["horizon"],
        "risk_overview": risk["portfolio"],
        "sector_overview": macro.get("portfolio", {}),
        "cards": cards,
        "narratives": _narrate_all(cards) if narrate else {},
    }


# ----------------------------------- CLI -----------------------------------

def _f(v, suffix=""):
    return "—" if v is None else f"{v}{suffix}"


def _print(a: dict) -> None:
    p = a["risk_overview"]
    print(f"\nPortfolio analysis — as of {a['as_of']}  |  horizon: {a['horizon']}")
    print("=" * 72)
    print(f"  Book vol {_f(p['vol_pct'], '%')}  |  beta {_f(p['beta'])}  |  "
          f"top risk {_f(p['top_risk_contributor'])} ({_f(p['top_risk_pct'], '%')})")
    sec = a.get("sector_overview", {})
    if sec.get("top_sector"):
        print(f"  Top sector {sec['top_sector']} ({_f(sec['top_sector_pct'], '%')})  |  "
              f"effective sectors {_f(sec.get('effective_sectors'))}  |  "
              f"concentration {_f(sec.get('concentration'))}")
    if sec.get("flows_note"):
        print(f"  {sec['flows_note']}")
    print("\n  Per-stock cards (ranked by risk contribution):")
    for c in a["cards"]:
        t, r, d = c["technical"], c["risk"], c["technical"]["dials"]
        print(f"\n  ▸ {c['symbol']}    wt {_f(r['weight_pct'], '%')}   risk {_f(r['risk_contribution_pct'], '%')}"
              f"   beta {_f(r['beta'])}")
        att = c.get("attention", {})
        if att.get("score") is not None:
            drv = ("  — " + "; ".join(att["drivers"])) if att.get("drivers") else ""
            print(f"     attention : {att['score']:.0f}/100{drv}")
        print(f"     technical : trend={d['trend']}  momentum={d['momentum']} (RSI {_f(t['rsi_14'])})"
              f"  level={d['level']} ({_f(t['pct_from_52w_high'], '%')} from 52w high)")
        print(f"     price     : vs200SMA {_f(t['price_vs_sma200_pct'], '%')}  MACD-hist {_f(t['macd_hist'])}"
              f"  ret 1m {_f(t['ret_1m_pct'], '%')} / 3m {_f(t['ret_3m_pct'], '%')}  vol {_f(t['volume_trend'])}")
        fn = c.get("fundamental")
        if fn:
            fd = fn["dials"]
            print(f"     fundamental: revenue {_f(fd.get('revenue_growth'))} ({_f(fn['revenue_yoy_pct'], '%')} YoY)"
                  f"  earnings {_f(fd.get('earnings_growth'))} ({_f(fn['net_income_yoy_pct'], '%')})"
                  f"  margin {_f(fd.get('margin_trend'))} ({_f(fn['net_margin_pct'], '%')})")
            g = fn.get("guidance")
            if g:
                bits = [b for b in (g.get("revenue_outlook"), g.get("margin_outlook")) if b]
                if bits:
                    print(f"     guidance  : {'  |  '.join(bits)}  [{g.get('source', 'concall')}]")
        sn = c.get("sentiment")
        if sn:
            print(f"     sentiment : news flow {sn['label']} ({sn['n_articles']} headlines, "
                  f"mean {sn['mean_polarity']:+.2f})  [current snapshot, not point-in-time]")
        ow = c.get("ownership")
        if ow:
            inst_dial = (ow.get("dials") or {}).get("institutional") or "—"
            print(f"     ownership : institutional {_f(ow['institutional_pct'], '%')} ({inst_dial})  "
                  f"insider {_f(ow['insider_pct'], '%')}")
        conf = c.get("confidence")
        if conf:
            print(f"     confidence: {conf['level']} ({conf['score']:.2f}) — {'; '.join(conf['reasons'])}")
        d = c.get("delta")
        if d and d.get("changes"):
            print(f"     Δ vs last : {'; '.join(d['changes'])}  (since {str(d['since'])[:16]})")
        rep = a["narratives"].get(c["symbol"])
        if rep:
            print(f"     synthesis : {rep.synthesis}")
            for w in rep.watch_items:
                print(f"        ◦ {w}")
    if not a["narratives"]:
        print("\n  (Set ANTHROPIC_API_KEY for per-stock reasoning traces.)")


def run(horizon: str = "annual", as_of=None, no_llm: bool = False, no_snapshot: bool = False) -> None:
    _print(analyze(as_of=as_of, horizon=horizon, narrate=not no_llm, snapshot=not no_snapshot))


def main() -> None:
    ap = argparse.ArgumentParser(description="Multi-agent portfolio analysis (risk + technical + synthesis).")
    ap.add_argument("--horizon", default="annual", help="risk horizon: d/w/m/q/y or N days")
    ap.add_argument("--as-of", help="point-in-time date YYYY-MM-DD")
    ap.add_argument("--no-llm", action="store_true", help="skip the Claude synthesis")
    ap.add_argument("--no-snapshot", action="store_true", help="don't store/diff a run snapshot")
    args = ap.parse_args()
    run(horizon=args.horizon,
        as_of=dt.date.fromisoformat(args.as_of) if args.as_of else None,
        no_llm=args.no_llm, no_snapshot=args.no_snapshot)


if __name__ == "__main__":
    main()
