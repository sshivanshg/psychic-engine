"""Tests for the Ownership agent — institutional/insider read (pure helpers + a PIT integration test)."""

import datetime as dt

import pytest

from tradeos.ownership import _institutional_dial, compute_ownership, load_ownership


def test_institutional_dial_buckets():
    assert _institutional_dial(60) == "high"
    assert _institutional_dial(30) == "moderate"
    assert _institutional_dial(5) == "low"
    assert _institutional_dial(None) is None


def test_compute_ownership_pct_and_nodata():
    assert compute_ownership("X", row={}) is None
    assert compute_ownership("X", row={"held_pct_institutions": None, "held_pct_insiders": None}) is None
    out = compute_ownership("X", row={"held_pct_institutions": 0.63, "held_pct_insiders": 0.18,
                                      "n_institutions": 120})
    assert out["institutional_pct"] == 63.0 and out["insider_pct"] == 18.0
    assert out["dials"]["institutional"] == "high"
    assert "barred from the eval" in out["note"]                                 # eval-barred, stated


def test_load_ownership_is_point_in_time():
    """Invariant (Prime Directive #2): an ownership snapshot is INVISIBLE before it was taken, so a
    current snapshot never leaks into a historical replay's card. Locks the look-ahead fix at the
    query level (skips without a DB). Uses a sentinel symbol and cleans up after itself."""
    from tradeos.db import get_connection
    sym = "__TEST_OWN__.NS"
    snap = dt.datetime(2025, 1, 15, 12, 0, tzinfo=dt.timezone.utc)
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO ownership (symbol, held_pct_institutions, held_pct_insiders, "
                        "n_institutions, snapshot_at) VALUES (%s, %s, %s, %s, %s) "
                        "ON CONFLICT (symbol) DO UPDATE SET snapshot_at=EXCLUDED.snapshot_at, "
                        "held_pct_institutions=EXCLUDED.held_pct_institutions",
                        (sym, 0.55, 0.10, 90, snap))
            conn.commit()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"DB not available: {e}")
    try:
        before = snap.date() - dt.timedelta(days=1)
        after = snap.date() + dt.timedelta(days=1)
        assert sym not in load_ownership([sym], as_of=before)   # taken later ⇒ not yet visible
        assert sym in load_ownership([sym], as_of=after)        # past the snapshot ⇒ visible
        assert sym in load_ownership([sym])                     # live (as_of=None) ⇒ always eligible
    finally:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM ownership WHERE symbol=%s", (sym,))
            conn.commit()
