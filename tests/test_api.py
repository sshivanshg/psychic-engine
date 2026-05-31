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
