"""Tests for the survivorship-free back-test universe (holdings ∪ sold/delisted names)."""

import tradeos.config as config


def test_load_universe_unions_and_dedups(tmp_path, monkeypatch):
    uni = tmp_path / "universe.csv"
    uni.write_text("# sold / delisted names\nYESBANK.NS\npnb.ns\nINFY.NS\n")   # INFY is also a holding
    monkeypatch.setattr(config, "UNIVERSE_FILE", uni)
    u = config.load_universe()
    assert "YESBANK.NS" in u and "PNB.NS" in u          # extra names included (and upper-cased)
    assert u.count("INFY.NS") == 1                       # deduped against the live holding
    for s in config.load_holdings():                     # holdings are never dropped
        assert s in u


def test_load_universe_degrades_to_holdings_without_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "UNIVERSE_FILE", tmp_path / "absent.csv")
    assert set(config.load_universe()) == set(config.load_holdings())
