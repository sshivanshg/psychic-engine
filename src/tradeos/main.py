"""Unified `tradeos` CLI — manage the portfolio and run the analyses.

  tradeos add RELIANCE.NS 10 2400      # add/update a holding (avg_cost optional); fetches its data
  tradeos add INFY.NS 20 --no-fetch    # add without fetching now
  tradeos remove ITC.NS
  tradeos holdings                     # list your portfolio
  tradeos ingest                       # refresh all price data
  tradeos check                        # DB row counts
  tradeos risk    [--horizon d/w/m/q/y] [--as-of YYYY-MM-DD] [--no-llm]
  tradeos analyze [--horizon d/w/m/q/y] [--as-of YYYY-MM-DD] [--no-llm]

(The individual `tradeos-ingest` / `tradeos-risk` / … scripts still work too.)
"""

import argparse
import datetime as dt


def _q(x: float):
    return int(x) if float(x).is_integer() else x


def _f(v, suffix: str = "") -> str:
    return "—" if v is None else f"{v}{suffix}"


def _print_holdings(positions) -> None:
    if not positions:
        print("\nNo holdings yet.  Add one:  tradeos add RELIANCE.NS 10 2400\n")
        return
    print(f"\n  {'symbol':<16}{'qty':>10}{'avg_cost':>12}")
    print("  " + "-" * 38)
    for p in positions:
        cost = "—" if p.avg_cost is None else _q(p.avg_cost)
        print(f"  {p.symbol:<16}{_q(p.quantity)!s:>10}{cost!s:>12}")
    print()


def _cmd_add(args):
    from .config import add_holding
    positions = add_holding(args.symbol, args.quantity, args.avg_cost)
    print(f"✓ added {args.symbol.upper()}")
    if not args.no_fetch:
        from .ingest import ingest_symbols
        print("fetching price history…")
        ingest_symbols([args.symbol.upper()], with_benchmark=True)
    _print_holdings(positions)


def _cmd_remove(args):
    from .config import remove_holding
    print(f"✓ removed {args.symbol.upper()}")
    _print_holdings(remove_holding(args.symbol))


def _cmd_holdings(args):
    from .config import _safe_load
    _print_holdings(_safe_load())


def _cmd_ingest(args):
    from .ingest import ingest
    ingest()


def _cmd_check(args):
    from .check import main as check_main
    check_main()


def _cmd_docs_add(args):
    from .docs import add_document
    period = dt.date.fromisoformat(args.period) if args.period else None
    filing = dt.date.fromisoformat(args.filing_date) if args.filing_date else None
    n = add_document(args.symbol, args.path, period=period, filing_date=filing, source_url=args.url)
    tag = f"  (period {args.period})" if args.period else "  (no --period: won't count for freshness)"
    print(f"✓ {args.symbol.upper()}: stored {n} chunks from {args.path}{tag}" if n
          else f"no text extracted from {args.path}")


def _cmd_docs_list(args):
    from .db import get_connection
    where, params = ("WHERE symbol=%s", [args.symbol.upper()]) if args.symbol else ("", [])
    with get_connection() as c, c.cursor() as cur:
        cur.execute(f"SELECT symbol, source, count(*), max(period) FROM doc_chunks {where} "
                    f"GROUP BY symbol, source ORDER BY symbol, source", params)
        rows = cur.fetchall()
    if not rows:
        print("No documents ingested. Add one:  tradeos docs add INFY.NS report.pdf")
        return
    for sym, src, n, period in rows:
        meta = f"  period {period}" if period else "  (no period tag)"
        print(f"  {sym:<14} {src}  ({n} chunks){meta}")


