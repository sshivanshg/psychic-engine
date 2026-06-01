"""Tiny TTL memo for the FastAPI READ layer ONLY — the quant engine stays pure and uncached.

Prices change once a day (after the close), yet a dashboard re-runs the full multi-agent analysis on
every request/refresh. This caches the FACTUAL (narrate=False, snapshot=False) engine reads for a
short TTL and hands back a DEEP COPY, so a caller that mutates the result — e.g. `annotate_deltas`
adding a `delta` key — can never corrupt the cached object. LLM narration is never cached here: it's
requested per symbol and run live (see `api.stock`).

Determinism note: this lives in the read layer, not the quant core, so a wall clock is fine here
(the core forbids it). Set `READ_CACHE_TTL=0` to disable caching entirely (every call recomputes).
"""

import copy
import os
import time
from collections.abc import Callable
from typing import Any

_TTL = float(os.getenv("READ_CACHE_TTL", "300"))   # seconds; 0 disables the cache
_store: dict[tuple, tuple[float, Any]] = {}


def memo(key: tuple, producer: Callable[[], Any]) -> Any:
    """Return a deep copy of `producer()`, cached under `key` for TTL seconds. The copy keeps the
    cache immutable to callers; recompute on miss/expiry."""
    if _TTL <= 0:
        return producer()
    now = time.monotonic()
    hit = _store.get(key)
    if hit is not None and now - hit[0] < _TTL:
        return copy.deepcopy(hit[1])
    value = producer()
    _store[key] = (now, value)
    return copy.deepcopy(value)


def clear() -> None:
    """Drop everything (e.g. after an ingest, so the next read reflects fresh prices)."""
    _store.clear()
