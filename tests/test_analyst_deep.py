"""Tests for the deep multi-agent analysis + ask-the-analyst (analyst.py).

The LLM is never hit: pure helpers are asserted exactly, the orchestration is exercised with a mocked
`_parse_call`, and the degradation/look-ahead paths are checked without a key. DB-touching paths are
patched out so the suite stays green without a populated database.
"""

import datetime as dt

from tradeos import analyst
from tradeos.analyst import (
    BEAR_SYSTEM,
    BULL_SYSTEM,
    JUDGE_SYSTEM,
    SECTOR_SYSTEM,
    CasePoint,
    DeepAnalysis,
    Scenario,
    SectorRead,
    SideCase,
    _ASK_RESEARCH_SYSTEM,
    _agg_usage,
    _case_text,
    _deep_cost,
    _sector_text,
    _web_sources,
)


# ----------------------------- bright lines (the Prime Directives in prose) -----------------------------

def test_every_deep_prompt_is_descriptive_only():
    """No agent may advise. Each system prompt must forbid buy/sell/hold AND price targets."""
    for p in (BULL_SYSTEM, BEAR_SYSTEM, SECTOR_SYSTEM, JUDGE_SYSTEM, _ASK_RESEARCH_SYSTEM):
        low = p.lower()
        assert "never say buy/sell/hold" in low
        assert "price target" in low                       # explicitly forbidden
        assert "never invent a number" in low              # honest-gaps directive


# ----------------------------- pure helpers -----------------------------

def test_agg_usage_sums_and_skips_none():
    class U:
        def __init__(self, i, o):
            self.input_tokens, self.output_tokens = i, o
    assert _agg_usage([U(10, 20), U(5, 5), None]) == {"input_tokens": 15, "output_tokens": 25}


def test_deep_cost_uses_sonnet_pricing_and_search_fee():
    # 1M in × $3 + 1M out × $15 = $18
    assert _deep_cost({"usage": {"input_tokens": 1_000_000, "output_tokens": 1_000_000}}) == 18.0
    # a live-news fetch adds the ~$0.02 web-search fee (token usage here is zero)
    class U:
        input_tokens = output_tokens = 0
    assert _deep_cost({"usage": {"input_tokens": 0, "output_tokens": 0},
                       "news_status": {"fetched": True, "usage": U()}}) == 0.02


def test_case_and_sector_text_handle_missing():
    assert _case_text(None) == "(no data)"
    assert _sector_text(None) == "(no data)"
    t = _case_text(SideCase(summary="sum", points=[CasePoint(point="p1", evidence="e1")]))
    assert "sum" in t and "p1" in t and "e1" in t
    s = _sector_text(SectorRead(sector="Tech", backdrop="b", company_fit="f", sensitivity="x"))
    assert "sector=Tech" in s and "sensitivity: x" in s


def test_web_sources_extracts_and_dedupes():
    r1 = type("R", (), {"url": "https://a.com", "title": "A"})()
    r2 = type("R", (), {"url": "https://a.com", "title": "A again"})()    # dup url
    r3 = type("R", (), {"url": "https://b.com", "title": None})()         # title falls back to url
    block = type("B", (), {"type": "web_search_tool_result", "content": [r1, r2, r3]})()
    text = type("T", (), {"type": "text", "text": "hi"})()
    resp = type("Resp", (), {"content": [text, block]})()
    assert _web_sources(resp) == [{"title": "A", "url": "https://a.com"},
                                  {"title": "https://b.com", "url": "https://b.com"}]


# ----------------------------- deep_analysis orchestration (mocked LLM) -----------------------------

