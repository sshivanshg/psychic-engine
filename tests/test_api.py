"""Tests for the FastAPI layer. DB-free routes are asserted exactly; engine-backed routes accept a
503 (no data / DB down) so the suite stays green without a populated database."""

import pytest

pytest.importorskip("httpx")          # starlette's TestClient needs httpx
from fastapi.testclient import TestClient  # noqa: E402

from tradeos.api import app  # noqa: E402

client = TestClient(app)


def test_health_ok():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and "holdings" in body and "llm" in body


def test_holdings_is_a_list():
    r = client.get("/api/holdings")
    assert r.status_code == 200 and isinstance(r.json(), list)


def test_bad_as_of_is_400():
    r = client.get("/api/portfolio?as_of=not-a-date")
    assert r.status_code == 400


def test_portfolio_endpoint_serves_or_503s():
    r = client.get("/api/portfolio?horizon=annual")
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        body = r.json()
        assert "cards" in body and "risk_overview" in body and "narratives" in body
        # narratives must be JSON (StockCard dumped), never a Pydantic object
        assert all(isinstance(v, dict) for v in body["narratives"].values())


def test_unknown_stock_404s_when_data_present():
    r = client.get("/api/stock/NOTREAL.NS")
    assert r.status_code in (404, 503)        # 404 if analysis ran, 503 if no data at all


def test_stock_series_shape_or_404():
    r = client.get("/api/stock/INFY.NS/series?lookback=120")
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        s = r.json()
        assert {"symbol", "dates", "close", "sma20", "sma50", "sma200", "volume"} <= set(s)
        assert len(s["dates"]) == len(s["close"]) == len(s["sma200"])      # aligned series
        assert s["symbol"] == "INFY.NS"


def test_stock_series_rejects_bad_lookback():
    assert client.get("/api/stock/INFY.NS/series?lookback=5").status_code == 422   # below ge=30


# --------------------------- evidence layer (Analyst Workbench / Newsroom / Coverage) ---------------------------

def test_sanitize_coerces_numpy_dates_and_models():
    """The evidence endpoints lean on _sanitize to JSON-ify np scalars, dates and Pydantic models."""
    import datetime as dt
    import json

    import numpy as np
    from pydantic import BaseModel

    from tradeos.api import _sanitize

    class M(BaseModel):
        a: int

    out = _sanitize({"f": np.float64(1.5), "i": np.int64(3), "d": dt.date(2026, 1, 2),
                     "m": M(a=7), "xs": [np.float32(2.0), None, "x"]})
    assert out == {"f": 1.5, "i": 3, "d": "2026-01-02", "m": {"a": 7}, "xs": [2.0, None, "x"]}
    json.dumps(out)                                          # must be JSON-serialisable end to end


def test_analyst_endpoint_shape_or_404():
    r = client.get("/api/analyst/INFY.NS?verdict=false")
    assert r.status_code in (200, 404, 503)
    if r.status_code == 200:
        body = r.json()
        assert {"symbol", "as_of", "facts", "verdict", "usage"} <= set(body)
        assert body["verdict"] is None                       # verdict=false ⇒ no LLM call
        assert isinstance(body["facts"], dict) and body["facts"]["symbol"] == "INFY.NS"


def test_analyst_unknown_symbol_404():
    r = client.get("/api/analyst/NOTREAL.NS?verdict=false")
    assert r.status_code in (404, 503)                       # SystemExit-no-data mapped to a clean 404


def test_analyst_deep_shape_or_404():
    """The deep read endpoint returns the multi-agent shape (deep=None without a key) or degrades."""
    r = client.get("/api/analyst/INFY.NS/deep")
    assert r.status_code in (200, 404, 503)
    if r.status_code == 200:
        body = r.json()
        assert {"symbol", "as_of", "facts", "deep", "debate", "usage", "cost_usd", "model"} <= set(body)
        assert body["symbol"] == "INFY.NS"


def test_analyst_ask_shape_or_503():
    """Ask-the-analyst is distinct from /api/ask; web=False keeps it offline. No key ⇒ excerpts + note."""
    r = client.post("/api/analyst/ask",
                    json={"symbol": "INFY.NS", "question": "how are margins trending?", "web": False})
    assert r.status_code in (200, 404, 503)
    if r.status_code == 200:
        body = r.json()
        assert {"answer", "citations", "hits", "web_used", "web_sources", "note"} <= set(body)
        assert body["web_used"] is False                     # web disabled in the request


