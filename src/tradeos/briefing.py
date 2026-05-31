"""Phase 5 — pre-market briefing + alert rules.

Composes the engine's `analyze()` output into a glanceable morning summary and fires alerts on YOUR
rules ("flag any holding that's the top risk contributor / guiding down on margins / changed since
yesterday"). The rule evaluation is PURE (operates on a card dict) so it's unit-tested without a DB
or network; only `run_briefing` touches the engine. Descriptive only — it flags, it never advises.

Roadmap "done when": every morning before open you get a portfolio briefing you trust enough to read.
"""

from .config import RISK_LIMITS
from .orchestrator import analyze

# --- alert rules: each takes a card dict → an alert string, or None. Tunable; add your own. ---


def _r_risk_concentration(card: dict) -> str | None:
    rc = (card.get("risk") or {}).get("risk_contribution_pct")
    lim = RISK_LIMITS["max_name_risk_pct"]
    return f"top risk contributor — {rc:.0f}% of book risk (≥ {lim:.0f}% limit)" if (
        rc is not None and rc >= lim) else None


def _r_downtrend_near_lows(card: dict) -> str | None:
    d = (card.get("technical") or {}).get("dials", {})
    return "in a downtrend and near 52-week lows" if (
        d.get("trend") == "downtrend" and d.get("level") == "near lows") else None


def _r_earnings_declining(card: dict) -> str | None:
    f = card.get("fundamental") or {}
    if (f.get("dials") or {}).get("earnings_growth") != "declining":
        return None
    ny = f.get("net_income_yoy_pct")
    return "earnings declining" + (f" ({ny:.0f}% YoY)" if ny is not None else "")


def _r_margin_contracting(card: dict) -> str | None:
    return "margins contracting" if (
        (card.get("fundamental") or {}).get("dials", {}).get("margin_trend") == "contracting") else None


def _r_negative_news(card: dict) -> str | None:
    s = card.get("sentiment") or {}
    return f"negative news flow ({s.get('n_articles')} headlines)" if s.get("label") == "negative" else None


def _r_changed_since_last(card: dict) -> str | None:
    changes = (card.get("delta") or {}).get("changes") or []
    return ("changed since last run — " + "; ".join(changes)) if changes else None


DEFAULT_RULES = [_r_risk_concentration, _r_downtrend_near_lows, _r_earnings_declining,
                 _r_margin_contracting, _r_negative_news, _r_changed_since_last]


def evaluate_alerts(card: dict, rules=None) -> list[str]:
    """All alerts a card trips, in rule order. Pure."""
    rules = DEFAULT_RULES if rules is None else rules
    return [a for r in rules if (a := r(card)) is not None]


def build_briefing(analysis: dict) -> dict:
    """Turn a full `analyze()` result into a briefing payload (pure — no engine/DB calls)."""
    stocks = []
    for c in analysis.get("cards", []):
        stocks.append({
            "symbol": c["symbol"],
            "attention": (c.get("attention") or {}).get("score"),
            "confidence": (c.get("confidence") or {}).get("level"),
            "alerts": evaluate_alerts(c),
        })
    flagged = [s for s in stocks if s["alerts"]]
    return {
        "as_of": analysis.get("as_of"),
        "horizon": analysis.get("horizon"),
        "risk_overview": analysis.get("risk_overview", {}),
        "sector_overview": analysis.get("sector_overview", {}),
        "stocks": stocks,
        "flagged": flagged,
        "n_flagged": len(flagged),
    }


def run_briefing(as_of=None, horizon: str = "annual") -> dict:
    """Run the full analysis and build the morning briefing (persists a snapshot for tomorrow's delta)."""
    return build_briefing(analyze(as_of=as_of, horizon=horizon, narrate=False, snapshot=True))


# ----------------------------------- CLI printout -----------------------------------

def _f(v, suffix: str = "") -> str:
    return "—" if v is None else f"{v}{suffix}"


def print_briefing(b: dict) -> None:
    p = b.get("risk_overview", {})
    print(f"\n📋 Pre-market briefing — as of {b.get('as_of')}  |  horizon {b.get('horizon')}")
    print("=" * 72)
    print(f"  Book vol {_f(p.get('vol_pct'), '%')}  |  beta {_f(p.get('beta'))}  |  "
          f"top risk {_f(p.get('top_risk_contributor'))} ({_f(p.get('top_risk_pct'), '%')})")
    sec = b.get("sector_overview", {})
    if sec.get("top_sector"):
        print(f"  Top sector {sec['top_sector']} ({_f(sec.get('top_sector_pct'), '%')})  |  "
              f"concentration {_f(sec.get('concentration'))}")
    if not b["flagged"]:
        print("\n  ✓ Nothing tripped your alert rules today.")
        return
    print(f"\n  ⚠ {b['n_flagged']} holding(s) flagged:\n")
    for s in b["flagged"]:
        print(f"  ▸ {s['symbol']}   attention {_f(s['attention'])}/100   confidence {_f(s['confidence'])}")
        for a in s["alerts"]:
            print(f"      • {a}")
    print("\n  (Descriptive flags on your rules — you make the call.)")
