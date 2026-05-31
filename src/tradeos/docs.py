"""Phase 3 — document intelligence (RAG) over concall / results documents.

Pipeline: parse (PDF via pymupdf, or .txt/.md) → chunk → embed (local fastembed, no API key)
→ store in Postgres + pgvector → cosine search → cited "ask-the-call" answer via Claude.

Retrieval works fully offline (local embeddings + pgvector). Only the final synthesised answer
needs ANTHROPIC_API_KEY; without it, `ask()` returns the retrieved excerpts so you still see the
evidence. Like every other agent here, the LLM only explains retrieved text — it never invents.

Retrieval rigour:
  * **Word-boundary chunking** — windows snap to whitespace so a figure like "23.5%" or a word is
    never split across two chunks (which would make that fact unretrievable).
  * **Relevance floor** — `ask()` measures the closest match's cosine distance and flags the answer
    as low-confidence when nothing clears `RAG_MAX_DISTANCE`, instead of confidently answering over
    chunks that don't actually cover the question.
  * **Validated citations** — the answer is structured (Pydantic) and we drop any cited excerpt
    number the model invented, so a citation always points at a real retrieved chunk.
"""

import os
from pathlib import Path

import numpy as np

from .config import CLAUDE_MODEL, RAG_MAX_DISTANCE
from .db import get_connection
from .log import get_logger

EMBED_MODEL = "BAAI/bge-small-en-v1.5"  # 384-dim, local ONNX, L2-normalised output
EMBED_DIM = 384

log = get_logger()
_embedder = None


def _model():
    global _embedder
    if _embedder is None:
        from fastembed import TextEmbedding
        _embedder = TextEmbedding(model_name=EMBED_MODEL)
    return _embedder


def _as_vec(v) -> np.ndarray:
    """Coerce to float32 and guard the dimension — a silent model/column mismatch corrupts search."""
    arr = np.asarray(v, dtype=np.float32)
    if arr.shape[0] != EMBED_DIM:
        raise RuntimeError(
            f"Embedding dim {arr.shape[0]} != EMBED_DIM {EMBED_DIM}. The doc_chunks.embedding column "
            f"is vector({EMBED_DIM}); if you changed EMBED_MODEL, migrate the column to match."
        )
    return arr


def embed(texts: list[str]) -> list[np.ndarray]:
    """Embed passages (documents) for storage."""
    return [_as_vec(v) for v in _model().embed(texts)]


def embed_query(text: str) -> np.ndarray:
    """Embed a search query. bge-small-en-v1.5 is symmetric (query_embed == embed today), but
    query_embed is the correct seam: swap in an asymmetric retrieval model later and the query gets
    its instruction prefix for free, with no call-site change."""
    return _as_vec(next(iter(_model().query_embed([text]))))


def _conn():
    conn = get_connection()
    from pgvector.psycopg import register_vector
    register_vector(conn)
    return conn


def parse_document(path) -> str:
    """Extract text from a .pdf (pymupdf) or a .txt/.md file."""
    p = str(path)
    if p.lower().endswith(".pdf"):
        import fitz  # pymupdf
        with fitz.open(p) as doc:
            return "\n".join(page.get_text() for page in doc)
    return Path(p).read_text(encoding="utf-8", errors="ignore")


