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

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
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


def _parse_as_of(as_of: str | None):
    if not as_of:
        return None
    try:
        return dt.date.fromisoformat(as_of)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"bad as_of (want YYYY-MM-DD): {e}") from e


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


@app.get("/api/portfolio")
def portfolio(horizon: str = "annual", as_of: str | None = None, narrate: bool = False) -> dict:
    """The dashboard payload: per-stock cards (6 dims + attention + confidence + delta) + overviews."""
    from .orchestrator import analyze
    from .snapshots import annotate_deltas
    aod = _parse_as_of(as_of)
    a = _guard(lambda: analyze(as_of=aod, horizon=horizon, narrate=narrate, snapshot=False))
    annotate_deltas(a["cards"])         # show "what changed vs the last saved run" without persisting a new one
    return _jsonable_analysis(a)


@app.get("/api/stock/{symbol}")
def stock(symbol: str, horizon: str = "annual", as_of: str | None = None, narrate: bool = True) -> dict:
    """One holding's full card + (opt-in) LLM reasoning trace."""
    from .orchestrator import analyze
    from .snapshots import annotate_deltas
    aod = _parse_as_of(as_of)
    a = _guard(lambda: analyze(as_of=aod, horizon=horizon, narrate=narrate, snapshot=False))
    annotate_deltas(a["cards"])
    sym = symbol.upper()
    card = next((c for c in a["cards"] if c["symbol"] == sym), None)
    if card is None:
        raise HTTPException(status_code=404, detail=f"{sym} not in the portfolio analysis")
    nar = a.get("narratives", {}).get(sym)
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
    from .risk import compute_risk
    return _guard(lambda: compute_risk(as_of=_parse_as_of(as_of), horizon=horizon))


@app.get("/api/eval")
def eval_(horizon: int = Query(21, ge=1), step: int = Query(5, ge=1)) -> dict:
    from .eval import evaluate
    return _guard(lambda: evaluate(horizon=horizon, step=step))


@app.get("/api/briefing")
def briefing(horizon: str = "annual", as_of: str | None = None) -> dict:
    from .briefing import run_briefing
    return _guard(lambda: run_briefing(as_of=_parse_as_of(as_of), horizon=horizon))


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
