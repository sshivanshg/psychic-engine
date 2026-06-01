# TradeOS

A scheduled, **multi-agent equity-analysis system** that runs on a real Indian-equity portfolio.
Six pure analyzers (risk · technical · fundamental · macro/sector · sentiment · ownership) feed a
hand-built orchestrator that produces, per holding: categorical dials, a decomposable **attention
score**, a **calibrated confidence**, a run-over-run **what-changed delta**, and an optional LLM
reasoning trace. A point-in-time **eval harness** measures whether the signals actually predict
forward returns (cross-sectional IC, Newey-West HAC t-stats, net-of-cost spreads). A thin FastAPI
read layer + a SvelteKit dashboard sit on top.

**Three bright lines the whole system honors:**
- **Descriptive, never prescriptive** — it explains risk/technicals/fundamentals; it never says buy/sell/hold.
- **Point-in-time** — analysis for `(symbol, as_of)` uses only data known by `as_of` (fundamentals apply an announcement lag).
- **Honest** — missing data → `None`/"no data"; a confidently-wrong number is treated as worse than no number.

> Personal learning project + hireable showcase. Free public data only (yfinance). The quant core is
> pure/deterministic; the LLM layer is optional and degrades to "numbers only" without an API key.

---

## Architecture at a glance

```
ingest (yfinance, RAG docs)
      │
      ▼
Postgres + pgvector   prices · price_vintages · fundamentals · security_meta ·
      │               doc_chunks · guidance · sentiment · ownership · run_snapshots
      ▼
AnalysisContext  ── loads everything ONCE, point-in-time ──┐
      ▼                                                     │
6 pure agents (REGISTRY) ──► orchestrator ──► per-stock cards (dials + attention +
      │                                        confidence + delta) ──► optional Claude synthesis
      ▼
eval.py (backtest)   ·   api.py (FastAPI read layer) ──► web/ (SvelteKit dashboard)
                                       └─ cache.py memoises factual reads; db.py pools connections
```

---

## 1. Prerequisites

| Tool | Why | Install |
|------|-----|---------|
| **Python 3.12** | pinned in `.python-version` (uv fetches it) | via uv |
| **`uv`** | env + lockfile + scripts | https://docs.astral.sh/uv/ |
| **PostgreSQL 14+** with **pgvector** | the data layer (pgvector backs RAG) | Homebrew (below) or Docker |
| **Node 18+ / npm** | only for the `web/` dashboard | https://nodejs.org |
| **`ANTHROPIC_API_KEY`** | *optional* — LLM narration + RAG answers | https://console.anthropic.com |

Without an API key everything still runs — you get all the numbers, just no Claude-written prose.

---

## 2. Setup

```bash
# 2.1 — install Python deps (creates .venv/, fetches Python 3.12, writes uv.lock)
uv sync

# 2.2 — Postgres + pgvector (Homebrew — the canonical local setup)
brew install postgresql@16 pgvector
brew services start postgresql@16
createdb tradeos
#   (if your role differs, set DATABASE_URL in .env — see the env table below)

# 2.3 — apply the schema (idempotent: creates tables, the vector extension, indexes)
psql -d tradeos -f db/init/01_init.sql

# 2.4 — config (optional): copy the template and edit only what you need
cp .env.example .env          # then add ANTHROPIC_API_KEY / change DATABASE_URL if needed

# 2.5 — your portfolio: add holdings (see CLI below) or edit holdings.csv directly
uv run tradeos add RELIANCE.NS 10 2400
```

**Docker alternative for the DB** (instead of 2.2): `docker compose up -d` brings up Postgres on
`localhost:5432` with creds `tradeos/tradeos`. Note the bundled image is TimescaleDB — make sure the
`vector` extension is available before `psql -f db/init/01_init.sql`, or RAG (`docs`/`ask`) won't work.

**Your portfolio file (`holdings.csv`)** — columns `symbol,quantity,avg_cost` (avg_cost optional,
unlocks unrealized P&L). NSE tickers end in `.NS`, BSE in `.BO`. Keep it **private** — it's your real
book and is git-ignored. `holdings.example.csv` is a reference template.

---

## 3. Quick start (first analysis in 3 commands)

```bash
uv run tradeos ingest                 # pull ~2y daily prices + fundamentals + meta for your book
uv run tradeos risk --no-llm          # portfolio risk read (numbers only, no key needed)
uv run tradeos analyze --no-llm       # per-stock cards: dials + attention + confidence + delta
```

Add `ANTHROPIC_API_KEY` to your `.env`, drop `--no-llm`, and the same commands add Claude's
descriptive synthesis on top.

---

## 4. Full CLI reference

Run everything via `uv run tradeos <command>`. `uv run tradeos --help` (or `<command> --help`) prints usage.

### Portfolio management
| Command | What it does |
|---------|--------------|
| `tradeos add SYMBOL QTY [AVG_COST]` | add/update a holding **and fetch its data**. `--no-fetch` to skip the fetch. |
| `tradeos remove SYMBOL` | remove a holding |
| `tradeos holdings` | list your portfolio |

