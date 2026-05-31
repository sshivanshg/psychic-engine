"""`tradeos-risk` — compute the quant risk read, print it, then (optionally) the
plain-English desk read from Claude.

  uv run tradeos-risk                       # annual horizon (default) + Claude read if key set
  uv run tradeos-risk --no-llm              # numbers only
  uv run tradeos-risk --horizon weekly      # express vol & VaR over a week (d/w/m/q/y or Nd)
  uv run tradeos-risk --as-of 2025-06-30    # point-in-time: no look-ahead past that date
"""

import argparse
import datetime as dt

from .risk import compute_risk
from .risk_agent import narrate_risk


def _f(v, suffix: str = "") -> str:
    return "—" if v is None else f"{v}{suffix}"


def _row(label: str, value: str) -> None:
    print(f"  {label:<18}: {value}")


def _print_numbers(risk: dict) -> None:
    p = risk["portfolio"]
    bench = f"benchmark {risk['benchmark']}" if risk["benchmark"] else "no benchmark"
    hz = risk["horizon"]
    annual = risk["horizon_days"] == 252

    print(f"\nPortfolio risk — as of {risk['as_of']}  |  horizon: {hz}  ({bench})")
    print(f"  method: {risk['method']}")
    print("=" * 72)
    _row("Total value", _f(p["total_value"]))
    _row("Effective holdings", f"{_f(p['effective_holdings'])} of {p['num_holdings']}"
                               f"   (avg pairwise corr {_f(p['avg_pairwise_corr'])})")
    vol = _f(p["vol_pct"], "%")
    if not annual:
        vol += f"   (annualised {_f(p['vol_annual_pct'], '%')})"
    _row(f"Vol ({hz})", f"{vol}   |  beta {_f(p['beta'])}")
    _row(f"VaR / CVaR ({hz})",
         f"95% {_f(p['var_95_pct'], '%')} / {_f(p['cvar_95_pct'], '%')}"
         f"    99% {_f(p['var_99_pct'], '%')} / {_f(p['cvar_99_pct'], '%')}")
    if risk["horizon_days"] != 1:
        _row("(1-day 99% VaR)", _f(p["var_99_1d_pct"], "%"))
    _row("Worst windows", f"1d {_f(p['worst_1d_pct'], '%')}  5d {_f(p['worst_5d_pct'], '%')}"
                          f"  10d {_f(p['worst_10d_pct'], '%')}  21d {_f(p['worst_21d_pct'], '%')}")
    _row("Top risk driver", f"{_f(p['top_risk_contributor'])} @ {_f(p['top_risk_pct'], '%')} of risk")

    print(f"\n  Holdings (ranked by risk contribution, vol at {hz} horizon):")
    hdr = f"  {'symbol':<13}{'wt%':>7}{'risk%':>8}{'vol%':>8}{'beta':>7}{'DTL(d)':>8}{'P&L%':>8}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for r in risk["positions"]:
        print(
            f"  {r['symbol']:<13}{_f(r['weight_pct']):>7}{_f(r['risk_contribution_pct']):>8}"
            f"{_f(r['vol_pct']):>8}{_f(r['beta']):>7}{_f(r['days_to_liquidate']):>8}"
            f"{_f(r['unrealized_pnl_pct']):>8}"
        )

    limits = risk.get("limits", [])
    if limits:
        print("\n  Risk limits (checked at natural units — annual vol, 1-day VaR):")
        for c in limits:
            mark = "✓" if c["ok"] else "❗"
            note = f"  ({c['note']})" if c.get("note") else ""
            print(f"   {mark} {c['metric']}: {_f(c['value'])} vs limit {_f(c['limit'])}{note}")


def _print_report(rep) -> None:
    print("\nDesk risk read (Claude)")
    print("=" * 72)
    print(f"  {rep.headline}\n")
    if rep.risk_drivers:
        print("  Risk drivers:")
        for d in rep.risk_drivers:
            print(f"   • {d}")
    print(f"\n  Tails & stress : {rep.tail_and_stress}")
    print(f"  Diversification: {rep.diversification}")
    print(f"  Liquidity      : {rep.liquidity}")
    if rep.limit_flags:
        print("\n  Limit flags:")
        for flag in rep.limit_flags:
            print(f"   • {flag}")
    if rep.position_notes:
        print("\n  Per-holding notes:")
        for n in rep.position_notes:
            print(f"   • {n.symbol}: {n.note}")
    print(f"\n  {rep.reminder}")


def run(horizon: str = "annual", as_of=None, no_llm: bool = False) -> None:
    risk = compute_risk(as_of=as_of, horizon=horizon)
    _print_numbers(risk)
    if no_llm:
        return
    report = narrate_risk(risk)
    if report is None:
        print("\n(Set ANTHROPIC_API_KEY to get the plain-English desk read from Claude.)")
    else:
        _print_report(report)


def main() -> None:
    ap = argparse.ArgumentParser(description="Compute and explain portfolio risk (quant engine).")
    ap.add_argument("--horizon", default="annual",
                    help="risk horizon for vol & VaR: d/w/m/q/y or N days (default: annual)")
    ap.add_argument("--as-of", help="point-in-time date YYYY-MM-DD (no look-ahead past it)")
    ap.add_argument("--no-llm", action="store_true", help="skip the Claude narration")
    args = ap.parse_args()
    run(horizon=args.horizon,
        as_of=dt.date.fromisoformat(args.as_of) if args.as_of else None,
        no_llm=args.no_llm)


if __name__ == "__main__":
    main()
