"""Phase 2 — the synthesized per-stock ATTENTION score (deterministic facts layer).

The roadmap's per-stock card = dials + a *synthesized score* + a reasoning trace. This is that
score — deliberately a DESCRIPTIVE one: it measures how much a holding **warrants your attention**
right now (notable states across risk, technical, fundamental and sector), NOT whether to buy or
sell. High = "look here", never "act". It's computed from the already-derived facts, so it's
deterministic and unit-testable; the LLM only explains it.

Honesty (the reason this is a score and not a "rating"): the sub-score weights are a transparent
HEURISTIC, equal by default. They are **not** validated by the Phase-4 eval harness — which, on the
current universe, found no signal with significant edge. So this is an attention *router*, not an
edge estimate. When the eval earns per-dimension weights, pass them via `weights` and the blend
updates with zero code change. Each dimension maps to a 0-100 sub-score (higher = more noteworthy);
the overall is the weighted mean of whichever dimensions are present.
"""

from .config import RISK_LIMITS, SECTOR_CONCENTRATION_PCT

# Dial → attention points (0-100). Extremes (good or bad) are noteworthy; calm states are not.
_MOMENTUM_PTS = {"overbought": 85, "oversold": 85, "strong": 45, "weak": 45, "neutral": 15}
_LEVEL_PTS = {"at highs": 70, "near lows": 70, "mid-range": 20}
_TREND_PTS = {"downtrend": 55, "sideways": 30, "uptrend": 20}
_GROWTH_PTS = {"declining": 85, "flat": 50, "growing": 25, "strong": 40}
_MARGIN_PTS = {"contracting": 80, "stable": 20, "expanding": 40}

# Sentiment & ownership are softer, current-snapshot signals (eval-barred) → lower default weight.
DEFAULT_WEIGHTS = {"risk": 1.0, "technical": 1.0, "fundamental": 1.0, "macro": 1.0,
                   "sentiment": 0.5, "ownership": 0.5}


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _risk_subscore(risk: dict):
    rc = risk.get("risk_contribution_pct")
    if rc is None:
        return None, []
    score = _clamp(100 * rc / RISK_LIMITS["max_name_risk_pct"])
    wt, drivers = risk.get("weight_pct"), []
    if rc >= RISK_LIMITS["max_name_risk_pct"]:
        drivers.append(f"top risk contributor — {rc:.0f}% of book risk (≥ {RISK_LIMITS['max_name_risk_pct']:.0f}% limit)")
    elif wt and rc > wt * 1.2:
        drivers.append(f"risk-dense: {rc:.0f}% of risk vs {wt:.0f}% of capital")
    elif rc >= 25:
        drivers.append(f"{rc:.0f}% of book risk")
    return score, drivers


def _technical_subscore(tech: dict):
    dials = tech.get("dials", {})
    pts, drivers = [], []
    mom = dials.get("momentum")
    if mom in _MOMENTUM_PTS:
        pts.append(_MOMENTUM_PTS[mom])
        if mom in ("overbought", "oversold"):
            rsi = tech.get("rsi_14")
            drivers.append(mom + (f" (RSI {rsi:.0f})" if rsi is not None else ""))
    lvl = dials.get("level")
    if lvl in _LEVEL_PTS:
        pts.append(_LEVEL_PTS[lvl])
        if lvl in ("at highs", "near lows"):
            pfh = tech.get("pct_from_52w_high")
            drivers.append(lvl + (f" ({pfh:.0f}% from 52w high)" if pfh is not None else ""))
    trend = dials.get("trend")
    if trend in _TREND_PTS:
        pts.append(_TREND_PTS[trend])
        if trend == "downtrend":
            drivers.append("in a downtrend")
    return (sum(pts) / len(pts), drivers) if pts else (None, [])