### Data
| Command | What it does |
|---------|--------------|
| `tradeos ingest` | refresh prices + fundamentals + sector/news/ownership for the whole universe (holdings ∪ `universe.csv`) + benchmark. Idempotent (UPSERT) — safe to re-run daily. |
| `tradeos check` | DB row counts + date ranges per ticker (the "data is really there" check) |

### Analysis
| Command | What it does |
|---------|--------------|
| `tradeos risk [--horizon d/w/m/q/y\|N] [--as-of YYYY-MM-DD] [--no-llm]` | portfolio risk: EWMA vol, beta, VaR/CVaR (historical + FHS), component risk %, liquidity, limit checks |
| `tradeos analyze [--horizon …] [--as-of …] [--no-llm] [--no-snapshot]` | the multi-agent per-stock cards (6 dims + attention + confidence + what-changed delta). `--no-snapshot` skips storing/diffing the run. |
| `tradeos briefing [--horizon …] [--as-of …]` | pre-market summary + alerts on your rules (top risk contributor, downtrend near lows, earnings declining, margins contracting, negative news, changed-since-last) |

`--as-of` is the point-in-time hook: every read is filtered to `date <= as_of` (no look-ahead).
`--horizon` accepts `d/w/m/q/y`, aliases (`weekly`, `annual`, …), or `N`/`Nd` trading days.

### Documents & RAG (concall / results PDFs)
| Command | What it does |
|---------|--------------|
| `tradeos docs add SYMBOL FILE [--period YYYY-MM-DD] [--filing-date YYYY-MM-DD] [--url URL]` | parse → chunk → embed (local, no key) → store in pgvector. `--period` enables freshness checks. |
| `tradeos docs list [SYMBOL]` | list ingested documents |
| `tradeos docs status [SYMBOL]` | coverage: which holdings are MISSING / STALE / UNTAGGED on transcripts |
| `tradeos ask SYMBOL "QUESTION"` | RAG answer over that symbol's docs, with citations (needs a key for prose; otherwise shows the retrieved excerpts) |
| `tradeos extract SYMBOL` | extract structured concall guidance (revenue/margin outlook + quotes) → feeds the Fundamental agent |

### Backtest / eval
| Command | What it does |
|---------|--------------|
| `tradeos eval [--horizon N] [--step N]` | does each signal predict forward returns? Cross-sectional IC, ICIR, Newey-West t, hit-rate, gross + net-of-cost tercile spread, **p-values + Bonferroni** multiple-testing deflation |
| `tradeos rag-eval [--k N]` | RAG retrieval quality (recall@k / answerable / best-distance) vs a golden set; also scores generation if a key is set |

### Serve the dashboard backend
| Command | What it does |
|---------|--------------|
| `tradeos serve [--host 127.0.0.1] [--port 8000]` | run the FastAPI read API (interactive docs at `/docs`); serves the built SPA if `web/build` exists |

**Script aliases** (same entry points, defined in `pyproject.toml`): `tradeos-ingest`,
`tradeos-check`, `tradeos-risk`, `tradeos-analyze`, `tradeos-api`. e.g. `uv run tradeos-risk --no-llm`.

### HTTP API (read-only JSON)
Start with `uv run tradeos serve`, then:
`GET /api/health` · `/api/holdings` · `/api/portfolio` · `/api/stock/{sym}` ·
`/api/stock/{sym}/series` · `/api/risk` · `/api/eval` · `/api/briefing` · `/api/docs/status` ·
`POST /api/ask {symbol, question}`. Most take `?horizon=` and `?as_of=`; `?narrate=true` adds the LLM
trace (per-stock narration is scoped to just that holding). Factual reads are cached (see knobs below).

---

## 5. The web dashboard (`web/`)

SvelteKit (Svelte 5) + TypeScript + Apache ECharts. It only ever talks to the API — never the DB.

```bash
cd web
npm install
npm run dev        # dev server on http://localhost:5173 (proxies to the API at :8000)
npm run check      # svelte-check (keep it clean)
npm run build      # adapter-static → web/build (then FastAPI serves it at the API origin)
```

Run the backend in another terminal: `uv run tradeos serve` (→ :8000). Override the API base for the
dev server with `VITE_API_BASE` in `web/.env` (template: `web/.env.example`). Pages: portfolio
overview (KPI tiles · risk-vs-weight bars · sector donut · correlation heatmap · cards), per-stock
drill-in (price+SMA chart · gauges · returns · trace · RAG ask), eval table, and briefing.

---

## 6. Environment variables

