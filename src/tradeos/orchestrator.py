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

from .config import CLAUDE_MODEL
from .risk import compute_risk
from .technical import compute_all_technical


# ----------------------------- structured synthesis output -----------------------------

class StockCard(BaseModel):
    symbol: str
    technical_read: str   # what the trend/momentum/level say
    risk_read: str        # what this name contributes to portfolio risk
    synthesis: str        # the two dimensions tied together (descriptive)
    watch_items: list[str]


SYNTH_SYSTEM = """You are an equity analyst writing a one-card read on a single holding in someone's
own portfolio. You are given precomputed TECHNICAL facts (trend/momentum/levels) and the stock's
RISK facts (its contribution to portfolio risk, vol, beta). Synthesise them.

Rules:
- Describe and interpret the numbers; cite them. Do NOT give buy / sell / hold recommendations or
  price targets — explain the picture, the human decides.
- Tie technical and risk together (e.g. "extended on momentum AND the book's largest risk
  contributor" is a more important observation than either alone).
- watch_items = concrete, factual things to keep an eye on (levels, momentum flips, risk concentration)."""


def _narrate_card(client, card: dict) -> StockCard | None:
    try:
        resp = client.messages.parse(
            model=CLAUDE_MODEL,
            max_tokens=1200,
            system=[{"type": "text", "text": SYNTH_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content":
                       f"Holding {card['symbol']}. Facts as JSON:\n\n{json.dumps(card, indent=2)}"}],
            output_format=StockCard,
        )
        return resp.parsed_output
    except Exception:  # noqa: BLE001 - one bad card shouldn't sink the whole run
        return None


def _narrate_all(cards: list) -> dict:
    if not os.getenv("ANTHROPIC_API_KEY") or not cards:
        return {}
    import anthropic
    client = anthropic.Anthropic()
    # Per-stock synthesis is independent → run concurrently (the cards don't depend on each other).
    with ThreadPoolExecutor(max_workers=min(5, len(cards))) as ex:
        pairs = list(ex.map(lambda c: (c["symbol"], _narrate_card(client, c)), cards))
    return {sym: rep for sym, rep in pairs if rep is not None}


def _build_card(symbol: str, tech: dict, risk_pos: dict) -> dict:
    return {
        "symbol": symbol,
        "last_close": tech.get("last_close"),
        "technical": tech,
        "risk": {
            "weight_pct": risk_pos.get("weight_pct"),
            "risk_contribution_pct": risk_pos.get("risk_contribution_pct"),
            "vol_pct": risk_pos.get("vol_pct"),
            "beta": risk_pos.get("beta"),
            "max_drawdown_pct": risk_pos.get("max_drawdown_pct"),
        },
    }


def analyze(as_of=None, horizon: str = "annual", narrate: bool = True) -> dict:
    risk = compute_risk(as_of=as_of, horizon=horizon)
    tech = compute_all_technical(as_of)
    risk_by_sym = {p["symbol"]: p for p in risk["positions"]}

    cards = [_build_card(s, t, risk_by_sym.get(s, {})) for s, t in tech.items()]
    cards.sort(
        key=lambda c: (c["risk"].get("risk_contribution_pct") is not None,
                       c["risk"].get("risk_contribution_pct") or 0),
        reverse=True,
    )

    return {
        "as_of": risk["as_of"],
        "horizon": risk["horizon"],
        "risk_overview": risk["portfolio"],
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
    print("\n  Per-stock cards (ranked by risk contribution):")
    for c in a["cards"]:
        t, r, d = c["technical"], c["risk"], c["technical"]["dials"]
        print(f"\n  ▸ {c['symbol']}    wt {_f(r['weight_pct'], '%')}   risk {_f(r['risk_contribution_pct'], '%')}"
              f"   beta {_f(r['beta'])}")
        print(f"     technical : trend={d['trend']}  momentum={d['momentum']} (RSI {_f(t['rsi_14'])})"
              f"  level={d['level']} ({_f(t['pct_from_52w_high'], '%')} from 52w high)")
        print(f"     price     : vs200SMA {_f(t['price_vs_sma200_pct'], '%')}  MACD-hist {_f(t['macd_hist'])}"
              f"  ret 1m {_f(t['ret_1m_pct'], '%')} / 3m {_f(t['ret_3m_pct'], '%')}  vol {_f(t['volume_trend'])}")
        rep = a["narratives"].get(c["symbol"])
        if rep:
            print(f"     synthesis : {rep.synthesis}")
            for w in rep.watch_items:
                print(f"        ◦ {w}")
    if not a["narratives"]:
        print("\n  (Set ANTHROPIC_API_KEY for per-stock reasoning traces.)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Multi-agent portfolio analysis (risk + technical + synthesis).")
    ap.add_argument("--horizon", default="annual", help="risk horizon: d/w/m/q/y or N days")
    ap.add_argument("--as-of", help="point-in-time date YYYY-MM-DD")
    ap.add_argument("--no-llm", action="store_true", help="skip the Claude synthesis")
    args = ap.parse_args()
    as_of = dt.date.fromisoformat(args.as_of) if args.as_of else None
    _print(analyze(as_of=as_of, horizon=args.horizon, narrate=not args.no_llm))


if __name__ == "__main__":
    main()
