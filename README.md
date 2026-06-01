# TradeOS

A **multi-angle equity sense-making system** that runs on real Indian-equity data. You give it a stock;
it pulls **every angle** — deep technicals, fundamentals + a quarterly-results trend, live web news with
auto-tagged catalysts, ownership, risk, and (when you've fed it concalls) a **management-credibility
read** — then a final AI agent weighs the bull and bear and writes a **small, honest verdict**.

It is built around one idea most "AI trading" tools get wrong:

> **Sense-making, not forecasting.** Reading and reconciling the whole information surface of a company
> is what LLMs are genuinely good at. *Predicting prices is not* — and this repo proves it: the eval
> harness, run on 106 NSE names over 10 years, finds **no simple signal with bankable, cost-surviving
> edge**. So TradeOS never tells you to buy or sell. It explains; **you make the call.**

Three bright lines the whole system honors:
- **Descriptive, never prescriptive** — it explains risk/technicals/fundamentals/news; never buy/sell/hold (SEBI).
- **Point-in-time** — analysis for `(symbol, as_of)` uses only data known by `as_of`; live web news is fetched only for a *live* read, never for a historical replay (that would be look-ahead).
- **Honest** — missing data → `None`/"no data"; a confidently-wrong number is treated as worse than no number; every number is traceable to its inputs and `as_of`.

> Personal learning project + hireable showcase. Free/cheap data only (yfinance + the agent's own web search).

---

## What you actually get

**1. The Analyst Engine — `python -m tradeos.analyst SYMBOL`** *(the headline)*
One symbol in → every fetched detail (free, deterministic) → one small AI verdict:

```
EPIGRAL.NS  →  TECHNICAL (trend/RSI/MACD/SMA/returns/52w) · FUNDAMENTAL + last-6-quarter trend ·
               LIVE NEWS + auto-tagged catalysts · OWNERSHIP · RISK (vol/β) · attention/confidence ·
               MANAGEMENT CREDIBILITY (guidance → delivered)
            →  FINAL VERDICT: one-liner · bull · bear · watch · confidence
```
Every brief is **journaled** (`analyst_runs`) so the dashboard can show past briefs and how the read evolved.

**2. Portfolio risk + an honest signal eval** — a desk-grade risk engine (EWMA covariance, VaR/CVaR,
component risk, limits) and a backtest harness that *measures* whether signals predict forward returns
(cross-sectional IC, Newey-West HAC t-stats, net-of-cost spreads, multiple-testing deflation) — and
reports the uncertainty so the number can't quietly lie to you.

**3. A SvelteKit dashboard** — per-stock analyst workbench (with a History tab), a global Briefs feed,
portfolio overview, a live multi-agent Reasoning Monitor, newsroom, and data-coverage map.

---

## Architecture at a glance

```
ingest (yfinance prices · fundamentals · sector · ownership)        concalls → docs.py (RAG, pgvector)
      │                                                                   │
      ▼                                                                   ▼
Postgres + pgvector   prices · price_vintages · fundamentals · security_meta · doc_chunks ·
      │               guidance · sentiment(live news) · ownership · run_snapshots · analyst_runs
      ▼
┌─ Analyst engine (analyst.py) ── per symbol ──────────────────────────────────────────────┐
│   assemble_facts (6 pure analyzers, free)  +  events.py (catalyst tags)  +  quarterly trend │
│   +  news.py (LIVE web search, cached)      +  credibility.py (guidance→delivered)           │
│   →  ONE small Haiku verdict (bull/bear/judge)  →  saved to analyst_runs                      │
└──────────────────────────────────────────────────────────────────────────────────────────┘
      │                                         │
      ▼                                         ▼
eval.py (backtest: IC/Newey-West/net-of-cost)   api.py (FastAPI read+write layer) ──► web/ (SvelteKit)
risk.py (EWMA cov · VaR · component risk · limits)        research/ (ERC sizer · sector-neutral eval)
```

The **quant core is pure/deterministic** (no network, no `datetime.now()` in compute). The **LLM layer
is separate, optional, and degrades** to "numbers only" without an API key.

---

## 1. Prerequisites

| Tool | Why | Install |
|------|-----|---------|
| **Python 3.12** | pinned in `.python-version` | via `uv` |
| **`uv`** | env + lockfile + scripts | https://docs.astral.sh/uv/ |
| **PostgreSQL 14+** with **pgvector** | the data layer (pgvector backs RAG) | Homebrew (below) |
| **Node 18+ / npm** | only for the `web/` dashboard | https://nodejs.org |
| **`ANTHROPIC_API_KEY`** | the AI verdict, credibility, live web news, RAG answers | https://console.anthropic.com |

Without a key everything deterministic still runs — you get all the numbers and a dial-based verdict,
just no AI synthesis / live news / credibility.

---

## 2. Setup

```bash
uv sync                                        # env + deps (fetches Python 3.12, writes uv.lock)

brew install postgresql@16 pgvector            # the canonical local DB
brew services start postgresql@16
createdb tradeos
psql -d tradeos -f db/init/01_init.sql         # idempotent: tables + vector extension + indexes

cp .env.example .env                           # then add ANTHROPIC_API_KEY (and DATABASE_URL if your role differs)

uv run tradeos add RELIANCE.NS 10 2400         # add a holding (fetches its data); or edit holdings.csv
```

`holdings.csv` (columns `symbol,quantity,avg_cost`, avg_cost optional) is **your real book** — git-ignored.
NSE tickers end in `.NS`, BSE in `.BO`.

---

## 3. Quick start

```bash
# A — analyse ANY stock (held or not). Pulls every angle + a small AI verdict.
uv run tradeos ingest                          # prices + fundamentals for your book ∪ universe.csv
uv run python -m tradeos.analyst RELIANCE.NS    # full detail + live-news + verdict  (~$0.05 first run, ~$0.003 cached)
uv run python -m tradeos.analyst RELIANCE.NS --no-live-news   # skip the web search (deterministic + verdict only)

# B — portfolio risk + the honest signal eval
uv run tradeos risk --no-llm                    # EWMA vol, beta, VaR/CVaR, component risk, limit checks
uv run tradeos eval                             # do the signals predict returns? (IC, Newey-West t, net spread)
```

---

## 4. The analyst engine in depth

### What feeds a brief (and what it costs)

| Angle | Source | Cost |
|------|--------|------|
| Technical · fundamental · quarterly-results trend · risk · ownership · attention/confidence | the 6 pure analyzers over one point-in-time data load | **free** |
| **Catalysts** | `events.py` — keyword classifier tags each headline (results / legal / deal / rating / management / capex / …) | **free** |
| **Live news** | `news.py` — the agent **web-searches** for recent news, scores + stores it in `sentiment` | ~**$0.05** per fresh fetch, then **cached 24h** |
| **Management credibility** | `credibility.py` — pairs stored concall guidance with the actual results that followed → delivered/partial/missed/too-early | ~**$0.004**, only when a concall is ingested |
| **Final verdict** | ONE small `claude-haiku-4-5` call: steelman bull + bear, reconcile | ~**$0.003** |

So a **first** brief on a name ≈ **$0.05** (the web search dominates); **repeat** briefs within 24h reuse
the cached news ≈ **$0.003**. The cost line is printed under every brief, e.g.
`[verdict: 1 call · 892+419 tok · ~$0.0030 · live news: 5 items, 23679 tok +search ~$0.046]`.

### Model choice (cost vs precision)
The engine defaults to **Haiku** (cheap, fast). Haiku occasionally mis-states a number — for a
decision-grade brief, run with the precise model:
```bash
ANALYST_MODEL=claude-sonnet-4-6 uv run python -m tradeos.analyst SYMBOL
```

### Management credibility — activate it
Credibility needs the **concall transcript** (not the results filing). Ingest one, extract guidance, re-run:
```bash
uv run tradeos docs add SYMBOL transcript.pdf   # parse → chunk → embed (local, free)
uv run tradeos extract SYMBOL                    # RAG-extract management guidance → guidance table
uv run python -m tradeos.analyst SYMBOL          # the brief now scores guidance → delivered
```
Free transcripts: screener.in → the stock → **Concalls** → *Transcript*.

### Past briefs (history)
Every verdict run is saved. See them in the dashboard: **`/analyst/SYMBOL` → History tab** (per name), or
**`/runs`** (the global Briefs feed, all names). The history is a true journal — it shows each brief *as it
was*, with no re-fetch and no re-spend.

---

## 5. CLI reference

Run everything via `uv run tradeos <command>` (`--help` on any command).

| Command | What it does |
|---------|--------------|
| `tradeos add SYMBOL QTY [AVG_COST]` · `remove SYMBOL` · `holdings` | manage your book (`add` also fetches data) |
| `tradeos ingest` | refresh prices + fundamentals + sector + ownership for holdings ∪ `universe.csv` (+ benchmark). Idempotent. *(news is no longer pre-ingested — it's fetched live per brief)* |
| `tradeos check` | DB row counts + date ranges per ticker |
| `tradeos risk [--horizon d/w/m/q/y\|N] [--as-of …] [--no-llm]` | portfolio risk: EWMA vol, beta, VaR/CVaR (hist + FHS), component risk %, liquidity, limits |
| `tradeos analyze [--horizon …] [--as-of …] [--no-llm] [--no-snapshot]` | the multi-agent per-stock cards (6 dims + attention + confidence + what-changed delta) |
| `tradeos briefing [--horizon …] [--as-of …]` | pre-market summary + alert rules |
| **`python -m tradeos.analyst SYMBOL [--as-of …] [--no-live-news]`** | **the analyst engine** — full detail + live news + AI verdict for one name |
| `tradeos docs add SYMBOL FILE [--period …] [--filing-date …]` · `docs list` · `docs status` | RAG corpus (concalls/results) |
| `tradeos ask SYMBOL "QUESTION"` | RAG answer over that name's docs, with citations |
| `tradeos extract SYMBOL` | extract structured concall guidance → feeds Fundamental + credibility |
| `tradeos eval [--horizon N] [--step N]` | does each signal predict forward returns? IC · ICIR · Newey-West t · net-of-cost spread · Bonferroni |
| `tradeos rag-eval [--k N]` | RAG retrieval quality vs a golden set |
| `tradeos serve [--host … --port …]` | run the FastAPI backend (docs at `/docs`; serves the built SPA if present) |

**Research scripts** (not wired into the CLI; reuse the pure engine):
```bash
uv run python research/sizing.py          # risk-parity (ERC) position sizing + a cost/turnover guard
uv run python research/sector_neutral.py  # sector-neutralized + composite signal eval
```

---

## 6. The eval, and what it found

`eval.py` measures, per signal, over a point-in-time history:
- **Cross-sectional IC** (daily Spearman across names, averaged) — the desk-standard selection metric.
- **Newey-West (HAC) t-stat** — overlapping forward windows are autocorrelated; this corrects the naive SE.
- **Net long-short tercile spread** — `|gross| − round-trip cost`, so cost erodes the edge (even a reversion signal).
- **Multiple-testing deflation** — per-signal p-value + a Bonferroni floor; a lone `|t|>2` across trials is flagged.
- **Survivorship** flagged (universe = holdings ∪ `universe.csv`); **fundamentals announcement-lagged** (no look-ahead).

**The honest verdict (106 NSE names × 10y):** no simple signal has bankable edge. Everything that flickered
on 2 years collapsed to `t≈0` with real power; the only statistically-significant hit (RSI @ 5-day) had a
**negative** net spread — real, but smaller than costs. Sector-neutralization sharpened the t-stats (it's the
correct selection-IC method, worth productionizing) but still cleared nothing. **That negative result is the
point** — it's why this is a sense-making tool, not a forecaster, and why the discipline layer
(`research/sizing.py`) focuses on risk + cost, where a personal book actually compounds.

---

## 7. The dashboard (`web/`)

SvelteKit (Svelte 5) + TypeScript + Apache ECharts. It only ever talks to the API.

```bash
cd web && npm install
npm run dev        # http://localhost:5173 (proxies to the API at :8000)
npm run check      # svelte-check (keep it clean)
npm run build      # adapter-static → web/build (then FastAPI serves it)
```
Run the backend alongside: `uv run tradeos serve`. Pages: **Overview** (risk-vs-weight, sector donut,
correlation heatmap, cards) · **Analyst** workbench per symbol (every angle + verdict + **History**) ·
**Briefs** (`/runs`, the global brief feed) · **Newsroom** · **Reasoning Monitor** (live multi-agent run, SSE)
· **Briefing** · **Data Coverage** · **Manage** (add holdings, upload concalls).

---

## 8. Environment variables

All optional — sane defaults apply; set in `.env` or the shell.

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | `postgresql://tradeos:tradeos@localhost:5432/tradeos` | Postgres connection string |
| `ANTHROPIC_API_KEY` | *(unset)* | AI verdict · credibility · live news · RAG answers |
| `ANALYST_MODEL` | `claude-haiku-4-5` | the analyst verdict + credibility model (`claude-sonnet-4-6` for precision) |
| `NEWS_MODEL` | `claude-haiku-4-5` | the live web-search model |
| `NEWS_TTL_HOURS` | `24` | how long stored news is reused before a fresh search (`6` = intraday, `168` = a week) |
| `CLAUDE_MODEL` | `claude-opus-4-8` | the `analyze`/narration + guidance-extraction model |
| `HISTORY_PERIOD` | `2y` | daily price history to pull (`1y`/`2y`/`5y`/`10y`/`max`) |
| `BENCHMARK` | `^NSEI` | index for beta (NIFTY 50) |
| `COV_SHRINKAGE` | `1` | Ledoit-Wolf covariance shrinkage on/off |
| `SECTOR_CONCENTRATION_PCT` | `40.0` | macro: flag a sector above this % of the book |
| `ANNOUNCEMENT_LAG_DAYS` | `45` | days after quarter-end before results are "known" (point-in-time) |
| `COST_BPS` | `15` | per-leg transaction cost for the eval's net spread |
| `RAG_MAX_DISTANCE` | `0.45` | cosine-distance floor above which `ask` flags weak evidence |
| `PORTFOLIO_FILE` / `UNIVERSE_FILE` | `holdings.csv` / `universe.csv` | your book / the survivorship-free eval universe |
| `API_HOST` / `API_PORT` | `127.0.0.1` / `8000` | where `tradeos serve` binds |
| `READ_CACHE_TTL` | `300` | seconds the API caches factual reads (`0` disables) |
| `DB_POOL_MIN` / `DB_POOL_MAX` / `DB_POOL_TIMEOUT` | `1` / `10` / `5` | connection-pool sizing |

---

## 9. Point-in-time & honesty model (why outputs are trustworthy)

- **`--as-of`** filters every read to `date <= as_of`; fundamentals additionally require
  `period_end + ANNOUNCEMENT_LAG_DAYS <= as_of`. **Live web news is disabled for a historical `as_of`** —
  fetching today's news for a past date would be look-ahead.
- **`price_vintages`** is an append-only revision log: a replay reconstructs prices *as they were known* at
  a date, so a backtest isn't silently re-stated by a later yfinance re-adjustment.
- **Honest stats**: Newey-West HAC t-stats for overlapping windows; Bonferroni deflation; small universes
  labelled underpowered, not dressed up.
- **Provenance**: every output carries `as_of` + the features behind it; RAG answers cite chunks and flag
  weak retrieval; credibility cites the actual numbers and refuses to score vague guidance.

---

## 10. Development

```bash
uv run pytest -q                 # 132 tests; DB-backed integration tests skip cleanly without Postgres
uv run ruff check .              # lint (must stay clean)
uv run mypy src/tradeos          # type-check (must stay clean)
cd web && npm run check          # svelte-check (must stay clean)
```

CI runs all three on every push/PR. See `CLAUDE.md` for the contributor contract and `ROADMAP.md` for phases.

---

## 11. Security notes

- The API has **no auth** and binds to `127.0.0.1` by default — keep it local. Do **not** set
  `API_HOST=0.0.0.0` on an untrusted network: it exposes your book and the write/LLM/web-search routes spend money.
- Keep `holdings.csv` (and `data/`) out of version control — they hold your real positions and private documents.

---

## 12. Troubleshooting

- **`No price data found. Run tradeos ingest first.`** → `uv run tradeos ingest`.
- **`connection refused` / pool timeout** → Postgres isn't up. `brew services list`; check `DATABASE_URL`.
- **`no data` for a ticker** → wrong symbol/suffix. NSE needs `.NS`, BSE `.BO`.
- **AI verdict / live news does nothing** → `ANTHROPIC_API_KEY` not set (the deterministic detail still prints).
- **Credibility says "no guidance"** → ingest a **concall transcript** (not a results filing) and `tradeos extract`.
- **A brief mis-states a number** → Haiku tradeoff; re-run with `ANALYST_MODEL=claude-sonnet-4-6`.
- **RAG / `ask` errors about the `vector` type** → pgvector isn't enabled; re-run `psql -d tradeos -f db/init/01_init.sql`.
