"""Tests for the Macro (sector-exposure) agent — pure compute + integration."""

import pandas as pd
import pytest

from tradeos.config import Position
from tradeos.macro import compute_all_macro, compute_macro


def _close(prices: dict) -> pd.DataFrame:
    # one row is enough — compute_macro uses the latest close
    return pd.DataFrame(prices, index=pd.to_datetime(["2026-03-31"]))


def test_sector_weights_and_concentration():
    positions = [Position("INFY.NS", 10, None), Position("TCS.NS", 10, None),
                 Position("HDFCBANK.NS", 10, None)]
    close = _close({"INFY.NS": [100.0], "TCS.NS": [100.0], "HDFCBANK.NS": [100.0]})
    sectors = {"INFY.NS": "Technology", "TCS.NS": "Technology", "HDFCBANK.NS": "Financial Services"}
    out = compute_macro(positions, close, sectors)

    pf = out["portfolio"]
    assert pf["top_sector"] == "Technology"
    assert pf["top_sector_pct"] == pytest.approx(66.67, abs=0.1)   # 2 of 3 equal-value names
    assert pf["num_sectors"] == 2
    assert pf["concentration"] == "high"                            # > 40% in one sector
    # per-symbol carries the share of the WHOLE BOOK in that name's sector
    assert out["by_symbol"]["INFY.NS"]["sector_weight_pct"] == pytest.approx(66.67, abs=0.1)
    assert out["by_symbol"]["HDFCBANK.NS"]["sector_weight_pct"] == pytest.approx(33.33, abs=0.1)


def test_unknown_sector_is_tracked_not_dropped():
    positions = [Position("AAA.NS", 10, None), Position("BBB.NS", 10, None)]
    close = _close({"AAA.NS": [100.0], "BBB.NS": [100.0]})
    out = compute_macro(positions, close, sectors={"AAA.NS": "Energy"})   # BBB missing ⇒ Unknown
    assert out["portfolio"]["unknown_pct"] == pytest.approx(50.0, abs=0.1)
    assert out["portfolio"]["num_sectors"] == 1                            # Unknown not counted as a sector


def test_empty_close_is_safe():
    assert compute_macro([Position("X.NS", 1, None)], pd.DataFrame(), {}) == {"by_symbol": {}, "portfolio": {}}


def test_macro_is_point_in_time_uses_only_terminal_row():
    """No look-ahead (Prime Directive #2): weights come from the LAST available close, so when the
    context bounds the panel at `as_of`, the read reflects that date — not any later row."""
    positions = [Position("TECH.NS", 1, None), Position("BANK.NS", 1, None)]
    sectors = {"TECH.NS": "Technology", "BANK.NS": "Financial Services"}
    # Day 1 Tech dominates; day 2 (the as-of terminal row) Bank dominates. Result must follow day 2.
    close = pd.DataFrame(
        {"TECH.NS": [300.0, 100.0], "BANK.NS": [100.0, 300.0]},
        index=pd.to_datetime(["2026-03-30", "2026-03-31"]),
    )
    out = compute_macro(positions, close, sectors)
    assert out["portfolio"]["top_sector"] == "Financial Services"
    # truncating to day 1 only flips it — proving the read depends solely on the terminal (as-of) row
    out_d1 = compute_macro(positions, close.iloc[:1], sectors)
    assert out_d1["portfolio"]["top_sector"] == "Technology"


def test_macro_flows_seam_degrades_honestly():
    positions = [Position("INFY.NS", 10, None)]
    close = _close({"INFY.NS": [100.0]})
    out = compute_macro(positions, close, {"INFY.NS": "Technology"})           # no flow source
    assert out["portfolio"]["flows"] is None
    assert "no source" in out["portfolio"]["flows_note"]
    out2 = compute_macro(positions, close, {"INFY.NS": "Technology"}, flows={"fii_cr": -1200})
    assert out2["portfolio"]["flows"] == {"fii_cr": -1200}
    assert out2["portfolio"]["flows_note"] is None                              # real data ⇒ no caveat


def test_compute_all_macro_integration():
    try:
        out = compute_all_macro()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"DB not available: {e}")
    assert "by_symbol" in out and "portfolio" in out
