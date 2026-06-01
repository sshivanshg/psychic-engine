# TradeOS — how the flow works

A scheduled, multi-agent **equity-analysis** system on a real portfolio. It is **descriptive, never
prescriptive** (explains risk/technicals/fundamentals; never says buy/sell/hold — SEBI), every number
is **point-in-time and reproducible**, and the **LLM layer is optional** (everything factual works with
no API key).

These diagrams render on GitHub and in any Mermaid-aware Markdown viewer.

**The three layers, and the rule that separates them:**
| Layer | Modules | Network? | LLM? | Determinism |
|---|---|---|---|---|
| **Ingestion** | `ingest.py` `sources.py` `news.py` `docs.py` `extraction.py` | ✅ the *only* network layer | only `news`/web-search | side-effectful |
| **Engine core** | `context.py` `agents.py` `risk/technical/fundamental/macro/sentiment/ownership` `scoring.py` `snapshots.py` `orchestrator.py` `analyst.py` `eval.py` `briefing.py` | ❌ never | ❌ never computes | **pure** (no `datetime.now()`, no hidden state) |
| **LLM narration** | `StockCard` synth · `verdict` · `deep_analysis` · `ask_research` · `credibility` · `docs.ask` | via Anthropic | ✅ | optional / degradable |

The **API/SPA are READ layers** — no quant logic, no per-agent DB queries; they just serve the engine's reads.

---

## 1 · System overview

```mermaid
flowchart TB
    subgraph SRC["External sources — free data only"]
        YF["yfinance<br/>prices · fundamentals · ownership · sector"]
        WEB["Anthropic web_search<br/>live news + follow-ups"]
        PDF["uploaded PDFs<br/>concalls / results"]
    end

    subgraph DRV["Drivers"]
        CLI["CLI — tradeos *<br/>ingest · analyze · analyst · briefing · eval · docs"]
        SPA["SvelteKit SPA (web/)"]
    end

    subgraph ING["Ingestion — the ONLY network layer"]
        I1["ingest.py · sources.py<br/>(+ append-only price_vintages)"]
        I2["news.py<br/>web_search → sentiment"]
        I3["docs.py<br/>parse → chunk → embed (local)"]
        I4["extraction.py<br/>guidance from concalls"]
    end

    DB[("Postgres + pgvector")]

    subgraph ENG["Engine core — PURE"]
        CTX["context.py · AnalysisContext.build<br/>ONE point-in-time data load"]
        REG["agents.py REGISTRY — 6 analyzers<br/>risk · technical · fundamental · macro · sentiment · ownership"]
        SC["scoring.py — attention + confidence<br/>snapshots.py — what-changed delta"]
        ORCH["orchestrator.py · analyze()<br/>per-stock cards, ranked by risk"]
        AN["analyst.py · assemble_facts<br/>single-name fact base"]
        EV["eval.py — PIT backtest"]
        BR["briefing.py — alert rules"]
    end

    subgraph LLMX["LLM layer — optional / degradable"]
        L1["StockCard synthesis"]
        L2["verdict — 1 Haiku call"]
        L3["deep_analysis — bull·bear·sector → judge"]
        L4["ask_research / docs.ask + web_search"]
        L5["credibility"]
    end

    CACHE["cache.py — read-layer TTL memo"]
    API["api.py — FastAPI read layer<br/>+ thin write seam + SSE stream"]

    YF --> I1 --> DB
    WEB --> I2 --> DB
    PDF --> I3 --> DB
    I3 --> I4 --> DB
    CLI --> I1 & ORCH & AN & EV & BR

    DB --> CTX --> REG --> SC --> ORCH
    CTX --> AN
    ORCH --> L1
    AN --> L2 & L3 & L4 & L5
    DB --> EV & BR

    ORCH --> API
    AN --> API
    EV --> API
    BR --> API
    CACHE -.memoize.- API
    API --> SPA
    SPA -.writes: holdings · docs · ingest.-> API
```

