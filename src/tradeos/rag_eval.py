"""Phase 3 — RAG evaluation: does retrieval surface the evidence, and are answers grounded?

'Eval before trust' applied to RAG. The two failure modes are separated the way you must separate
them to debug them:

  * RETRIEVAL quality (measurable OFFLINE, no API key): for each golden question, does the union of
    the top-k retrieved chunks actually CONTAIN the facts needed to answer it? Reports recall over
    the expected snippets, an 'answerable@k' rate (all snippets retrieved), and the best cosine
    distance. This is the half that most determines RAG quality and it needs no LLM.
  * GENERATION faithfulness (needs a key): run ask(), check every citation points at a real
    retrieved chunk (grounding) and the expected fact actually surfaces in the answer.

A pipeline can have great generation over useless retrieval (or vice-versa); scoring them apart is
the whole point. (Tiny corpus ⇒ illustrative — recall@k earns its meaning on real multi-doc filings.)
"""

import json
import os
from pathlib import Path

from .config import PROJECT_ROOT
from .docs import ask, search

GOLDEN_PATH = Path(os.getenv("RAG_GOLDEN_FILE", str(PROJECT_ROOT / "eval" / "rag_golden.json")))


def load_golden(path=None) -> list[dict]:
    p = Path(path) if path else GOLDEN_PATH
    if not p.exists():
        raise FileNotFoundError(f"Golden set not found: {p}. Create it or set RAG_GOLDEN_FILE.")
    return json.loads(p.read_text())


def _score_retrieval(contents: list[str], must_contain: list[str]):
    """Pure: (recall, answerable) for expected snippets over the union of retrieved chunk texts."""
    blob = " ".join(c.lower() for c in contents)
    must = [s.lower() for s in must_contain]
    if not must:
        return None, None
    found = sum(1 for s in must if s in blob)
    recall = found / len(must)
    return recall, recall == 1.0


def evaluate_rag(k: int = 3, with_generation: bool | None = None, golden_path=None) -> dict:
    golden = load_golden(golden_path)
    use_gen = (os.getenv("ANTHROPIC_API_KEY") is not None) if with_generation is None else with_generation

    rows = []
    for item in golden:
        hits = search(item["symbol"], item["question"], k=k)
        recall, answerable = _score_retrieval([h["content"] for h in hits], item.get("must_contain", []))
        row = {
            "symbol": item["symbol"],
            "question": item["question"],
            "hits": len(hits),
            "best_distance": min((h["distance"] for h in hits), default=None),
            "recall": round(recall, 3) if recall is not None else None,
            "answerable": answerable,
        }
        if use_gen:
            res = ask(item["symbol"], item["question"], k=k)
            ans = (res.get("answer") or "").lower()
            cites = res.get("citations", [])
            row["cited"] = len(cites)
            row["grounded"] = bool(cites) and all(1 <= c <= len(hits) for c in cites)
            row["answer_hit"] = any(s.lower() in ans for s in item.get("must_contain", []))
        rows.append(row)

    rec = [r["recall"] for r in rows if r["recall"] is not None]
    ans_ok = [r["answerable"] for r in rows if r["answerable"] is not None]
    dists = [r["best_distance"] for r in rows if r["best_distance"] is not None]
    return {
        "summary": {
            "questions": len(rows),
            "k": k,
            "mean_recall": round(sum(rec) / len(rec), 3) if rec else None,
            "answerable_rate": round(sum(ans_ok) / len(ans_ok), 3) if ans_ok else None,
            "mean_best_distance": round(sum(dists) / len(dists), 3) if dists else None,
            "generation_evaluated": use_gen,
        },
        "rows": rows,
    }
