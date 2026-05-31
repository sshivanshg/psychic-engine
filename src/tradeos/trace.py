"""LLM observability — record every Claude call's tokens, latency and (best-effort) cost.

Build-your-own, on purpose (roadmap rule #5: understand the thing before adopting Langfuse). A
`RunTrace` collects one `CallRecord` per LLM call; `print_summary()` emits a per-run cost/latency
line. Tokens and latency are EXACT (from the API `usage` block); cost is derived from a configurable
list-price table and flagged *approximate* — list prices drift, so confirm against current pricing.
A Langfuse / LangSmith exporter is a drop-in later: iterate `trace.records` and POST them.
"""

import time
from dataclasses import dataclass
from threading import Lock

from .log import get_logger

log = get_logger()

# USD per MILLION tokens (input, output). APPROXIMATE list prices — confirm against current Anthropic
# pricing; they drift. Matched by model-name prefix; unknown models → cost None (tokens/latency still
# captured). Override via code if you track exact contracted rates.
PRICES = {
    "claude-opus-4": (15.0, 75.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-haiku-4": (0.80, 4.0),
}


def _rate(model: str):
    for prefix, rate in PRICES.items():
        if model.startswith(prefix):
            return rate
    return None


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Best-effort cost from the list-price table. None when the model isn't priced here."""
    r = _rate(model)
    if r is None:
        return None
    return input_tokens / 1e6 * r[0] + output_tokens / 1e6 * r[1]


@dataclass
class CallRecord:
    label: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    cost_usd: float | None


class RunTrace:
    """Thread-safe collector (the orchestrator records from a ThreadPoolExecutor)."""

    def __init__(self) -> None:
        self.records: list[CallRecord] = []
        self._lock = Lock()

    def record(self, label: str, model: str, usage, latency_ms: float) -> None:
        it = int(getattr(usage, "input_tokens", 0) or 0)
        ot = int(getattr(usage, "output_tokens", 0) or 0)
        rec = CallRecord(label, model, it, ot, round(latency_ms, 1), cost_usd(model, it, ot))
        with self._lock:
            self.records.append(rec)

    def summary(self) -> dict:
        recs = self.records
        costs = [r.cost_usd for r in recs if r.cost_usd is not None]
        return {
            "calls": len(recs),
            "input_tokens": sum(r.input_tokens for r in recs),
            "output_tokens": sum(r.output_tokens for r in recs),
            "total_latency_ms": round(sum(r.latency_ms for r in recs), 1),
            "est_cost_usd": round(sum(costs), 4) if costs else None,
            "cost_complete": len(costs) == len(recs) and bool(recs),  # all calls were priced
        }

    def print_summary(self) -> None:
        if not self.records:
            return
        s = self.summary()
        cost = (("~$%.4f" % s["est_cost_usd"]) + ("" if s["cost_complete"] else " (partial)")) \
            if s["est_cost_usd"] is not None else "n/a"
        print(f"  · LLM: {s['calls']} call(s) · {s['input_tokens']:,} in / {s['output_tokens']:,} out "
              f"tokens · {s['total_latency_ms']:.0f} ms · est cost {cost}")


def timed_call(trace: "RunTrace | None", label: str, model: str, thunk):
    """Run an LLM call thunk, recording (label, model, usage, latency) into `trace` if present."""
    t0 = time.perf_counter()
    msg = thunk()
    if trace is not None:
        trace.record(label, model, getattr(msg, "usage", None), (time.perf_counter() - t0) * 1000)
    return msg