def _cmd_docs_status(args):
    from .docs import coverage_status
    rows = coverage_status([args.symbol.upper()] if args.symbol else None)
    if not rows:
        print("\nNo holdings yet.  Add one:  tradeos add RELIANCE.NS 10\n")
        return
    print(f"\nDocument coverage — {len(rows)} holding(s)")
    print("  expected = latest quarter in `fundamentals`; transcript = latest ingested doc `period`")
    print("=" * 82)
    hdr = (f"  {'symbol':<14}{'status':<11}{'latest results':<16}{'latest transcript':<19}"
           f"{'docs':>5}{'ingested':>13}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    attention = 0
    for r in rows:
        if r["flag"] in ("MISSING", "STALE", "UNTAGGED"):
            attention += 1
        li = str(r["last_ingested"])[:10] if r["last_ingested"] else "—"
        print(f"  {r['symbol']:<14}{r['flag']:<11}{str(r['latest_results'] or '—'):<16}"
              f"{str(r['latest_transcript'] or '—'):<19}{r['docs']:>5}{li:>13}")
    if attention:
        print(f"\n  ⚠ {attention} holding(s) need attention (MISSING / STALE / UNTAGGED).")
        print("    Add the latest transcript:  tradeos docs add SYMBOL <file.pdf> --period YYYY-MM-DD")
    else:
        print("\n  ✓ every holding's latest reported quarter has a transcript on file.")


def _cmd_ask(args):
    from .docs import ask
    res = ask(args.symbol, args.question)
    if not res["hits"]:                       # no documents ingested for this symbol
        print(res.get("note") or "No results.")
        return
    if res.get("weak_evidence") and res.get("note"):
        print(f"\n⚠ {res['note']}")
    if res.get("answer"):
        print(f"\n{res['answer']}")
        if res.get("citations"):
            print(f"\ncited: {', '.join(f'[{c}]' for c in res['citations'])}")
    else:
        print("\n(No ANTHROPIC_API_KEY — showing the retrieved excerpts instead of a synthesised answer.)")
    print("\nsources (cosine distance):")
    for i, h in enumerate(res["hits"], 1):
        snippet = " ".join(h["content"][:150].split())
        print(f"  [{i}] {h['source']}#{h['chunk']}  d={h['distance']}  {snippet}…")


def _cmd_eval(args):
    from .eval import evaluate
    r = evaluate(horizon=args.horizon, step=args.step)
    print(f"\nSignal eval — {r['horizon_days']}d forward return · {r['universe']} names · "
          f"pooled sampled every {r['step_days']}d")
    print("  IC = cross-sectional rank IC (per-date Spearman across names, averaged); "
          "t = Newey-West (overlap-adjusted)")
    print("=" * 88)
    hdr = (f"  {'signal':<16}{'dates':>6}{'IC':>7}{'ICIR':>7}{'t':>7}"
           f"{'hit%':>7}{'base%':>7}{'LS%':>8}{'poolIC':>8}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for name, s in r["signals"].items():
        print(f"  {name:<16}{s['n_dates']:>6}{_f(s['ic']):>7}{_f(s['icir']):>7}{_f(s['t_stat']):>7}"
              f"{_f(s['hit_rate_pct']):>7}{_f(s['base_rate_pct']):>7}"
              f"{_f(s['ls_spread_pct']):>8}{_f(s['pooled_ic']):>8}")
    print(f"\n  Read it: |t| ≳ 2 ≈ significant (Newey-West lag {r['nw_lag']}d). hit% vs a 50% null; "
          f"base% = P(fwd>0). LS% = top−bottom tercile, gross of cost.")
    print("  Small universe ⇒ illustrative, not conclusive: a 5-name cross-section is underpowered, "
          "so t will usually read 'noise'.")
    print("  poolIC mixes time + cross-section (inflated n) — diagnostic only, never the headline.")


def _as_of(args):
    return dt.date.fromisoformat(args.as_of) if getattr(args, "as_of", None) else None


def _cmd_risk(args):
    from .cli import run
    run(horizon=args.horizon, as_of=_as_of(args), no_llm=args.no_llm)


def _cmd_analyze(args):
    from .orchestrator import run
    run(horizon=args.horizon, as_of=_as_of(args), no_llm=args.no_llm)


def main() -> None:
    ap = argparse.ArgumentParser(prog="tradeos", description="Personal portfolio intelligence.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="add/update a holding (and fetch its data)")
    a.add_argument("symbol")
    a.add_argument("quantity", type=float)
    a.add_argument("avg_cost", type=float, nargs="?", default=None)
    a.add_argument("--no-fetch", action="store_true", help="don't fetch price data now")
    a.set_defaults(func=_cmd_add)

    r = sub.add_parser("remove", help="remove a holding")
    r.add_argument("symbol")
    r.set_defaults(func=_cmd_remove)

    sub.add_parser("holdings", help="list your portfolio").set_defaults(func=_cmd_holdings)
    sub.add_parser("ingest", help="refresh all price data").set_defaults(func=_cmd_ingest)
    sub.add_parser("check", help="DB row counts").set_defaults(func=_cmd_check)

    d = sub.add_parser("docs", help="manage RAG documents")
    dsub = d.add_subparsers(dest="docs_cmd", required=True)
    dadd = dsub.add_parser("add", help="parse + embed a PDF/txt for a symbol")
    dadd.add_argument("symbol")
    dadd.add_argument("path")
    dadd.add_argument("--period", help="quarter-end the doc covers, YYYY-MM-DD (enables freshness checks)")
    dadd.add_argument("--filing-date", help="date the doc was filed / first public, YYYY-MM-DD")
    dadd.add_argument("--url", help="source URL the doc was fetched from")
    dadd.set_defaults(func=_cmd_docs_add)
    dls = dsub.add_parser("list", help="list ingested documents")
    dls.add_argument("symbol", nargs="?")
    dls.set_defaults(func=_cmd_docs_list)
    dst = dsub.add_parser("status", help="coverage: which holdings are missing/stale on transcripts")
    dst.add_argument("symbol", nargs="?")
    dst.set_defaults(func=_cmd_docs_status)

    ak = sub.add_parser("ask", help="ask a question over a symbol's documents (RAG)")
    ak.add_argument("symbol")
    ak.add_argument("question")
    ak.set_defaults(func=_cmd_ask)

    ev = sub.add_parser("eval", help="back-test whether signals predict forward returns")
    ev.add_argument("--horizon", type=int, default=21, help="forward-return horizon in trading days")
    ev.add_argument("--step", type=int, default=5, help="subsample dates every N days (limit overlap)")
    ev.set_defaults(func=_cmd_eval)

    for name, fn, desc in (("risk", _cmd_risk, "portfolio risk read"),
                           ("analyze", _cmd_analyze, "multi-agent per-stock analysis")):
        p = sub.add_parser(name, help=desc)
        p.add_argument("--horizon", default="annual", help="d/w/m/q/y or N days")
        p.add_argument("--as-of", help="point-in-time date YYYY-MM-DD")
        p.add_argument("--no-llm", action="store_true", help="skip the Claude layer")
        p.set_defaults(func=fn)

    args = ap.parse_args()
    try:
        args.func(args)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"\n{e}")


if __name__ == "__main__":
    main()