---

## 2 · Data layer (who writes, who reads)

```mermaid
flowchart LR
    ingest["ingest.py"] --> prices[("prices")] & pv[("price_vintages")] & fundamentals[("fundamentals")] & ownership[("ownership")] & meta[("security_meta")]
    news["news.py"] --> sentiment[("sentiment")]
    docs["docs.py"] --> chunks[("doc_chunks + vector")]
    extraction["extraction.py"] --> guidance[("guidance")]
    orchestrator["orchestrator.py"] --> snaps[("run_snapshots")]
    analyst["analyst.py"] --> runs[("analyst_runs")]

    prices & fundamentals & guidance & sentiment & ownership & meta --> CTX["AnalysisContext (PIT)"]
    chunks --> RAG["docs.search / ask"]
    snaps --> DELTA["what-changed delta"]
    runs --> HIST["Workbench · History tab"]
    pv --> REPLAY["eval vintage_asof replay"]
```

All reads are **point-in-time**: only data with availability `<= as_of` is used (fundamentals apply an
announcement lag; ownership/sentiment are PIT-gated on their snapshot date; price vintages reconstruct
"as known at" a date for reproducible backtests).

---

## 3 · Portfolio analysis pipeline — `orchestrator.analyze()`

```mermaid
flowchart TB
    A["analyze(as_of, horizon, narrate, snapshot, on_event?)"] --> B["AnalysisContext.build()<br/>ONE PIT load shared by all agents"]
    B --> C{{"for agent in REGISTRY"}}
    C --> R[risk] & T[technical] & F[fundamental] & M[macro] & S[sentiment] & O[ownership]
    R & T & F & M & S & O --> G["_build_card per symbol<br/>compute_attention (6 dims) + compute_confidence"]
    G --> H["sort by risk-contribution %"]
    H --> SN{"snapshot?"}
    SN -- yes --> J["annotate_deltas + save_run → run_snapshots"]
    SN -- no --> K
    J --> K{"narrate?"}
    K -- "key set" --> L["narrate_cards → StockCard (parallel, ThreadPool)"]
    K -- "no key" --> N["degrade: deterministic agent reads only"]
    L --> Z["result = cards + narratives + risk/sector overviews"]
    N --> Z
```

> `on_event` is an **optional observer** (default `None` ⇒ byte-identical run). When wired, it emits every
> stage to the live Reasoning Monitor — it only *watches*, it never changes a number. (See §6.)

---

## 4 · Single-name analyst — `analyst.py`

`assemble_facts` is the **free, deterministic** base. On top of it sit three optional LLM reads of
increasing depth, all descriptive and all degradable.

```mermaid
flowchart TB
    ENTRY["CLI: tradeos analyst SYM [--deep]<br/>API: /api/analyst/{sym}, …/deep, /analyst/ask"] --> AF

    AF["assemble_facts(sym)<br/>6 analyzers + 6-quarter trend + raw headlines + catalyst tags — FREE"]

    AF --> V["verdict() — QUICK<br/>1 Haiku call → one-liner + ≤3 bull/bear/watch (~$0.003)"]
    AF --> D["deep_analysis() — DEEP (see §4.1)"]
    AF --> Q["ask_research() — FOLLOW-UP (see §5)"]

    V --> SV["save → analyst_runs"]
    D --> SV
```

### 4.1 · `deep_analysis` — the multi-agent read (bull · bear · sector → judge)

