"""Phase 5 — FastAPI read layer over the engine.

THIN by design: every endpoint calls the same pure engine the CLI uses (`orchestrator.analyze`,
`risk.compute_risk`, `eval.evaluate`, `docs.ask`, `briefing.run_briefing`). No business logic and no
per-agent DB queries live here — the API just serves the engine's reads as JSON. Descriptive only:
it serves risk/technical/fundamental facts, never a buy/sell. LLM narration is opt-in (`?narrate=true`)
and degrades to empty when no `ANTHROPIC_API_KEY` is set, exactly like the CLI.

Run:  uv run tradeos-api   (→ http://127.0.0.1:8000, docs at /docs)
The SvelteKit dev server (5173) calls this cross-origin; production can serve the built SPA from here.
"""

import datetime as dt
import os
import time
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .config import PROJECT_ROOT

app = FastAPI(
    title="TradeOS API",
    version="0.1.0",
    description="Descriptive portfolio intelligence — a read layer over the TradeOS engine.",
)

# The SvelteKit dev server runs on :5173; allow it (and any extra origins via CORS_ORIGINS) to call us.
_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
_origins += [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware, allow_origins=_origins, allow_credentials=False,
    allow_methods=["*"], allow_headers=["*"],
)


def _parse_date(value: str | None, field: str = "date"):
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"bad {field} (want YYYY-MM-DD): {e}") from e


def _parse_as_of(as_of: str | None):
    return _parse_date(as_of, "as_of")


def _jsonable_analysis(a: dict) -> dict:
    """analyze()'s `narratives` holds Pydantic StockCard objects — dump them to plain dicts for JSON."""
    out = dict(a)
    nar = a.get("narratives") or {}
    out["narratives"] = {s: (r.model_dump() if hasattr(r, "model_dump") else r) for s, r in nar.items()}
    return out


def _guard(fn):
    """Run an engine call, mapping the engine's 'no data' RuntimeError to a clean 503."""
    try:
        return fn()
    except RuntimeError as e:           # e.g. "No price data. Run `tradeos ingest` first."
        raise HTTPException(status_code=503, detail=str(e)) from e
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/api/health")
def health() -> dict:
    from .config import _safe_load
    return {"status": "ok", "holdings": len(_safe_load()), "llm": bool(os.getenv("ANTHROPIC_API_KEY"))}


@app.get("/api/holdings")
def holdings() -> list[dict]:
    from .config import _safe_load
    return [{"symbol": p.symbol, "quantity": p.quantity, "avg_cost": p.avg_cost} for p in _safe_load()]


def _cached_analysis(aod, horizon: str) -> dict:
    """The FACTUAL (narrate=False) analysis, memoised by (as_of, horizon) for a short TTL. `memo`
    hands back a deep copy, so callers can safely mutate (annotate_deltas) and narrate on top. Both
    the portfolio and per-stock routes share this key, so the engine runs once per (as_of, horizon)."""
    from .cache import memo
    from .orchestrator import analyze
    return memo(("analysis", str(aod), horizon),
                lambda: analyze(as_of=aod, horizon=horizon, narrate=False, snapshot=False))


@app.get("/api/portfolio")
def portfolio(horizon: str = "annual", as_of: str | None = None, narrate: bool = False) -> dict:
    """The dashboard payload: per-stock cards (6 dims + attention + confidence + delta) + overviews."""
    from .orchestrator import narrate_cards
    from .snapshots import annotate_deltas
    aod = _parse_as_of(as_of)
    a = _guard(lambda: _cached_analysis(aod, horizon))
    annotate_deltas(a["cards"])         # show "what changed vs the last saved run" without persisting a new one
    if narrate:
        a["narratives"] = narrate_cards(a["cards"])
    return _jsonable_analysis(a)


@app.get("/api/stock/{symbol}")
def stock(symbol: str, horizon: str = "annual", as_of: str | None = None, narrate: bool = True) -> dict:
    """One holding's full card + (opt-in) LLM reasoning trace. Reuses the cached factual analysis and
    narrates ONLY this holding — not the whole book (which would fire an LLM call per holding)."""
    from .orchestrator import narrate_cards
    from .snapshots import annotate_deltas
    aod = _parse_as_of(as_of)
    a = _guard(lambda: _cached_analysis(aod, horizon))
    annotate_deltas(a["cards"])
    sym = symbol.upper()
    card = next((c for c in a["cards"] if c["symbol"] == sym), None)
    if card is None:
        raise HTTPException(status_code=404, detail=f"{sym} not in the portfolio analysis")
    nar: Any = (narrate_cards([card]) if narrate else {}).get(sym)   # one holding, not the entire book
    return {"card": card, "narrative": nar.model_dump() if hasattr(nar, "model_dump") else nar}