def chunk_text(text: str, size: int = 1000, overlap: int = 150) -> list[str]:
    """Overlapping windows that snap to whitespace, so words/numbers are never split mid-token.

    Each non-final chunk ends at a space/newline; each non-first chunk starts just after one. A
    token longer than the window (rare — e.g. a giant URL) falls back to a hard cut so we always
    make forward progress.
    """
    text = text.strip()
    n = len(text)
    if n == 0:
        return []
    overlap = max(0, min(overlap, size // 2))
    chunks: list[str] = []
    start = 0
    while start < n:
        end = min(start + size, n)
        if end < n:
            brk = max(text.rfind(" ", start, end), text.rfind("\n", start, end))
            if brk > start + size // 2:        # snap to a word boundary, but don't shrink too much
                end = brk
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= n:
            break
        nxt = end - overlap                     # step back for context overlap
        if nxt <= start:                        # pathological long token: force progress
            nxt = end
        ws = text.find(" ", nxt)                # snap the next start to a word boundary too
        start = ws + 1 if 0 <= ws < n else nxt
    return chunks


def add_document(symbol: str, path, *, period=None, filing_date=None, source_url=None) -> int:
    """Parse → chunk → embed → store. Re-adding the same source replaces its chunks (idempotent).

    Provenance (all optional, repeated per chunk like `source`):
      * `period`      — the fiscal quarter-end the document covers; drives `tradeos docs status`.
      * `filing_date` — when it was filed / first public (the point-in-time-relevant date).
      * `source_url`  — where it was fetched from (NULL when added manually).
    """
    symbol = symbol.upper()
    source = os.path.basename(str(path))
    chunks = chunk_text(parse_document(path))
    if not chunks:
        return 0
    vectors = embed(chunks)
    rows = [(symbol, source, i, c, v, period, filing_date, source_url)
            for i, (c, v) in enumerate(zip(chunks, vectors))]
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM doc_chunks WHERE symbol=%s AND source=%s", (symbol, source))
        cur.executemany(
            "INSERT INTO doc_chunks "
            "(symbol, source, chunk_index, content, embedding, period, filing_date, source_url) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            rows,
        )
        conn.commit()
    return len(chunks)


def search(symbol: str, query: str, k: int = 5) -> list[dict]:
    """Cosine-nearest chunks for a query (pgvector `<=>` = cosine distance; smaller = closer)."""
    qv = embed_query(query)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT content, source, chunk_index, embedding <=> %s AS dist "
            "FROM doc_chunks WHERE symbol=%s ORDER BY dist LIMIT %s",
            (qv, symbol.upper(), k),
        )
        return [{"content": r[0], "source": r[1], "chunk": r[2], "distance": round(float(r[3]), 3)}
                for r in cur.fetchall()]


_ASK_SYSTEM = (
    "You answer questions about a company using ONLY the provided numbered document excerpts (e.g. "
    "an earnings concall). Cite the excerpts you rely on inline as [1], [2] in your answer, and also "
    "return their numbers in the `citations` field. If the excerpts don't contain the answer, say so "
    "plainly in `answer` and return an empty `citations` list — do NOT use outside knowledge. Be "
    "factual and concise; no investment advice."
)


def _valid_citations(cites, k: int) -> list[int]:
    """Keep only in-range, de-duplicated citation numbers — defends against a model citing a chunk
    that wasn't retrieved (e.g. returning [9] when only 5 excerpts were provided)."""
    seen: set[int] = set()
    out: list[int] = []
    for c in cites or []:
        if isinstance(c, int) and 1 <= c <= k and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def ask(symbol: str, question: str, k: int = 5, max_distance: float | None = None) -> dict:
    """Retrieve + (if a key is set) synthesise a cited answer.

    Returns {answer, citations, hits, weak_evidence, note}. `weak_evidence` is True when even the
    closest excerpt is farther than the relevance floor, so the caller can flag low confidence
    rather than trusting an answer drawn from chunks that don't really cover the question.
    """
    floor = RAG_MAX_DISTANCE if max_distance is None else max_distance
    hits = search(symbol, question, k)
    if not hits:
        return {"answer": None, "citations": [], "hits": [], "weak_evidence": False,
                "note": f"No documents for {symbol.upper()}. "
                        f"Add one:  tradeos docs add {symbol.upper()} <file.pdf>"}

    best = min(h["distance"] for h in hits)
    weak = best > floor
    note = None
    if weak:
        note = (f"Weak evidence — the closest excerpt is {best:.2f} cosine-distance away "
                f"(floor {floor:.2f}); the documents may not cover this. Treat the answer as low-confidence.")

    if not os.getenv("ANTHROPIC_API_KEY"):
        return {"answer": None, "citations": [], "hits": hits, "weak_evidence": weak, "note": note}

    import anthropic
    from pydantic import BaseModel

    class CitedAnswer(BaseModel):
        answer: str
        citations: list[int]   # excerpt numbers actually used

    context = "\n\n".join(f"[{i + 1}] (source: {h['source']})\n{h['content']}" for i, h in enumerate(hits))
    try:
        msg = anthropic.Anthropic().messages.parse(
            model=CLAUDE_MODEL,
            max_tokens=900,
            system=[{"type": "text", "text": _ASK_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": f"Excerpts:\n{context}\n\nQuestion: {question}"}],
            output_format=CitedAnswer,
        )
        out = msg.parsed_output
        if out is None:
            raise RuntimeError("structured output parse returned nothing")
        return {"answer": out.answer, "citations": _valid_citations(out.citations, len(hits)),
                "hits": hits, "weak_evidence": weak, "note": note}
    except Exception as e:  # noqa: BLE001 - a failed synthesis shouldn't lose the retrieved evidence
        log.warning("RAG synthesis failed for %s: %s", symbol.upper(), e)
        return {"answer": None, "citations": [], "hits": hits, "weak_evidence": weak,
                "note": ((note + " ") if note else "") + "Synthesis failed; showing retrieved excerpts."}


# ----------------------------- coverage / freshness -----------------------------
# Manual document ingestion is only reliable if the system KNOWS when it's incomplete. We already
# ingest each holding's latest reported quarter (`fundamentals.period_end`); cross-referencing it
# against the latest ingested document period turns "I forgot to add a transcript" from a silent
# blind spot into a visible, tracked gap.

def _coverage_flag(expected_period, latest_doc_period, has_docs: bool) -> str:
    """Classify one holding's transcript coverage. Pure (no DB) so it's unit-testable.

      MISSING   — no documents at all for this holding.
      UNCHECKED — has documents, but no fundamentals quarter to compare freshness against.
      UNTAGGED  — has documents but none carry a `period`, so freshness can't be verified.
      STALE     — the latest reported quarter is newer than the latest transcript on file.
      OK        — a transcript covers the latest reported quarter (or newer).
    """
    if not has_docs:
        return "MISSING"
    if expected_period is None:
        return "UNCHECKED"
    if latest_doc_period is None:
        return "UNTAGGED"
    return "STALE" if latest_doc_period < expected_period else "OK"


def coverage_status(symbols=None) -> list[dict]:
    """Per-holding document coverage: expected (latest results quarter) vs what's ingested."""
    from .config import load_portfolio
    if symbols is None:
        symbols = [p.symbol for p in load_portfolio()]
    if not symbols:
        return []

    ph = ",".join(["%s"] * len(symbols))
    with get_connection() as c, c.cursor() as cur:
        cur.execute(f"SELECT symbol, max(period_end) FROM fundamentals WHERE symbol IN ({ph}) "
                    f"GROUP BY symbol", list(symbols))
        expected = dict(cur.fetchall())
        cur.execute(f"SELECT symbol, count(*), count(DISTINCT source), max(period), max(ingested_at) "
                    f"FROM doc_chunks WHERE symbol IN ({ph}) GROUP BY symbol", list(symbols))
        docs = {r[0]: {"chunks": r[1], "docs": r[2], "latest_period": r[3], "last_ingested": r[4]}
                for r in cur.fetchall()}

    out = []
    for s in symbols:
        d = docs.get(s)
        has_docs = bool(d and d["chunks"])
        out.append({
            "symbol": s,
            "flag": _coverage_flag(expected.get(s), d["latest_period"] if d else None, has_docs),
            "latest_results": expected.get(s),
            "latest_transcript": d["latest_period"] if d else None,
            "docs": d["docs"] if d else 0,
            "chunks": d["chunks"] if d else 0,
            "last_ingested": d["last_ingested"] if d else None,
        })
    return out