```mermaid
flowchart TB
    Q0["GET /api/analyst/{sym}/deep"] --> DA["deep_analysis(sym, as_of, horizon)"]
    DA --> NW{"live read &amp; as_of is None?"}
    NW -- yes --> RN["refresh_news()<br/>web_search, cached by NEWS_TTL"]
    NW -- "no (historical ⇒ look-ahead bar)" --> AF2
    RN --> AF2["assemble_facts — FREE"]
    AF2 --> CR["assess_credibility()<br/>free DB read; LLM only if guidance exists"]
    CR --> KEY{"ANTHROPIC_API_KEY set?"}
    KEY -- no --> DEG["return facts · deep=None<br/>UI shows a deterministic dial read"]
    KEY -- yes --> DG["_digest(facts) — compact, few-hundred-token brief"]

    DG --> PAR["3 specialists IN PARALLEL · DEEP_MODEL = Sonnet"]
    PAR --> BULL["🐂 bull → SideCase (≤5 cited points)"]
    PAR --> BEAR["🐻 bear → SideCase (≤5 cited points)"]
    PAR --> SEC["🏭 sector → SectorRead (backdrop · fit · sensitivity)"]

    BULL & BEAR & SEC --> JUDGE["⚖️ judge → DeepAnalysis<br/>thesis · whats_right/wrong · sector_context<br/>· descriptive scenarios · what_to_watch · bottom_line"]
    JUDGE --> SAVE["_save_deep_run → analyst_runs (History tab)"]
    SAVE --> OUT["{ deep, debate{bull,bear,sector}, usage, cost_usd }"]
    OUT --> UI["Analysis tab renders the read + the raw agent debate"]
```

Every prompt carries the bright-line clause: **descriptive only, no buy/sell/targets, cite a provided
number, never invent one.** "How it should perform" is rendered as **conditional scenarios**
("IF x and y, THEN the setup descriptively implies z") — never a prediction. The sector agent is told it
has **no live sector-index/peer feed**, so it labels sector views as context, not data.

---

## 5 · Ask the analyst — `ask_research` (whole research + live web)

Distinct from the Documents-tab "Ask the filings" (`docs.ask`, which sees **only** ingested filings).

```mermaid
sequenceDiagram
    participant UI as SPA · "Ask the analyst"
    participant API as POST /api/analyst/ask
    participant AR as analyst.ask_research
    participant DB as Postgres + pgvector
    participant LLM as Claude (CLAUDE_MODEL)

    UI->>API: {symbol, question, web}
    API->>AR: ask_research(...)
    AR->>DB: assemble_facts (memoized) + latest persisted deep summary
    AR->>DB: docs.search → top-k filing chunks (RAG)
    alt no API key
        AR-->>UI: retrieved excerpts + note (degrade, never fabricate)
    else key set
        Note over AR,LLM: digest + deep + excerpts + question<br/>(+ web_search tool only if live & as_of is None)
        AR->>LLM: ask
        LLM-->>AR: answer (+ web_search results)
        AR->>AR: validate [n] citations · scrape web sources
        AR-->>UI: {answer, citations, hits, web_used, web_sources}
    end
```

---

## 6 · Live Reasoning Monitor — `/live` over SSE

```mermaid
sequenceDiagram
    participant UI as /live (EventSource)
    participant API as GET /api/stream/analyze
    participant WK as worker thread
    participant ORCH as analyze(on_event=…)

    UI->>API: open SSE
    API->>WK: spawn (uncached, snapshot=false)
    WK->>ORCH: run fresh
    ORCH-->>API: run_start
    ORCH-->>API: context_loaded (provenance)
    loop each of 6 agents
        ORCH-->>API: agent_start → agent_done (+ read, metrics, latency)
    end
    ORCH-->>API: ranking → card × N
    ORCH-->>API: narration_start → narration_done × N
    ORCH-->>API: run_complete
    API-->>UI: data: {event}  (paced by STREAM_PACING_MS)
    API-->>UI: event: end
```

---

## 7 · RAG document intelligence — `docs.py`

