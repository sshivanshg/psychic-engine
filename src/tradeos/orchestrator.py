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
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from pydantic import BaseModel

from .agents import REGISTRY
from .config import BENCHMARK, CLAUDE_MODEL
from .context import AnalysisContext
from .log import get_logger
from .scoring import compute_attention, compute_confidence
from .snapshots import annotate_deltas, save_run
from .trace import RunTrace, timed_call

log = get_logger()

# An event emitter: on_event(event_type, payload). Optional everywhere — when None, `analyze()` runs
# byte-identically to before (no observation overhead beyond a truthiness check). It is a pure
# OBSERVER of the run (the dashboard's live "Reasoning Monitor" uses it); it never changes a number.
EventEmitter = Callable[[str, dict], None]


def _short(sym) -> str:
    return str(sym).replace(".NS", "") if sym else "—"


def _counts(labels: list) -> str:
    """`['uptrend','uptrend','downtrend']` → `'2 uptrend · 1 downtrend'` (skips None)."""
    seen: dict[str, int] = {}
    for x in labels:
        if x:
            seen[x] = seen.get(x, 0) + 1
    return " · ".join(f"{n} {k}" for k, n in seen.items()) or "—"


def _context_summary(ctx: AnalysisContext) -> dict:
    """What the single point-in-time data load actually pulled — the run's provenance."""
    def _nonempty(v) -> bool:
        return v is not None and (len(v) > 0 if hasattr(v, "__len__") else bool(v))

    return {
        "as_of": str(ctx.as_of) if ctx.as_of else "latest",
        "horizon": ctx.horizon,
        "benchmark": BENCHMARK,
        "n_holdings": len(ctx.positions),
        "symbols": ctx.symbols,
        "price_rows": int(ctx.close.shape[0]) if getattr(ctx, "close", None) is not None else 0,
        "n_fundamentals": sum(1 for v in ctx.fundamentals.values() if _nonempty(v)),
        "n_with_guidance": sum(1 for v in ctx.guidance.values() if v),
        "n_with_sentiment": sum(1 for v in ctx.sentiment.values() if v),
        "n_with_ownership": sum(1 for v in ctx.ownership.values() if v),
        "n_sectors_known": sum(1 for v in ctx.sectors.values() if v),
    }


def _agent_summary(name: str, out: dict) -> dict:
    """A compact, human-readable read of one agent's output, for the live monitor. Pure."""
    if name == "risk":
        p = out.get("portfolio", {})
        return {
            "note": (f"book vol {p.get('vol_annual_pct', '—')}% ann · beta {p.get('beta', '—')} · "
                     f"99% 1d VaR {p.get('var_99_1d_pct', '—')}% · top risk "
                     f"{_short(p.get('top_risk_contributor'))} {p.get('top_risk_pct', '—')}%"),
            "metrics": {
                "vol ann %": p.get("vol_annual_pct"), "beta": p.get("beta"),
                "VaR99 1d %": p.get("var_99_1d_pct"), "CVaR99 %": p.get("cvar_99_pct"),
                "eff. holdings": p.get("effective_holdings"),
                "top risk": f"{_short(p.get('top_risk_contributor'))} · {p.get('top_risk_pct', '—')}%",
            },
            "warnings": out.get("data_warnings") or [],
            "method": out.get("method"),
        }
    if name == "technical":
        per = {s: {"trend": d.get("dials", {}).get("trend"), "momentum": d.get("dials", {}).get("momentum"),
                   "level": d.get("dials", {}).get("level"), "rsi": d.get("rsi_14")}
               for s, d in out.items()}
        return {"note": f"{len(per)} holdings · " + _counts([v["trend"] for v in per.values()]),
                "per_symbol": per}
    if name == "fundamental":
        per, n = {}, 0
        for s, d in out.items():
            if d:
                n += 1
                fd = d.get("dials", {})
                per[s] = {"revenue": fd.get("revenue_growth"), "earnings": fd.get("earnings_growth"),
                          "margin": fd.get("margin_trend"), "quarter": d.get("latest_quarter")}
            else:
                per[s] = {"revenue": None, "earnings": None, "margin": None, "quarter": None}
        return {"note": f"{n}/{len(out)} holdings have quarterly fundamentals on file", "per_symbol": per}
    if name == "macro":
        p = out.get("portfolio", {})
        return {"note": (f"top sector {p.get('top_sector', '—')} {p.get('top_sector_pct', '—')}% · "
                         f"concentration {p.get('concentration', '—')}"),
                "metrics": {"top sector": p.get("top_sector"), "top sector %": p.get("top_sector_pct"),
                            "eff. sectors": p.get("effective_sectors"), "concentration": p.get("concentration")}}
    if name == "sentiment":
        per = {s: {"label": d.get("label"), "headlines": d.get("n_articles"), "mean": d.get("mean_polarity")}
               for s, d in out.items() if d}
        n = sum(1 for d in out.values() if d and d.get("n_articles"))
        return {"note": f"news flow on {n} holding(s) · current snapshot, eval-barred", "per_symbol": per}
    if name == "ownership":
        per = {s: {"institutional %": d.get("institutional_pct"), "insider %": d.get("insider_pct")}
               for s, d in out.items() if d}
        return {"note": f"institutional/insider holding on {len(per)} name(s) · snapshot, eval-barred",
                "per_symbol": per}
    return {"note": "done"}