@app.get("/api/stock/{symbol}/series")
def stock_series(symbol: str, as_of: str | None = None, lookback: int = Query(400, ge=30, le=2000)) -> dict:
    """Point-in-time price history + SMA 20/50/200 + volume for the per-stock chart."""
    from .technical import price_series
    s = price_series(symbol.upper(), _parse_as_of(as_of), lookback=lookback)
    if s is None:
        raise HTTPException(status_code=404, detail=f"no price data for {symbol.upper()}")
    return s


@app.get("/api/risk")
def risk(horizon: str = "annual", as_of: str | None = None) -> dict:
    from .cache import memo
    from .risk import compute_risk
    aod = _parse_as_of(as_of)
    return _guard(lambda: memo(("risk", str(aod), horizon),
                               lambda: compute_risk(as_of=aod, horizon=horizon)))


@app.get("/api/eval")
def eval_(horizon: int = Query(21, ge=1), step: int = Query(5, ge=1)) -> dict:
    from .cache import memo
    from .eval import evaluate
    return _guard(lambda: memo(("eval", horizon, step), lambda: evaluate(horizon=horizon, step=step)))


@app.get("/api/briefing")
def briefing(horizon: str = "annual", as_of: str | None = None) -> dict:
    from .briefing import run_briefing
    return _guard(lambda: run_briefing(as_of=_parse_as_of(as_of), horizon=horizon))


def _sse_default(o):
    """JSON fallback for the event stream: numpy scalars → python, dates → ISO, else str."""
    if hasattr(o, "item"):                      # numpy scalar (np.float64 etc.)
        try:
            return o.item()
        except Exception:                        # noqa: BLE001
            pass
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    return str(o)


@app.get("/api/stream/analyze")
def stream_analyze(horizon: str = "annual", as_of: str | None = None, narrate: bool = True):
    """LIVE multi-agent run as Server-Sent Events — the dashboard's Reasoning Monitor subscribes here
    and watches every stage as it happens: data load → each of the 6 agents (with its output) →
    per-holding reads → LLM synthesis. Runs the engine FRESH (uncached) with `on_event` wired to a
    queue; a worker thread runs `analyze`, this generator streams its events. snapshot=False (a 'watch'
    run shouldn't persist a snapshot). Descriptive only — it explains, it never advises."""
    import json
    import queue
    import threading

    from .orchestrator import analyze

    aod = _parse_as_of(as_of)
    events: queue.Queue = queue.Queue()
    DONE = object()

    def worker() -> None:
        try:
            analyze(as_of=aod, horizon=horizon, narrate=narrate, snapshot=False,
                    on_event=lambda etype, payload: events.put({"type": etype, "payload": payload}))
        except Exception as e:                   # surface the engine's "no data" etc. as an event
            events.put({"type": "error", "payload": {"message": str(e)}})
        finally:
            events.put(DONE)

    threading.Thread(target=worker, daemon=True).start()
    pacing = float(os.getenv("STREAM_PACING_MS", "140")) / 1000.0

    def gen():
        yield ": reasoning-monitor stream open\n\n"     # prompt the client to start rendering
        while True:
            item = events.get()
            if item is DONE:
                yield "event: end\ndata: {}\n\n"
                return
            yield f"data: {json.dumps(item, default=_sse_default)}\n\n"
            # Pace the deterministic cascade so it's watchable; LLM events arrive in real time.
            if pacing > 0 and not str(item.get("type", "")).startswith("narration"):
                time.sleep(pacing)

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.get("/api/docs/status")
def docs_status() -> list[dict]:
    from .docs import coverage_status
    rows = _guard(coverage_status)
    return [{**r, "latest_results": str(r["latest_results"]) if r.get("latest_results") else None,
             "latest_transcript": str(r["latest_transcript"]) if r.get("latest_transcript") else None,
             "last_ingested": str(r["last_ingested"]) if r.get("last_ingested") else None} for r in rows]


class AskRequest(BaseModel):
    symbol: str
    question: str


@app.post("/api/ask")
def ask(req: AskRequest) -> dict:
    from .docs import ask as rag_ask
    return _guard(lambda: rag_ask(req.symbol, req.question))


# ----------------------------------------------------------------------------------------------
# WRITE SEAM (mutations). Still THIN: each route just calls the SAME function the CLI uses
# (config.add_holding / remove_holding, ingest.*, docs.add_document) — I/O orchestration, never
# quant logic — and clears the read cache so the dashboard reflects the change immediately. Safe at
# the 127.0.0.1 single-user default; there is no auth, so don't expose these on 0.0.0.0 unguarded.
# ----------------------------------------------------------------------------------------------

_ALLOWED_DOC_EXT = {".pdf", ".txt", ".md"}
_MAX_DOC_BYTES = 25 * 1024 * 1024          # 25 MB — a generous cap for a results PDF / transcript


def _holdings_payload(positions: list) -> list[dict]:
    return [{"symbol": p.symbol, "quantity": p.quantity, "avg_cost": p.avg_cost} for p in positions]


def _clear_read_cache() -> None:
    from .cache import clear
    clear()