```mermaid
flowchart LR
    U["upload PDF / txt / md"] --> P["parse_document"] --> C["chunk_text<br/>word-boundary windows"] --> E["embed — fastembed bge-small, LOCAL (no API)"] --> S[("doc_chunks + pgvector")]
    QQ["question"] --> EQ["embed_query"] --> SR["cosine search · <=>"]
    S --> SR
    SR --> FL{"clears RAG_MAX_DISTANCE?"}
    FL -- no --> WK["flag weak_evidence (low confidence)"]
    FL -- yes --> AN["CitedAnswer (CLAUDE_MODEL)<br/>inline [n], invalid citations dropped"]
```

Retrieval is fully offline (local embeddings + pgvector); only the final synthesis needs a key, and it
**cites or refuses** — never invents.

---

## 8 · Honest signal evaluation — `eval.py`

```mermaid
flowchart LR
    U["survivorship-free universe.csv<br/>(holdings ∪ sold/delisted)"] --> SIG["build PIT signals<br/>(returns + announcement-lagged fundamentals)"]
    SIG --> IC["cross-sectional IC per step"]
    IC --> NW["Newey-West HAC t-stat<br/>(overlapping windows)"]
    NW --> DEF["multiple-testing deflation<br/>p-value + Bonferroni floor"]
    DEF --> NET["tercile spread NET of COST_BPS<br/>|gross| − cost"]
    NET --> OUT["IC · ICIR · hit-rate · significance flags<br/>(every caveat printed)"]
```

> A signal has **no edge until proven** out-of-sample, net of costs. Current honest caveat: the
> cross-section is small/underpowered and the universe lacks genuinely delisted names — the machinery is
> right; breadth is the binding constraint.

---

## 9 · Request → screen map (the SPA)

| Screen (route) | Primary endpoint(s) | Shows |
|---|---|---|
| Overview `/` | `GET /api/portfolio` `GET /api/risk` | KPI tiles · risk-vs-weight bars · sector donut · correlation heatmap · cards |
| Briefing `/briefing` | `GET /api/briefing` | alert-rule flags; each name links to the Workbench |
| **Analyst Workbench** `/analyst/[symbol]` | `GET /api/analyst/{sym}` · `…/deep` · `POST /api/analyst/ask` · `…/series` · `…/news` · `…/docs` · `…/history` | every fetched detail; **Analysis** tab = the deep read + agent debate + **Ask the analyst**; Documents = **Ask the filings** |
| Reasoning Monitor `/live` | `GET /api/stream/analyze` (SSE) | the 6 agents running live + streamed synthesis |
| Newsroom `/news` | `GET /api/news` | all fetched headlines, filterable |
| Coverage `/coverage` | `GET /api/coverage` | per-name data presence + freshness (blind-spot map) |
| Manage `/manage` | `POST /api/holdings` · `DELETE …` · `POST /api/docs` · `POST /api/ingest` | the thin write seam (each calls the same fn the CLI uses, then clears the cache) |

---

## 10 · The guardrails the whole flow obeys

1. **Descriptive, never prescriptive** — no buy/sell/hold, no price targets (SEBI). Enforced in every prompt.
2. **Point-in-time integrity** — only data `<= as_of`; live web/news fire **only for a live read** (`as_of is None`).
3. **Determinism** — the quant core is pure (no network, no clock, no hidden state). Ingestion is the only network layer; LLM narration is separate and optional.
4. **Provenance & honest gaps** — every number traces to its inputs + `as_of`; missing data ⇒ `None` / "no data" / low confidence, never fabricated. RAG answers cite or refuse.
5. **Costs aren't optional in eval**; **no edge until proven** out-of-sample.

---

### Model knobs (env)
- `CLAUDE_MODEL` — the strong model for synthesis · `docs.ask` · `ask_research` (default `claude-opus-4-8`).
- `DEEP_MODEL` — the deep multi-agent read (default `claude-sonnet-4-6`).
- `ANALYST_MODEL` — the cheap quick `verdict` + credibility (default `claude-haiku-4-5`).
- `NEWS_MODEL` — live news web-search (default `claude-haiku-4-5`).