def _fundamental_subscore(fund: dict):
    dials = fund.get("dials", {})
    pts, drivers = [], []
    if (rg := dials.get("revenue_growth")) in _GROWTH_PTS:
        pts.append(_GROWTH_PTS[rg])
    if (eg := dials.get("earnings_growth")) in _GROWTH_PTS:
        pts.append(_GROWTH_PTS[eg])
        if eg == "declining":
            ny = fund.get("net_income_yoy_pct")
            drivers.append("earnings declining" + (f" ({ny:.0f}% YoY)" if ny is not None else ""))
    if (mt := dials.get("margin_trend")) in _MARGIN_PTS:
        pts.append(_MARGIN_PTS[mt])
        if mt == "contracting":
            drivers.append("margins contracting")
    return (sum(pts) / len(pts), drivers) if pts else (None, [])


def _macro_subscore(macro: dict):
    sw, sector = macro.get("sector_weight_pct"), macro.get("sector")
    if sw is None:
        return None, []
    score = _clamp(100 * sw / SECTOR_CONCENTRATION_PCT)
    drivers = [f"{sector or 'sector'} is {sw:.0f}% of the book"] if sw >= SECTOR_CONCENTRATION_PCT else []
    return score, drivers


def _sentiment_subscore(sent: dict):
    mp = sent.get("mean_polarity")
    if mp is None:
        return None, []
    score = _clamp(abs(mp) * 100)           # strong news flow (either sign) is noteworthy
    drivers = []
    label = sent.get("label")
    if label in ("positive", "negative") and abs(mp) >= 0.3:
        drivers.append(f"{label} news flow ({sent.get('n_articles')} headlines)")
    return score, drivers


def _ownership_subscore(own: dict):
    inst = own.get("institutional_pct")
    if inst is None:
        return None, []
    score = _clamp(abs(inst - 50) * 1.4)    # 50% institutional = least remarkable; extremes warrant a look
    drivers = [f"institutional holding {inst:.0f}%"] if (inst >= 70 or inst <= 15) else []
    return score, drivers


def compute_attention(card: dict, weights: dict | None = None) -> dict:
    """Blend the per-dimension sub-scores of one card into a 0-100 attention score. Pure/testable."""
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    subs: dict[str, float | None] = {}
    drivers: list[str] = []
    for dim, fn in (("risk", _risk_subscore), ("technical", _technical_subscore),
                    ("fundamental", _fundamental_subscore), ("macro", _macro_subscore),
                    ("sentiment", _sentiment_subscore), ("ownership", _ownership_subscore)):
        s, ds = fn(card.get(dim) or {})
        subs[dim] = round(s, 1) if s is not None else None
        drivers += ds

    present = {d: v for d, v in subs.items() if v is not None}
    overall = None
    if present:
        num = sum(present[d] * w.get(d, 1.0) for d in present)
        den = sum(w.get(d, 1.0) for d in present)
        overall = round(num / den, 1) if den else None

    return {
        "score": overall,
        "components": subs,
        "drivers": drivers[:4],
        "note": ("descriptive attention score — where to look, not buy/sell; heuristic equal weights, "
                 "not yet edge-validated by the eval harness"),
    }


# ----------------------------- calibrated confidence -----------------------------
# Confidence in the READ — calibrated to (a) how complete the data is, (b) how deep/hard the inputs
# are, and (c) whether the available signals point the same way. It is NOT a probability of profit:
# the signals aren't edge-validated, so a "high" means "this description rests on complete, coherent
# data", never "this will go up". Pure & deterministic so it's unit-testable.

EXPECTED_DIMS = ("risk", "technical", "fundamental", "macro", "sentiment", "ownership")

# Direction of each categorical read: +1 constructive / -1 deteriorating / 0 neutral. Used only to
# measure COHERENCE (do the dimensions agree?), never as a buy/sell vote.
_TREND_DIR = {"uptrend": 1, "sideways": 0, "downtrend": -1}
_MOM_DIR = {"overbought": 1, "strong": 1, "neutral": 0, "weak": -1, "oversold": -1}
_LEVEL_DIR = {"at highs": 1, "mid-range": 0, "near lows": -1}
_GROWTH_DIR = {"strong": 1, "growing": 1, "flat": 0, "declining": -1}
_MARGIN_DIR = {"expanding": 1, "stable": 0, "contracting": -1}
_SENT_DIR = {"positive": 1, "neutral": 0, "negative": -1}


