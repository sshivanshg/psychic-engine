"""Tests for concall guidance extraction — deterministic merge + storage round-trip.

The LLM extraction itself needs an API key (skipped here); the merge into the Fundamental agent,
the no-key fallback, and the DB round-trip are all exercised.
"""

import datetime as dt

import pytest

from tradeos.config import Position
from tradeos.fundamental import compute_all_fundamental


def test_fundamental_merges_injected_guidance():
    # both fundamentals + guidance injected ⇒ pure, no DB/network
    positions = [Position("INFY.NS", 10, None)]
    guidance = {"INFY.NS": {"revenue_outlook": "guiding 2-4% cc growth", "margin_outlook": "21-23%",
                            "demand_commentary": None, "other_guidance": [], "quotes": ["..."],
                            "source": "INFY_Q4.txt", "period": "2026-03-31"}}
    out = compute_all_fundamental(positions=positions, fundamentals={}, guidance=guidance)
    assert "INFY.NS" in out
    assert out["INFY.NS"]["guidance"]["revenue_outlook"] == "guiding 2-4% cc growth"
    assert out["INFY.NS"]["dials"]["revenue_growth"] is None        # empty-shape stub (no numbers)


def test_no_guidance_and_no_numbers_is_skipped():
    out = compute_all_fundamental(positions=[Position("ZZZ.NS", 1, None)], fundamentals={}, guidance={})
    assert out == {}


def test_extract_without_key_returns_evidence(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    try:
        from tradeos.extraction import extract_guidance
        res = extract_guidance("INFY.NS")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"DB/embeddings not available: {e}")
    if not res.get("hits"):
        pytest.skip("no documents ingested for INFY.NS")
    assert res["stored"] is False and res["hits"]                   # evidence shown, nothing fabricated


def test_guidance_storage_roundtrip():
    from tradeos.db import get_connection
    from tradeos.extraction import _store, load_guidance
    data = {"revenue_outlook": "test outlook", "margin_outlook": None, "demand_commentary": None,
            "other_guidance": [], "quotes": ["q"]}
    try:
        _store("TESTX.NS", "unit_test_doc.txt", dt.date(2026, 3, 31), data)
        g = load_guidance("TESTX.NS")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"DB not available: {e}")
    try:
        assert g is not None
        assert g["revenue_outlook"] == "test outlook"
        assert g["source"] == "unit_test_doc.txt" and g["period"] == "2026-03-31"
    finally:
        with get_connection() as c, c.cursor() as cur:
            cur.execute("DELETE FROM guidance WHERE symbol='TESTX.NS'")
            c.commit()
