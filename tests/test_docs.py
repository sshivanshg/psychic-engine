"""Tests for the RAG document layer — chunking, citations, relevance floor, coverage."""

import datetime as dt

import pytest

from tradeos.docs import (
    _coverage_flag,
    _valid_citations,
    ask,
    chunk_text,
    coverage_status,
    parse_document,
)


def test_chunk_text_overlap():
    chunks = chunk_text("x " * 1200, size=1000, overlap=150)   # spaced tokens
    assert len(chunks) >= 3
    assert all(len(c) <= 1000 for c in chunks)


def test_chunk_text_empty():
    assert chunk_text("   ") == []


def test_chunk_never_splits_a_token():
    # Unique fixed-width tokens: a mid-word split would produce a fragment that isn't in the vocab.
    words = [f"w{i:04d}" for i in range(400)]
    text = " ".join(words)
    vocab = set(words)
    chunks = chunk_text(text, size=200, overlap=40)
    assert len(chunks) > 1
    for c in chunks:
        assert all(tok in vocab for tok in c.split())          # every token intact


def test_chunk_long_token_terminates():
    # A single token longer than the window must still chunk (forward progress, no infinite loop).
    chunks = chunk_text("x" * 5000, size=1000, overlap=150)
    assert len(chunks) >= 4
    assert "".join(chunks).count("x") >= 5000                  # full content covered (with overlap)


def test_valid_citations():
    assert _valid_citations([1, 2, 2, 3], 3) == [1, 2, 3]      # de-duplicated
    assert _valid_citations([2, 1], 3) == [2, 1]               # order preserved
    assert _valid_citations([0, 4, 9], 3) == []                # out-of-range dropped
    assert _valid_citations(None, 5) == []                     # missing handled
    assert _valid_citations(["1", 1.5, None], 5) == []         # non-ints dropped


def test_parse_txt(tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("operating margin expanded to 21 percent")
    assert "margin" in parse_document(f).lower()


def test_parse_pdf(tmp_path):
    import fitz  # pymupdf
    p = tmp_path / "note.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "guidance revenue growth 2 to 4 percent")
    doc.save(str(p))
    doc.close()
    text = parse_document(str(p)).lower()
    assert "guidance" in text and "revenue" in text


def test_relevance_floor_flags_low_confidence():
    """The floor must flip weak_evidence purely on the distance threshold (deterministic)."""
    try:
        loose = ask("INFY.NS", "what did management say about margins?", max_distance=0.95)
        tight = ask("INFY.NS", "what did management say about margins?", max_distance=0.01)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"DB/embeddings not available: {e}")
    if not loose["hits"]:
        pytest.skip("no documents ingested for INFY.NS")
    assert loose["weak_evidence"] is False        # closest hit is well within a loose floor
    assert tight["weak_evidence"] is True         # nothing clears an impossibly tight floor
    assert loose["answer"] is None                # no ANTHROPIC_API_KEY ⇒ evidence-only path


def test_coverage_flag_cases():
    q1, q0 = dt.date(2026, 3, 31), dt.date(2025, 12, 31)
    assert _coverage_flag(q1, None, has_docs=False) == "MISSING"     # nothing ingested
    assert _coverage_flag(None, None, has_docs=True) == "UNCHECKED"  # no results baseline
    assert _coverage_flag(q1, None, has_docs=True) == "UNTAGGED"     # doc present, no period tag
    assert _coverage_flag(q1, q0, has_docs=True) == "STALE"          # newer quarter than transcript
    assert _coverage_flag(q1, q1, has_docs=True) == "OK"             # transcript covers latest
    assert _coverage_flag(q0, q1, has_docs=True) == "OK"             # transcript ahead of results


def test_coverage_status_integration():
    try:
        rows = coverage_status()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"DB not available: {e}")
    valid = {"MISSING", "UNCHECKED", "UNTAGGED", "STALE", "OK"}
    for r in rows:
        assert r["flag"] in valid
        assert {"symbol", "latest_results", "latest_transcript", "docs", "chunks"} <= set(r)