def _present_dims(card: dict) -> list[str]:
    """Which dimensions actually contributed data to this card (None / empty ⇒ absent)."""
    present = []
    risk = card.get("risk") or {}
    if risk.get("risk_contribution_pct") is not None:
        present.append("risk")
    if (card.get("technical") or {}).get("dials"):
        present.append("technical")
    fund = card.get("fundamental") or {}
    if fund.get("latest_quarter") or fund.get("guidance"):
        present.append("fundamental")
    if (card.get("macro") or {}).get("sector_weight_pct") is not None:
        present.append("macro")
    if (card.get("sentiment") or {}).get("label") not in (None, "no-data"):
        present.append("sentiment")
    if (card.get("ownership") or {}).get("dials"):
        present.append("ownership")
    return present


def _dir(mapping: dict, key) -> int | None:
    """Look up a categorical direction, tolerating a missing/None dial (no read ⇒ None)."""
    return mapping.get(key) if isinstance(key, str) else None


def _coherence(card: dict) -> tuple[float | None, list[str]]:
    """Directional agreement across available reads: 1 = all point the same way, 0 = evenly split.
    None when fewer than two directional reads exist (nothing to corroborate)."""
    t = (card.get("technical") or {}).get("dials", {})
    f = (card.get("fundamental") or {}).get("dials", {})
    s = card.get("sentiment") or {}
    signs = [
        _dir(_TREND_DIR, t.get("trend")), _dir(_MOM_DIR, t.get("momentum")), _dir(_LEVEL_DIR, t.get("level")),
        _dir(_GROWTH_DIR, f.get("earnings_growth")), _dir(_MARGIN_DIR, f.get("margin_trend")),
        _dir(_SENT_DIR, s.get("label")),
    ]
    vals = [v for v in signs if v is not None]
    if len(vals) < 2:
        return None, []
    mag = sum(abs(v) for v in vals)
    if mag == 0:
        return 0.5, ["all reads neutral"]
    coh = abs(sum(vals)) / mag
    note = ["signals coherent" if coh >= 0.66 else "signals mixed" if coh < 0.34 else "signals partly aligned"]
    return coh, note


def compute_confidence(card: dict) -> dict:
    """A 0-1 confidence in the card's READ + a {high,medium,low} level and the reasons behind it."""
    present = _present_dims(card)
    completeness = len(present) / len(EXPECTED_DIMS)

    fund = card.get("fundamental") or {}
    tech = card.get("technical") or {}
    depth_flags = [
        bool(fund.get("latest_quarter")),                 # hard quarterly numbers, not just guidance
        tech.get("sma200") is not None,                   # ≥200 sessions ⇒ trend dials are meaningful
        bool(fund.get("guidance")),                       # management commentary on file
    ]
    depth = sum(depth_flags) / len(depth_flags)

    coh, coh_note = _coherence(card)
    coh_val = 0.5 if coh is None else coh

    score = round(0.4 * completeness + 0.3 * depth + 0.3 * coh_val, 2)
    level = "high" if score >= 0.66 else "medium" if score >= 0.4 else "low"

    reasons = [f"{len(present)}/{len(EXPECTED_DIMS)} dimensions present"]
    if not fund.get("latest_quarter"):
        reasons.append("no quarterly fundamentals")
    reasons += coh_note
    return {
        "score": score,
        "level": level,
        "present_dims": present,
        "reasons": reasons[:4],
        "note": ("confidence in the READ, from data completeness + depth + signal coherence — NOT a "
                 "probability of return (signals aren't edge-validated)"),
    }
