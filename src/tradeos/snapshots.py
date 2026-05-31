"""Run-over-run "what-changed" delta.

Each `analyze` persists a COMPACT per-stock snapshot (the dials, the attention score, the risk
contribution, the sentiment label) to `run_snapshots`. On the next run we diff the fresh card against
the most recent prior snapshot and surface what moved — a dial that flipped, a score that jumped, a
name that became a bigger risk contributor. The diff (`compute_delta`) is pure and unit-tested; only
storage/retrieval touch the DB.

This is the roadmap's "tells you *what changed*" — the thing that turns a static read into something
you glance at each morning to see only the deltas, not re-read the whole book.
"""

from .db import get_connection
from .log import get_logger

log = get_logger()

# Categorical dials we watch for a flip (old != new is a change worth surfacing).
DIAL_FIELDS = ("trend", "momentum", "level", "revenue_growth", "earnings_growth",
               "margin_trend", "sentiment", "institutional")


def snapshot_card(card: dict) -> dict:
    """The minimal, comparable fingerprint of a card (what we store + diff)."""
    t = (card.get("technical") or {}).get("dials", {})
    f = (card.get("fundamental") or {}).get("dials", {})
    o = (card.get("ownership") or {}).get("dials", {})
    return {
        "attention": (card.get("attention") or {}).get("score"),
        "trend": t.get("trend"),
        "momentum": t.get("momentum"),
        "level": t.get("level"),
        "revenue_growth": f.get("revenue_growth"),
        "earnings_growth": f.get("earnings_growth"),
        "margin_trend": f.get("margin_trend"),
        "sentiment": (card.get("sentiment") or {}).get("label"),
        "institutional": o.get("institutional"),
        "risk_contribution_pct": (card.get("risk") or {}).get("risk_contribution_pct"),
    }


def compute_delta(prev: dict | None, curr: dict, *, score_eps: float = 5.0,
                  risk_eps: float = 3.0) -> list[str]:
    """Human-readable changes from `prev` to `curr` snapshot. Pure; [] on a first run (no prior).

    Categorical dials are reported on any flip; numeric moves (attention, risk %) only when they
    clear a threshold, so day-to-day noise doesn't drown the signal.
    """
    if prev is None:
        return []
    changes: list[str] = []
    for k in DIAL_FIELDS:
        a, b = prev.get(k), curr.get(k)
        if a != b and a is not None and b is not None:
            changes.append(f"{k} {a} → {b}")
    pa, pb = prev.get("attention"), curr.get("attention")
    if pa is not None and pb is not None and abs(pb - pa) >= score_eps:
        changes.append(f"attention {pa:.0f} → {pb:.0f}")
    ra, rb = prev.get("risk_contribution_pct"), curr.get("risk_contribution_pct")
    if ra is not None and rb is not None and abs(rb - ra) >= risk_eps:
        changes.append(f"risk contribution {ra:.0f}% → {rb:.0f}%")
    return changes


def load_prior(symbol: str) -> tuple[dict, str] | None:
    """The most recent stored snapshot for a symbol (the prior run), as (payload, run_at-string)."""
    with get_connection() as c, c.cursor() as cur:
        cur.execute("SELECT payload, run_at FROM run_snapshots WHERE symbol=%s "
                    "ORDER BY run_at DESC LIMIT 1", (symbol,))
        row = cur.fetchone()
    return (row[0], str(row[1])) if row else None


def save_run(cards: list[dict], as_of) -> None:
    """Append a snapshot row per card for this run. Best-effort: a write failure never sinks analyze."""
    from psycopg.types.json import Json
    as_of_date = str(as_of)[:10] if as_of else None
    rows = [(c["symbol"], as_of_date, Json(snapshot_card(c))) for c in cards]
    if not rows:
        return
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.executemany("INSERT INTO run_snapshots (symbol, as_of, payload) VALUES (%s, %s, %s)", rows)
            conn.commit()
    except Exception as e:  # noqa: BLE001 - observability is not worth crashing the analysis for
        log.warning("run-snapshot save failed: %s", e)


def annotate_deltas(cards: list[dict]) -> None:
    """Attach `card['delta']` = {since, changes} by diffing each card against its prior snapshot.
    Call BEFORE save_run so the prior is genuinely the previous run, not this one."""
    for c in cards:
        prior = load_prior(c["symbol"])
        if prior is None:
            c["delta"] = {"since": None, "changes": []}
        else:
            prev_payload, run_at = prior
            c["delta"] = {"since": run_at, "changes": compute_delta(prev_payload, snapshot_card(c))}