All optional — sane defaults apply. Set in `.env` (loaded automatically) or the shell.

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | `postgresql://tradeos:tradeos@localhost:5432/tradeos` | Postgres connection string |
| `HISTORY_PERIOD` | `2y` | how much daily history to pull (`1y`/`2y`/`5y`/`max`) |
| `BENCHMARK` | `^NSEI` | index for beta (NIFTY 50) |
| `ANTHROPIC_API_KEY` | *(unset)* | enables LLM narration + RAG answers |
| `CLAUDE_MODEL` | `claude-opus-4-8` | narration model (`claude-sonnet-4-6` / `claude-haiku-4-5` are cheaper) |
| `COV_SHRINKAGE` | `1` | Ledoit-Wolf covariance shrinkage on/off (`0` to disable) |
| `SECTOR_CONCENTRATION_PCT` | `40.0` | macro: flag a sector above this % of the book |
| `ANNOUNCEMENT_LAG_DAYS` | `45` | days after quarter-end before results are "known" (point-in-time) |
| `COST_BPS` | `15` | per-leg transaction cost for the eval's net spread |
| `RAG_MAX_DISTANCE` | `0.45` | cosine-distance floor above which `ask` flags weak evidence |
| `PORTFOLIO_FILE` | `holdings.csv` | your holdings file |
| `UNIVERSE_FILE` | `universe.csv` | extra (sold/delisted) names for a survivorship-free eval universe |
| `API_HOST` / `API_PORT` | `127.0.0.1` / `8000` | where `tradeos serve` binds |
| `CORS_ORIGINS` | *(none extra)* | comma-separated extra origins allowed to call the API |
| `READ_CACHE_TTL` | `300` | seconds the API caches factual reads; `0` disables caching |
| `DB_POOL_MIN` / `DB_POOL_MAX` / `DB_POOL_TIMEOUT` | `1` / `10` / `5` | connection-pool sizing + acquire timeout (seconds) |

---

## 7. Point-in-time & honesty model (why outputs are trustworthy)

- **`--as-of`** filters every read to `date <= as_of`; fundamentals additionally require
  `period_end + ANNOUNCEMENT_LAG_DAYS <= as_of`, so a replay never sees results before they were public.
- **`price_vintages`** is an append-only revision log: `eval --vintage_asof` (and `risk.load_panels_asof`)
  reconstruct prices *as they were known* at a date, so a backtest isn't silently re-stated by a later
  yfinance re-adjustment.
- **Survivorship**: the eval universe = holdings ∪ `universe.csv` (add sold/delisted names there).
- **Honest stats**: overlapping windows use Newey-West HAC t-stats; multiple signals are deflated
  (Bonferroni); small-universe results are labelled underpowered, not dressed up.
- **Provenance**: the risk read surfaces `cov_obs`/`var_obs` (the common-sample length behind the
  covariance and VaR) and `data_warnings` when a short-history holding truncates the sample; horizon
  VaR is labelled a √T (iid) approximation while the 1-day VaR limit check stays exact.

---

## 8. Performance

- **Connection pool** (`db.py`, `psycopg_pool`) reuses connections across the ~6 reads each
  `analyze` makes — tune with `DB_POOL_*`.
- **Read-layer cache** (`cache.py`) memoises the factual analysis/risk/eval per `(as_of, horizon)` for
  `READ_CACHE_TTL` seconds and hands back deep copies; the portfolio and every per-stock page share one
  computation. After an ingest, restart the server (or wait out the TTL) to pick up fresh prices.
- The per-stock endpoint narrates **only the requested holding**, not the whole book.

---

## 9. Development

```bash
uv run pytest -q                 # 117 tests; DB-backed integration tests skip cleanly without Postgres
uv run ruff check .              # lint (must stay clean)
uv run mypy src/tradeos          # type-check (must stay clean)
```

CI (`.github/workflows/ci.yml`) runs all three on every push/PR. Definition of done: Prime Directives
intact, quant core pure, mypy+ruff clean, pytest green, a point-in-time invariant test for any new
analyzer, repo map updated. See `CLAUDE.md` for the full contributor contract and `ROADMAP.md` for phases.

---

## 10. Security notes

- The API has **no auth** and binds to `127.0.0.1` by default — keep it local. Do **not** set
  `API_HOST=0.0.0.0` on an untrusted network: `/api/holdings` exposes your book and `?narrate=true` /
  `/api/ask` spend Claude tokens.
- Keep `holdings.csv` (and `data/`) out of version control — they hold your real positions and private
  documents (both are git-ignored).

---

## 11. Troubleshooting

- **`No price data found. Run tradeos ingest first.`** → run `uv run tradeos ingest`.
- **`connection refused` / pool timeout** → Postgres isn't up. `brew services list` (or `docker compose ps`); check `DATABASE_URL`.
- **`no data returned` for a ticker** → wrong symbol/suffix. NSE needs `.NS`, BSE `.BO`.
- **RAG / `ask` errors about the `vector` type** → pgvector isn't installed/enabled; re-run `psql -d tradeos -f db/init/01_init.sql` on a Postgres that has pgvector.
- **LLM steps do nothing** → `ANTHROPIC_API_KEY` isn't set; that's expected — the numbers still print.
- **Dashboard can't reach the API** → start `uv run tradeos serve` and/or set `VITE_API_BASE` in `web/.env`.