def test_stock_news_shape_or_503():
    r = client.get("/api/stock/INFY.NS/news?limit=5")
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        body = r.json()
        assert body["symbol"] == "INFY.NS" and isinstance(body["headlines"], list)
        for h in body["headlines"]:
            assert {"title", "publisher", "published", "polarity", "event"} <= set(h)


def test_stock_news_rejects_bad_limit():
    assert client.get("/api/stock/INFY.NS/news?limit=0").status_code == 422   # below ge=1


def test_stock_docs_shape_or_503():
    r = client.get("/api/stock/INFY.NS/docs")
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        assert r.json()["symbol"] == "INFY.NS" and isinstance(r.json()["documents"], list)


def test_news_feed_shape_or_503():
    r = client.get("/api/news?limit=10")
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        body = r.json()
        assert "headlines" in body and isinstance(body["headlines"], list)
        assert len(body["headlines"]) <= 10                  # limit honoured


def test_coverage_shape_or_503():
    r = client.get("/api/coverage")
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        rows = r.json()["rows"]
        assert isinstance(rows, list)
        for row in rows:
            assert {"symbol", "price_rows", "news", "doc_chunks"} <= set(row)


# --------------------------- write seam (mutations) ---------------------------
# These call the same config/docs functions the CLI uses. Holdings tests point PORTFOLIO_FILE at a
# temp file so the real holdings.csv is never touched, and use fetch=False so there's no network.

def test_add_and_remove_holding_roundtrip(monkeypatch, tmp_path):
    from tradeos import config
    monkeypatch.setattr(config, "PORTFOLIO_FILE", tmp_path / "holdings.csv")

    r = client.post("/api/holdings",
                    json={"symbol": "test.ns", "quantity": 5, "avg_cost": 100, "fetch": False})
    assert r.status_code == 200
    body = r.json()
    assert body["fetched"] is False and body["warning"] is None
    assert {"symbol": "TEST.NS", "quantity": 5.0, "avg_cost": 100.0} in body["holdings"]
    assert "TEST.NS" in (tmp_path / "holdings.csv").read_text()      # persisted (upper-cased)

    r2 = client.delete("/api/holdings/TEST.NS")
    assert r2.status_code == 200
    assert all(h["symbol"] != "TEST.NS" for h in r2.json()["holdings"])


def test_add_holding_requires_symbol():
    r = client.post("/api/holdings", json={"symbol": "  ", "quantity": 1, "fetch": False})
    assert r.status_code == 400


def test_docs_upload_rejects_unsupported_type():
    r = client.post("/api/docs", data={"symbol": "INFY.NS"},
                    files={"file": ("x.exe", b"data", "application/octet-stream")})
    assert r.status_code == 400


def test_docs_upload_requires_symbol():
    r = client.post("/api/docs", data={"symbol": "  "},
                    files={"file": ("x.txt", b"hello", "text/plain")})
    assert r.status_code == 400


def test_docs_upload_rejects_empty_file():
    r = client.post("/api/docs", data={"symbol": "INFY.NS"},
                    files={"file": ("x.txt", b"", "text/plain")})
    assert r.status_code == 400


def test_docs_upload_rejects_bad_period():
    r = client.post("/api/docs", data={"symbol": "INFY.NS", "period": "not-a-date"},
                    files={"file": ("x.txt", b"hello", "text/plain")})
    assert r.status_code == 400


# --------------------------- live stream (SSE Reasoning Monitor) ---------------------------

def test_stream_analyze_is_event_stream(monkeypatch):
    monkeypatch.setenv("STREAM_PACING_MS", "0")          # don't pace the test
    with client.stream("GET", "/api/stream/analyze?narrate=false") as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.headers.get("content-type", "")
        body = "".join(r.iter_text())
    # run_start is emitted before any DB touch ⇒ present with or without a populated database,
    # and the stream always terminates with an end event (clean close, even on a 'no data' run).
    assert '"type": "run_start"' in body
    assert "event: end" in body
