"""Tests for LLM observability (cost table + run trace) — no network."""

from types import SimpleNamespace

from tradeos.trace import RunTrace, cost_usd


def test_cost_table():
    assert cost_usd("claude-opus-4-8", 1_000_000, 1_000_000) == 15.0 + 75.0   # Opus list price
    assert cost_usd("claude-haiku-4-5", 1_000_000, 0) == 0.80
    assert cost_usd("some-unpriced-model", 1000, 1000) is None                # unknown ⇒ no estimate


def test_run_trace_aggregates():
    t = RunTrace()
    t.record("a", "claude-opus-4-8", SimpleNamespace(input_tokens=1000, output_tokens=500), 120.0)
    t.record("b", "claude-opus-4-8", SimpleNamespace(input_tokens=2000, output_tokens=1000), 80.0)
    s = t.summary()
    assert s["calls"] == 2
    assert s["input_tokens"] == 3000 and s["output_tokens"] == 1500
    assert s["total_latency_ms"] == 200.0
    assert s["cost_complete"] is True and s["est_cost_usd"] > 0


def test_unpriced_model_marks_cost_incomplete():
    t = RunTrace()
    t.record("x", "mystery-model", SimpleNamespace(input_tokens=1000, output_tokens=1000), 10.0)
    s = t.summary()
    assert s["est_cost_usd"] is None and s["cost_complete"] is False


def test_record_handles_missing_usage():
    t = RunTrace()
    t.record("n", "claude-opus-4-8", None, 5.0)        # API response without a usage block
    assert t.summary()["calls"] == 1 and t.summary()["input_tokens"] == 0
