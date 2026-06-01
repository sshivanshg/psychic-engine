"""Tests for the read-layer TTL cache (pure — no DB/network).

The cache must (a) serve repeat reads without re-running the producer, (b) hand back an INDEPENDENT
deep copy so a caller mutating the result (annotate_deltas) can't corrupt the cache, and (c) be
fully disable-able via READ_CACHE_TTL=0.
"""

from tradeos import cache


def test_memo_caches_and_isolates(monkeypatch):
    monkeypatch.setattr(cache, "_TTL", 300)
    cache.clear()
    calls = {"n": 0}

    def producer():
        calls["n"] += 1
        return {"cards": [{"symbol": "X"}]}

    a = cache.memo(("k",), producer)
    a["cards"][0]["MUT"] = True            # mutate the returned copy
    b = cache.memo(("k",), producer)
    assert calls["n"] == 1                 # second call served from cache (producer not re-run)
    assert "MUT" not in b["cards"][0]      # cache handed back an independent deep copy


def test_memo_ttl_zero_disables(monkeypatch):
    monkeypatch.setattr(cache, "_TTL", 0)
    cache.clear()
    calls = {"n": 0}

    def producer():
        calls["n"] += 1
        return calls["n"]

    assert cache.memo(("k",), producer) == 1
    assert cache.memo(("k",), producer) == 2   # TTL<=0 ⇒ no caching, producer runs every call


def test_clear_forces_recompute(monkeypatch):
    monkeypatch.setattr(cache, "_TTL", 300)
    cache.clear()
    calls = {"n": 0}

    def producer():
        calls["n"] += 1
        return calls["n"]

    assert cache.memo(("k",), producer) == 1
    cache.clear()
    assert cache.memo(("k",), producer) == 2   # cleared ⇒ recompute
