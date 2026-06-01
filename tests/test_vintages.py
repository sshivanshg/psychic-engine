"""Tests for the price-revision log (reproducibility, Fix #4).

Two layers:
  1. Pure-function — `_latest_vintage_by_cell` reconstructs the as-known-at view (no DB).
  2. Integration — a round-trip through `price_vintages` proves a back-test replay sees the value that
     was known at decision time, NOT a later vendor restatement. Skips without a DB; cleans up.
"""

import datetime as dt

import pandas as pd
import pytest

from tradeos.ingest import _changed_vintage_rows
from tradeos.risk import _latest_vintage_by_cell, load_panels_asof


class _FakeCursor:
    """Minimal cursor stub so the capture-diff logic is testable without a DB."""

    def __init__(self, old_rows):
        self._old = old_rows

    def execute(self, sql, params=None):  # noqa: D401, ARG002
        return None

    def fetchall(self):
        return self._old


def test_changed_vintage_rows_logs_new_and_restated_only():
    """The revision log captures a (symbol, date) on first sight and ONLY when the vendor restates it —
    an unchanged re-ingest writes nothing (so vintages stay a sparse restatement log, not a daily copy)."""
    d1, d2, d3 = dt.date(2025, 1, 1), dt.date(2025, 1, 2), dt.date(2025, 1, 3)
    old = [(d1, 100.0, 100.0, 10), (d2, 200.0, 200.0, 20)]      # already in `prices`
    df = pd.DataFrame(
        {"open": [1.0, 1.0, 1.0], "high": [1.0, 1.0, 1.0], "low": [1.0, 1.0, 1.0],
         "close": [100.0, 250.0, 300.0],       # d1 unchanged · d2 restated 200→250 · d3 brand-new
         "adj_close": [100.0, 250.0, 300.0],
         "volume": [10, 20, 30]},
        index=[d1, d2, d3],
    )
    out = _changed_vintage_rows(_FakeCursor(old), "AAA", df)
    logged = {r[1] for r in out}
    assert d1 not in logged                     # unchanged ⇒ not logged (no float-noise spurious rows)
    assert logged == {d2, d3}                   # restated + new only
    assert all(r[0] == "AAA" for r in out)


def test_latest_vintage_by_cell_picks_known_at_target():
    # Two vintages of the SAME (AAA, 2025-01-10) cell: an original, then a later restatement.
    rows = [
        ("AAA", dt.date(2025, 1, 10), dt.date(2025, 1, 11), 100.0, 100.0, 10),   # original
        ("AAA", dt.date(2025, 1, 10), dt.date(2025, 3, 1), 50.0, 50.0, 20),      # restated after a split
        ("BBB", dt.date(2025, 1, 10), dt.date(2025, 1, 11), 200.0, 200.0, 5),
    ]
    out = {(s, d): (c, a, v) for s, d, _vd, c, a, v in _latest_vintage_by_cell(rows)}
    assert out[("AAA", dt.date(2025, 1, 10))] == (50.0, 50.0, 20)   # latest vintage wins
    assert out[("BBB", dt.date(2025, 1, 10))] == (200.0, 200.0, 5)
    assert len(out) == 2                                            # one row per (symbol, date)


def test_latest_vintage_by_cell_empty():
    assert _latest_vintage_by_cell([]) == []


def test_load_panels_asof_reproduces_decision_time_values():
    """Reproducibility invariant: reading 'as known at 2025-02-01' returns the ORIGINAL price, even
    though a later (2025-03-01) vintage restated it — so a replayed back-test isn't silently re-adjusted.
    """
    from tradeos.db import get_connection
    sym = "__TEST_VINTAGE__.NS"
    day = dt.date(2025, 1, 10)
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.price_vintages')")   # tolerate a pre-table DB
            if cur.fetchone()[0] is None:
                pytest.skip("price_vintages table not present (apply db/init/01_init.sql)")
            cur.executemany(
                "INSERT INTO price_vintages (symbol, date, vintage_date, close, adj_close, volume) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                [(sym, day, dt.date(2025, 1, 11), 100.0, 100.0, 10),    # observed at ingest
                 (sym, day, dt.date(2025, 3, 1), 50.0, 50.0, 20)],      # restated later (e.g. 1:2 split)
            )
            conn.commit()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"DB not available: {e}")
    try:

        # As known on 2025-02-01: only the original vintage existed ⇒ close = 100.
        close, adj, vol = load_panels_asof([sym], as_of=dt.date(2025, 2, 1),
                                           vintage_asof=dt.date(2025, 2, 1))
        assert float(close.loc[day, sym]) == pytest.approx(100.0)
        assert int(vol.loc[day, sym]) == 10

        # As known today (vintage_asof past the restatement) ⇒ the restated close = 50.
        close2, _adj2, _vol2 = load_panels_asof([sym], as_of=None, vintage_asof=dt.date(2025, 6, 1))
        assert float(close2.loc[day, sym]) == pytest.approx(50.0)

        # Before the first ingest, nothing was known ⇒ honest empty, never a fabricated number.
        c3, _a3, _v3 = load_panels_asof([sym], as_of=None, vintage_asof=dt.date(2025, 1, 1))
        assert c3.empty
    finally:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM price_vintages WHERE symbol=%s", (sym,))
            conn.commit()
