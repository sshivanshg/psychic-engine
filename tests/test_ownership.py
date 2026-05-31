"""Tests for the Ownership agent — institutional/insider read (pure, no DB)."""

from tradeos.ownership import _institutional_dial, compute_ownership


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