def _fake_parse(model, system, user, output_format, max_tokens=900):
    """Stand in for one structured Claude call — returns a typed output + a fixed usage."""
    usage = type("U", (), {"input_tokens": 10, "output_tokens": 20})()
    if output_format is SideCase:
        return SideCase(summary="s", points=[CasePoint(point="p", evidence="e")]), usage
    if output_format is SectorRead:
        return SectorRead(sector="Tech", backdrop="b", company_fit="f", sensitivity="x"), usage
    if output_format is DeepAnalysis:
        return DeepAnalysis(headline="h", thesis="t", whats_right=["r"], whats_wrong=["w"],
                            sector_context="sc", quarter_read="q",
                            scenarios=[Scenario(label="L", drivers=["d"], implication="i")],
                            what_to_watch=["watch"], confidence="medium", bottom_line="bl"), usage
    raise AssertionError(f"unexpected output_format {output_format}")


def test_deep_analysis_happy_path(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(analyst, "assemble_facts", lambda *a, **k: {"symbol": "DEEPX.NS", "last_close": 100})
    monkeypatch.setattr(analyst, "_digest", lambda f: "DIGEST")
    import tradeos.credibility as cred
    monkeypatch.setattr(cred, "assess_credibility", lambda *a, **k: None)
    monkeypatch.setattr(analyst, "_parse_call", _fake_parse)

    out = analyst.deep_analysis("DEEPX.NS", live_news=False, save=False)
    assert out["deep"].headline == "h" and out["deep"].whats_right == ["r"]
    assert set(out["debate"]) == {"bull", "bear", "sector"}
    assert out["debate"]["bull"]["summary"] == "s"               # sub-agent dumped to a dict
    assert out["debate"]["sector"]["sector"] == "Tech"
    assert out["usage"] == {"input_tokens": 40, "output_tokens": 80}   # 4 calls × (10/20)
    assert out["cost_usd"] and out["cost_usd"] > 0
    assert out["model"] == analyst.DEEP_MODEL


def test_deep_analysis_degrades_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(analyst, "assemble_facts", lambda *a, **k: {"symbol": "DEEPY.NS"})
    import tradeos.credibility as cred
    monkeypatch.setattr(cred, "assess_credibility", lambda *a, **k: None)
    out = analyst.deep_analysis("DEEPY.NS", live_news=False, save=False)
    assert out["deep"] is None and out["debate"] is None          # facts only, no crash
    assert out["facts"]["symbol"] == "DEEPY.NS"


# ----------------------------- ask_research grounding + look-ahead gate -----------------------------

def test_ask_research_degrades_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(analyst, "assemble_facts", lambda *a, **k: {"symbol": "ASKA.NS"})
    import tradeos.docs as docs
    monkeypatch.setattr(docs, "search",
                        lambda *a, **k: [{"content": "c", "source": "s", "chunk": 0, "distance": 0.1}])
    res = analyst.ask_research("ASKA.NS", "what about margins?")
    assert res["answer"] is None and res["hits"]                  # retrieved excerpts still returned
    assert res["web_used"] is False and "ANTHROPIC_API_KEY" in res["note"]


def test_ask_research_web_is_barred_for_historical_as_of(monkeypatch):
    """Live web_search may fire for a live read (as_of=None) but NEVER for a past as_of (look-ahead)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(analyst, "assemble_facts", lambda *a, **k: {"symbol": "GATE.NS"})
    monkeypatch.setattr(analyst, "_digest", lambda f: "DIGEST")
    monkeypatch.setattr(analyst, "_latest_deep_summary", lambda s: None)
    import tradeos.docs as docs
    monkeypatch.setattr(docs, "search", lambda *a, **k: [])

    captured: dict = {}

    class _Msgs:
        def create(self, **kwargs):
            captured.clear()
            captured.update(kwargs)
            block = type("B", (), {"type": "text", "text": "answer"})()
            return type("R", (), {"content": [block]})()

    monkeypatch.setattr(analyst, "_get_client", lambda: type("C", (), {"messages": _Msgs()})())

    analyst.ask_research("GATE.NS", "q", as_of=None, allow_web=True)
    assert "tools" in captured                                   # live read ⇒ web_search offered
    analyst.ask_research("GATE.NS", "q", as_of=dt.date(2025, 1, 1), allow_web=True)
    assert "tools" not in captured                               # historical as_of ⇒ web barred
