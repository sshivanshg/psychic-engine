"""Tests for the agent framework + shared context."""

import pytest

from tradeos.agents import REGISTRY
from tradeos.context import AnalysisContext


def test_registry_shape():
    names = [a.name for a in REGISTRY]
    assert names == ["risk", "technical", "fundamental", "macro", "sentiment", "ownership"]
    assert all(a.scope in ("portfolio", "per_stock") for a in REGISTRY)


def test_agents_run_via_shared_context():
    try:
        ctx = AnalysisContext.build()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"DB not available: {e}")
    out = {a.name: a.run(ctx) for a in REGISTRY}
    assert "positions" in out["risk"] and "portfolio" in out["risk"]
    assert isinstance(out["technical"], dict)
    assert isinstance(out["fundamental"], dict)
    assert "by_symbol" in out["macro"] and "portfolio" in out["macro"]


def test_injected_panels_match_self_loaded():
    """The refactor must not change numbers: injecting the context's panels == loading internally."""
    from tradeos.risk import compute_risk
    try:
        ctx = AnalysisContext.build()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"DB not available: {e}")
    injected = compute_risk(panels=ctx.panels, positions=ctx.positions)
    loaded = compute_risk()
    assert injected["portfolio"]["vol_pct"] == loaded["portfolio"]["vol_pct"]
    assert injected["portfolio"]["beta"] == loaded["portfolio"]["beta"]
    assert injected["portfolio"]["top_risk_pct"] == loaded["portfolio"]["top_risk_pct"]
