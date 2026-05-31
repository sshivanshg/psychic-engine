"""Tests for portfolio management (add / remove / persist) — no DB, no network."""

from tradeos import config


def test_add_remove_roundtrip(tmp_path, monkeypatch):
    f = tmp_path / "holdings.csv"
    monkeypatch.setattr(config, "PORTFOLIO_FILE", f)

    config.add_holding("infy.ns", 20, 1300)   # lower-case in → normalised to upper
    config.add_holding("TCS.NS", 5)             # avg_cost optional
    ps = {p.symbol: p for p in config.load_portfolio()}
    assert set(ps) == {"INFY.NS", "TCS.NS"}
    assert ps["INFY.NS"].quantity == 20 and ps["INFY.NS"].avg_cost == 1300
    assert ps["TCS.NS"].avg_cost is None

    config.add_holding("INFY.NS", 30)           # re-add replaces, doesn't duplicate
    ps = {p.symbol: p for p in config.load_portfolio()}
    assert ps["INFY.NS"].quantity == 30 and len([p for p in config.load_portfolio() if p.symbol == "INFY.NS"]) == 1

    config.remove_holding("TCS.NS")
    assert {p.symbol for p in config.load_portfolio()} == {"INFY.NS"}


def test_empty_portfolio_is_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PORTFOLIO_FILE", tmp_path / "empty.csv")
    assert config._safe_load() == []     # missing file → [] not an exception
