# TradeOS — web dashboard (Phase 5)

A SvelteKit (Svelte 5) SPA over the TradeOS FastAPI backend. It renders the same engine reads the
CLI shows — portfolio overview, per-stock drill-in (dials, reasoning trace, "ask the call" RAG),
the honest signal-eval table, and the pre-market briefing. Descriptive only.

## Run it (two terminals)

**1. Backend** (from the repo root):
```bash
uv run tradeos serve            # → http://127.0.0.1:8000  (OpenAPI docs at /docs)
```

**2. Frontend** (from `web/`):
```bash
npm install
npm run dev                     # → http://localhost:5173
```

The dev server calls the backend cross-origin (CORS is pre-allowed for :5173). Point it elsewhere by
copying `.env.example` to `.env` and setting `VITE_API_BASE`.

## Production (single origin)
```bash
npm run build                   # emits web/build (adapter-static SPA)
uv run tradeos serve            # FastAPI auto-mounts web/build at /
```
Then the whole app is served from `http://127.0.0.1:8000`.

## Pages
- `/` — portfolio overview: risk header + a card per holding (attention, confidence, dials, Δ).
- `/stock/[symbol]` — full card: technical / fundamental / macro / sentiment / ownership, LLM trace, RAG ask.
- `/eval` — the point-in-time signal backtest (IC, Newey-West t, net-of-cost spread, caveats).
- `/briefing` — the morning briefing: alerts that tripped your rules.
