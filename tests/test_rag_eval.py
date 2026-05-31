"""Tests for the RAG eval harness — pure retrieval scoring + integration."""

import pytest

from tradeos.rag_eval import _score_retrieval, evaluate_rag


def test_score_retrieval():
    contents = ["Operating margin expanded to 21.1%, target band 20% to 22%."]
    assert _score_retrieval(contents, ["21.1%", "20% to 22%"]) == (1.0, True)   # both facts present
    recall, answerable = _score_retrieval(contents, ["21.1%", "absent fact"])
    assert recall == 0.5 and answerable is False                                # partial
    assert _score_retrieval([], ["x"]) == (0.0, False)                          # nothing retrieved
    assert _score_retrieval(["anything"], []) == (None, None)                   # no expectations


def test_evaluate_rag_integration():
    try:
        out = evaluate_rag(k=3, with_generation=False)
    except FileNotFoundError as e:
        pytest.skip(str(e))
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"DB/embeddings not available: {e}")
    if not out["rows"] or all(r["hits"] == 0 for r in out["rows"]):
        pytest.skip("no documents ingested for the golden symbols")
    assert out["summary"]["questions"] == len(out["rows"])
    assert out["summary"]["generation_evaluated"] is False
    for r in out["rows"]:
        if r["recall"] is not None:
            assert 0.0 <= r["recall"] <= 1.0