class HoldingRequest(BaseModel):
    symbol: str
    quantity: float
    avg_cost: float | None = None
    fetch: bool = True                     # also pull this name's price history now (like `tradeos add`)


@app.post("/api/holdings")
def add_holding_route(req: HoldingRequest) -> dict:
    """Add/replace a holding (writes holdings.csv via the same `config.add_holding` the CLI uses),
    optionally fetch its price history, and invalidate the read cache."""
    from .config import add_holding
    sym = req.symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol is required")
    positions = _guard(lambda: add_holding(sym, req.quantity, req.avg_cost))
    warning: str | None = None
    if req.fetch:
        try:
            from .ingest import ingest_symbols
            ingest_symbols([sym], with_benchmark=True)
        except Exception as e:             # a data hiccup must not lose the persisted holding
            warning = f"holding saved, but price fetch failed: {e}"
    _clear_read_cache()
    return {"holdings": _holdings_payload(positions), "fetched": req.fetch and warning is None,
            "warning": warning}


@app.delete("/api/holdings/{symbol}")
def remove_holding_route(symbol: str) -> dict:
    """Remove a holding (same `config.remove_holding` as `tradeos remove`) and invalidate the cache."""
    from .config import remove_holding
    sym = symbol.strip().upper()
    positions = _guard(lambda: remove_holding(sym))
    _clear_read_cache()
    return {"holdings": _holdings_payload(positions)}


class IngestRequest(BaseModel):
    symbols: list[str] | None = None


@app.post("/api/ingest")
def ingest_route(req: IngestRequest | None = None) -> dict:
    """Refresh price/fundamentals data (same engine as `tradeos ingest`). With `symbols`, refreshes
    just those names; otherwise the whole book. Can take a while (it hits yfinance)."""
    syms = [s.strip().upper() for s in (req.symbols if req and req.symbols else []) if s.strip()]

    def run() -> int | None:
        if syms:
            from .ingest import ingest_symbols
            return ingest_symbols(syms, with_benchmark=True)
        from .ingest import ingest
        ingest()
        return None

    rows = _guard(run)
    _clear_read_cache()
    return {"status": "ok", "symbols": syms or "all", "rows": rows}


@app.post("/api/docs")
async def docs_add(
    symbol: str = Form(...),
    file: UploadFile = File(...),
    period: str | None = Form(None),
    filing_date: str | None = Form(None),
    source_url: str | None = Form(None),
) -> dict:
    """Upload a quarterly result / concall transcript (PDF/txt/md): parse → chunk → embed → store via
    the same `docs.add_document` as `tradeos docs add`. `period` (quarter-end, YYYY-MM-DD) enables the
    freshness/coverage check; `filing_date` is the point-in-time public date used by the engine."""
    from .docs import add_document
    sym = symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol is required")
    name = os.path.basename(file.filename or "").strip()
    ext = os.path.splitext(name)[1].lower()
    if not name or ext not in _ALLOWED_DOC_EXT:
        raise HTTPException(status_code=400,
                            detail=f"unsupported file '{name}'. Allowed types: pdf, txt, md")
    per = _parse_date(period, "period")
    fil = _parse_date(filing_date, "filing_date")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    if len(data) > _MAX_DOC_BYTES:
        raise HTTPException(status_code=413, detail=f"file too large (max {_MAX_DOC_BYTES // (1024*1024)} MB)")

    uploads = PROJECT_ROOT / "data" / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    dest = uploads / f"{sym}__{name}"      # basename-sanitised above ⇒ stays inside uploads/
    dest.write_bytes(data)

    n = _guard(lambda: add_document(sym, dest, period=per, filing_date=fil, source_url=source_url or None))
    _clear_read_cache()
    return {"symbol": sym, "source": name, "chunks": n, "period": period, "stored": bool(n),
            "note": None if n else "no text could be extracted from the file"}


# In production, `npm run build` (adapter-static) emits web/build; serve the SPA from here if present.
# Assets live under /_app; every other non-/api path falls back to index.html so client-side deep
# links (e.g. /stock/INFY.NS) survive a hard refresh. The /api routes above are matched first.
_WEB_BUILD = PROJECT_ROOT / "web" / "build"
if _WEB_BUILD.is_dir():
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    _assets = _WEB_BUILD / "_app"
    if _assets.is_dir():
        app.mount("/_app", StaticFiles(directory=str(_assets)), name="assets")

    @app.get("/{path:path}")
    def spa(path: str) -> FileResponse:
        candidate = _WEB_BUILD / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_WEB_BUILD / "index.html")   # SPA fallback


def serve(host: str = "", port: int = 0) -> None:
    """Entry point for `uv run tradeos-api` (host/port fall back to API_HOST/API_PORT env, then defaults)."""
    import uvicorn
    resolved_host = host or os.getenv("API_HOST", "127.0.0.1")
    resolved_port = port or int(os.getenv("API_PORT", "8000"))
    uvicorn.run("tradeos.api:app", host=resolved_host, port=resolved_port, reload=False)