_client = None


def _get_client():
    """Lazily build one Anthropic client and reuse it (across cards AND requests) instead of
    constructing a fresh client per narration call."""
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic()
    return _client


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


def _rec_dump(rec) -> dict | None:
    """One CallRecord → a small JSON dict (tokens/latency/cost) for the live monitor."""
    if rec is None:
        return None
    return {"input_tokens": rec.input_tokens, "output_tokens": rec.output_tokens,
            "latency_ms": rec.latency_ms, "cost_usd": rec.cost_usd}


def narrate_cards(cards: list, on_event: EventEmitter | None = None) -> dict:
    """LLM synthesis for the given cards, concurrently. Pass a SUBSET (e.g. one card) to narrate just
    that holding — the API's per-stock route does this instead of narrating the whole book to serve
    one card. Returns {} (degrades) when no ANTHROPIC_API_KEY is set or `cards` is empty.

    `on_event` (optional) makes the run observable: narration_start, narration_done (per symbol, with
    the synthesis + token/cost), narration_error, narration_summary, narration_skipped."""
    emit = on_event or (lambda *a, **k: None)
    if not os.getenv("ANTHROPIC_API_KEY") or not cards:
        emit("narration_skipped",
             {"reason": "no ANTHROPIC_API_KEY — deterministic agent reads above are complete"
              if not os.getenv("ANTHROPIC_API_KEY") else "no cards"})
        return {}
    client = _get_client()
    trace = RunTrace()
    emit("narration_start", {"count": len(cards), "model": CLAUDE_MODEL})
    out: dict = {}
    # Per-stock synthesis is independent → run concurrently (the cards don't depend on each other).
    with ThreadPoolExecutor(max_workers=min(5, len(cards))) as ex:
        futs = {ex.submit(_narrate_card, client, c, trace): c["symbol"] for c in cards}
        for fut in as_completed(futs):
            sym = futs[fut]
            rep = fut.result()
            if rep is None:
                emit("narration_error", {"symbol": sym})
                continue
            out[sym] = rep
            rec = next((r for r in trace.records if r.label == f"synthesis:{sym}"), None)
            emit("narration_done", {"symbol": sym, "card": rep.model_dump(), "trace": _rec_dump(rec)})
    trace.print_summary()   # per-run token / latency / cost observability
    emit("narration_summary", trace.summary())
    return out


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


def analyze(as_of=None, horizon: str = "annual", narrate: bool = True, snapshot: bool = True,
            on_event: EventEmitter | None = None) -> dict:
    # `emit` is a no-op unless a caller (the live monitor) passes on_event — so the default path is
    # unchanged. It only OBSERVES the run; every number is still computed by the pure agents.
    emit = on_event or (lambda *a, **k: None)
    emit("run_start", {"as_of": str(as_of) if as_of else "latest", "horizon": horizon,
                       "narrate": bool(narrate)})

    # One shared context → agents run off a single data load, via the registry.
    ctx = AnalysisContext.build(as_of=as_of, horizon=horizon)
    emit("context_loaded", _context_summary(ctx))

    results: dict = {}
    for agent in REGISTRY:
        emit("agent_start", {"agent": agent.name, "scope": agent.scope})
        t0 = time.perf_counter()
        out = agent.run(ctx)
        results[agent.name] = out
        emit("agent_done", {"agent": agent.name, "scope": agent.scope,
                            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                            **_agent_summary(agent.name, out)})

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

    # Emit each per-holding card (the merged read: dials + attention decomposition + confidence) in
    # ranked order — this is "what the agents concluded" for the live monitor.
    emit("ranking", {"order": [c["symbol"] for c in cards]})
    for rank, c in enumerate(cards, 1):
        emit("card", {"rank": rank, **c})

    narratives = narrate_cards(cards, on_event=on_event) if narrate else {}
    result = {
        "as_of": risk["as_of"],
        "horizon": risk["horizon"],
        "risk_overview": risk["portfolio"],
        "sector_overview": macro.get("portfolio", {}),
        "cards": cards,
        "narratives": narratives,
    }
    emit("run_complete", {"as_of": result["as_of"], "horizon": result["horizon"],
                          "n_cards": len(cards), "n_narrated": len(narratives)})
    return result


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
