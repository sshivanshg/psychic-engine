# TradeOS — Personal Portfolio Intelligence
### A learning-first build roadmap

> **What it is:** a scheduled, multi-agent system that analyzes *your own* holdings across
> several dimensions (risk, technical, fundamentals/earnings, macro, sentiment, ownership),
> synthesizes a per-stock view — dials + a score + a reasoning trace — and tells you *what
> changed*. You interrogate it; you make the call. It runs on free public data and you use it
> for your real investing.
>
> **Why this shape:** it satisfies all four of your goals at once —
> *genuinely useful to you*, *learn agentic LLM systems*, *learn specific infra*, *hireable showcase* —
> as long as you build it in the order below and **ship every phase before starting the next**.

---

## The non-negotiable rules (read these first)

1. **Each phase must be *usable* before you move on.** No "I'll wire it up later." A half-built
   phase 2 on top of a half-built phase 1 is how this project dies in a drawer. Working slice > big plan.
2. **It informs you, it does not command you.** You're putting real money behind this. The score
   and reasoning trace are *inputs you interrogate*, never a verdict you obey. The prettier the
   reasoning trace, the more skeptical you should be — an LLM will always write you a confident story.
3. **The eval harness is not optional and not last.** Before you trust any signal with capital,
   you measure whether it predicts anything. This is also your single strongest hireable artifact.
4. **Simple stack first. Fancy infra is a *deliberate phase 2*, not day one.** Your use case
   (positional investing, runs daily) does not need Kafka/Rust/K8s. You'll add them *to learn them*,
   and "I started simple then re-architected for scale" is a stronger story than cargo-culting Kafka up front.
5. **Build the framework yourself before you adopt one.** Write your own tiny agent loop / orchestration
   graph first — *then* optionally swap in LangGraph. You learn 10x more, and you'll actually understand
   what the framework does for you.

---

## Why this order (design decisions)

### Q: Why not just use Rust and Kafka from the start?

Short version: **you don't have the problem they solve yet — and you cannot truly learn an infra
tool without the problem it solves.** Since "learn everything myself" is your whole purpose,
starting with Kafka/Rust actively works *against* you. The longer reasons:

1. **Your use case doesn't need them.** Kafka and Rust exist for *high-throughput, low-latency
   streaming*. You have neither: ~10–20 stocks, a handful of data pulls a day, and decisions you
   hold for days or weeks. That's a scheduled batch job. Kafka here is a jet engine on a bicycle —
   weeks of fighting infra for a problem you don't have.

2. **You can't learn Kafka without a Kafka-sized problem.** Bolt it on day one and you'll wire one
   producer → one consumer, one topic, and stop. You'll have learned Kafka's "hello world" and
   *none* of why it exists — partitions, consumer groups, rebalancing, backpressure, offsets,
   exactly-once. Those concepts only click when real throughput pain forces them on you.
   **Learning an infra tool *is* learning the problem it solves.** No problem → you cargo-cult the
   tool and learn nothing deep.

3. **The earn-it path teaches it for real.** Build the batch version → feel a genuine limitation
   (ingestion too slow, or you decide you want live intraday) → *then* migrate to Kafka/Rust. Now
   every concept maps to a pain you personally felt. That sticks for life. It's the *fastest* route
   to actually knowing the tool — which is exactly what you said you want.

4. **Infra-first builds the half-finished monster.** The brain of this project is the agents + the
   eval harness. Spend month one on a Rust ingester and a Kafka cluster and you'll have beautiful
   plumbing, zero intelligence, and the exact over-scoped skeleton that dies in a drawer. Get the
   brain working on a boring stack; upgrade the plumbing once it's worth upgrading.

5. **You'd be deciding before you know anything.** You don't yet know your data shapes, access
   patterns, or which agents survive the backtest. A streaming architecture locks in choices you'll
   want to undo. A simple stack is cheap to change while you're still discovering what the system
   needs to be.

6. **"I re-architected it" beats "I started with Kafka."** Senior engineers hire for *judgment* —
   knowing *when* a tool is warranted. "I had a batch pipeline, hit this limit, migrated to
   streaming, here's the before/after" shows that. "I used Kafka because it's impressive" shows the
   opposite.

*The one exception:* if your **only** goal were a Kafka/Rust showcase, you could start there. But
you want to learn deeply *and* run it on real money — for both, earn-the-tool wins.

### Q: Why the Risk agent first, not the exciting sentiment/technical ones?
Risk is **computable and verifiable** (concentration, vol, beta, drawdown are math). You can check
it's correct, so you learn the agent pattern on solid ground. Sentiment / chart-pattern signals are
noisy and you can't easily tell right from wrong — terrible for *learning the mechanics*, and (per
Phase 4) they may not even have edge.

### Q: Why build my own agent loop before using LangGraph?
A framework hides the exact thing you're trying to learn. Write ~100 lines of "LLM + tools + loop"
yourself and you'll understand state, handoffs, and control flow from the inside. *Then* a framework
becomes a labor-saver you can evaluate — not a black box you depend on.

### Q: Why is the eval harness so early when it's the boring part?
Two reasons: it's your real money, so you must know the signals work before trusting them; and an
honest backtest is the rarest, most impressive thing in a project like this. Skipping it is how
people build confident systems that quietly lose money.

---

## Target architecture (the end state you're building toward)

```
            ┌──────────────────────────────────────────────┐
            │                 DATA SOURCES                  │
            │  prices (OHLCV) · fundamentals · filings/     │
            │  concalls · news · FII/DII · options (later)  │
            └───────────────────────┬──────────────────────┘
                                     │  ingestion (Python → later Kafka/Rust)
                                     ▼
            ┌──────────────────────────────────────────────┐
            │   STORAGE: Postgres + TimescaleDB (series)    │
            │            + pgvector (filings/news)          │
            └───────────────────────┬──────────────────────┘
                                     │  point-in-time reads
        ┌──────────┬──────────┬─────┴─────┬──────────┬──────────┐
        ▼          ▼          ▼           ▼          ▼          ▼
     RISK     TECHNICAL   FUNDAMENTAL    MACRO    SENTIMENT   OWNERSHIP
     agent      agent     /earnings      agent      agent      /flow
                            agent                              agent
        └──────────┴──────────┴─────┬─────┴──────────┴──────────┘
                                     ▼
                        ┌────────────────────────┐
                        │      ORCHESTRATOR       │  ← synthesizes per-stock
                        │  dials + score + trace  │     card; you interrogate it
                        └────────────┬───────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              ▼                      ▼                      ▼
        EVAL/BACKTEST           FRONTEND               ALERTS
        (does it work?)      (daily briefing)      (your rules → Telegram)
```

**Stack (phase 1):** Python · Postgres+TimescaleDB · pgvector · Claude API (Anthropic SDK) · FastAPI · Docker Compose
**Stack (phase 2, for learning):** Kafka · Rust (hot path) · Kubernetes+HPA · MLflow · Feast · CI/CD

---

## The phases

Estimates assume part-time (~10–15 hrs/week). Adjust to your pace; the *order* matters more than the dates.

---

### Phase 0 — Foundations & first data (≈1 week)

**Goal:** a clean repo and your real portfolio's daily prices sitting in your own database.

**What you'll learn**
- Modern Python project hygiene: `uv` (or Poetry), `ruff`, typed code, project layout
- Docker Compose (spin up Postgres+Timescale locally with one command)
- TimescaleDB basics: hypertables, why time-series storage differs from plain Postgres
- Picking & pulling free Indian-market data (NSE bhavcopy, `yfinance`, `nsepython`)

**What you build**
- Repo scaffold + `docker-compose.yml` (Postgres/Timescale)
- An ingestion script that pulls ~2 years of daily OHLCV for *your actual holdings* into a hypertable

**Concepts to master:** relational schema design, time-series modeling, idempotent ingestion (re-runnable without dupes)

**Resources:** TimescaleDB docs (hypertables); `uv` docs; `nsepython` / `jugaad-data` repos

**✅ Done when:** you can run one command to bring up the DB, one to ingest, and SQL-query a year of your portfolio's prices.

---

### Phase 1 — Your first agent + the eval mindset (≈1–2 weeks)

**Goal:** the **Risk Agent** — the most *real* and *computable* dimension — working end to end.

> Why Risk first: it's math, not vibes (concentration, volatility, drawdown, beta, position
> sizing). You can *verify* it's correct, which teaches you the agent pattern on solid ground
> before you touch noisy signals like sentiment.

**What you'll learn**
- What an "agent" actually *is*: an LLM + tools + a loop (build this yourself, ~100 lines, no framework)
- **Structured outputs** with Pydantic + `instructor` — forcing the model to return validated JSON
- Tool/function calling; prompt design; system vs user roles
- **Prompt caching** (Anthropic) to cut cost on repeated context
- The eval *mindset*: point-in-time data access (never read tomorrow's price to score today)

**What you build**
- Pure-Python risk computations first (no LLM): concentration %, portfolio vol, per-position beta, max drawdown, correlation matrix
- *Then* an LLM layer that turns those numbers into a plain-English risk report ("you're 34% in banking; vol is up 20% MoM")
- A skeleton eval harness: a "give me the data as of date D" function with **no look-ahead**

**Concepts to master:** separation of *computation* (deterministic, testable) from *narration* (LLM); why you compute facts and only *explain* with the LLM

**Resources:** Anthropic "Building effective agents" guide; `instructor` docs; the `claude-api` skill in this CLI (prompt caching patterns)

**✅ Done when:** you run it on your portfolio and get a correct, numbers-backed risk report with a readable explanation.

---

### Phase 2 — The agentic core: multiple agents + orchestration (≈2–3 weeks)

**Goal:** several analyzer agents + an orchestrator that produces a per-stock card. *This is the "useful tool" milestone and the big agentic-LLM payoff.*

**What you'll learn**
- **Multi-agent orchestration**: build your own simple graph/DAG of agents first; understand state passing, fan-out/fan-in, handoffs
- Designing a **reasoning trace** (capture *why*, with the evidence each agent contributed)
- **Observability**: wire in Langfuse (or LangSmith) — trace every agent call, token, latency, cost
- Cost/latency management across many LLM calls; when to parallelize agents
- *(Optional)* swap your hand-built graph for **LangGraph** and feel the difference

**What you build**
- Add **Technical** (computable indicators: RSI, moving averages, 52-wk position — facts, not predictions), **Fundamental/Earnings** (extraction — leans on Phase 3), and **Macro** (FII/DII + sector exposure) agents
- The **Orchestrator**: gathers all agents' outputs → per-stock card = per-dimension dials + a synthesized score + a reasoning trace
- Run agents **in parallel** where independent

**Concepts to master:** orchestration patterns (you'll recognize these from the workflow tooling — fan-out, barrier, pipeline); idempotent + cacheable agent calls; keeping the human in the loop in the UX

**Resources:** LangGraph docs; Langfuse docs; Anthropic multi-agent patterns

**✅ Done when:** you add holdings → get a synthesized card per stock (dials + score + trace) you'd actually look at before a decision.

---

### Phase 3 — RAG + document intelligence (≈1–2 weeks)

**Goal:** turn earnings filings & concall transcripts into something queryable; feed the Fundamental agent real guidance/earnings data.

**What you'll learn**
- Embeddings + **vector search with pgvector** (no separate vector DB needed)
- Chunking strategies for long financial docs; metadata filtering
- **RAG with citations** (answers must quote the source — no hallucinated numbers)
- Evaluating RAG: *groundedness* / faithfulness checks
- Structured **extraction** from messy PDFs → clean financial fields (with an accuracy eval set)

**What you build**
- Ingest concalls/filings for your holdings → embed → pgvector
- "Ask the call" — natural-language Q&A over a holding's latest concall, with cited quotes
- Guidance/earnings extraction that feeds Phase 2's Fundamental agent

**Concepts to master:** retrieval quality vs generation quality (debug them separately); a golden eval set for extraction accuracy (one wrong PAT number kills trust)

**Resources:** pgvector docs; Anthropic contextual-retrieval write-up; `instructor` for schema-constrained extraction

**✅ Done when:** you can ask any holding "what did management say about margins?" and get a cited, accurate answer.

---

### Phase 4 — The honest eval harness (≈2 weeks) — **the differentiator**

**Goal:** find out, honestly, whether your signals predict anything *before* you bet on them.

> This is the hardest *interesting* problem in the project and the artifact that most impresses a
> good engineer. Most "AI trading" projects skip it and quietly assume it works. Yours won't.

**What you'll learn**
- **Backtesting methodology**: walk-forward, out-of-sample, train/validate/test discipline
- **Look-ahead bias** — including the sneaky LLM kind: the model's *training data already knows*
  the outcome of past events. How to reason about and mitigate this.
- **Point-in-time data**: reconstructing what fundamentals/news looked like *that day*, not today
- Realistic metrics: hit rate, information coefficient (IC), Sharpe, turnover, and **transaction costs**
- Why "it worked on the last 6 months" is not validation

**What you build**
- A replay engine: step through history, generate each agent's score using only point-in-time data, record it
- Measure correlation of scores → forward returns; produce an honest report card per agent
  ("Risk dial: meaningful. Sentiment score: noise. Earnings-surprise: weak positive edge.")

**Concepts to master:** the difference between a backtest that lies to you and one that doesn't; sizing your confidence to the evidence

**Resources:** "Advances in Financial Machine Learning" (López de Prado) — at least the backtesting/leakage chapters; `vectorbt` for mechanics

**✅ Done when:** you have a report that tells you which agents actually have edge — and you weight them accordingly in the orchestrator.

---

### Phase 5 — Frontend + daily use (≈1–2 weeks)

**Goal:** you actually *use* it. A dashboard + a scheduled morning briefing.

**What you'll learn**
- **FastAPI** (clean API design over your engine)
- **SvelteKit** frontend (or start with Streamlit for speed, upgrade later) — components, data fetching, charts
- Scheduling: APScheduler/cron now (Airflow later if you want the skill)
- A **Telegram bot** for alerts on *your* rules ("ping me if any holding guides down on margins")

**What you build**
- Dashboard: portfolio overview → per-stock card → drill into the reasoning trace and cited evidence
- A scheduled daily run that produces a pre-market briefing and pushes alerts

**✅ Done when:** every morning before open, you get a portfolio briefing you trust enough to read.

---

### Phase 6 — The deliberate infra layer (ongoing / optional) — **resume scale**

**Goal:** learn the heavy infra by *re-architecting what already works*. Each sub-goal is its own
mini-project with a clean before/after story.

| Learn | Re-architect | The story it tells |
|---|---|---|
| **Kafka / streaming** | Move ingestion onto event streams | batch → streaming pipeline design |
| **Rust** | Rewrite the hot ingestion/compute path | systems perf; FFI to Python |
| **Kubernetes + HPA** | Containerize + autoscale agent workers | scale up in market hours, down at night |
| **MLflow** | Version your eval models / scoring configs | reproducible ML lifecycle |
| **Feast** | Shared feature store across agents | feature reuse, online/offline parity |
| **CI/CD** | GitHub Actions: test + deploy | production engineering discipline |

> Do these because you *want the skill*, not because the app needs them. Pick the 2–3 you care
> about most; you don't need all six.

**Resources:** "Designing Data-Intensive Applications" (Kleppmann); "Kafka: The Definitive Guide"; the Rust Book; Kubernetes docs

---

## Cross-cutting practices (do these every phase)

- **Tests for the deterministic parts** (risk math, indicators, extraction). LLM narration you eval, math you unit-test.
- **Eval before trust** — any new signal goes through Phase 4's harness before it influences a real decision.
- **Cost log** — track LLM spend per run; use prompt caching; you'll be glad you watched it early.
- **Write a short note per phase** ("what I learned / what I'd do differently"). This becomes your
  portfolio writeup and your interview talking points.
- **Use the latest Claude models** for the agents; structure prompts for caching from day one.

---

## What this becomes (the hireable narrative)

By the end you can say, truthfully:

> *"I built a multi-agent equity-analysis system I run on my own portfolio. Six analyzer agents
> feed an orchestrator that produces a scored, fully-traced view of each holding. I built the
> orchestration from scratch before adopting a framework, the document intelligence on RAG over
> filings and concalls, and — the part most people skip — a point-in-time backtest harness that
> honestly measures whether each signal has predictive edge, accounting for look-ahead bias and
> transaction costs. Then I re-architected the ingestion onto Kafka and Rust and deployed it on
> Kubernetes."*

That sentence is worth more than any single buzzword on a CV, because it shows **judgment, honesty,
and end-to-end ownership** — the things that actually get you hired.

---

## Appendix — tooling to install yourself, by phase

Listed so you know what's coming. Install each **when you reach the phase** — don't front-load.

- **Phase 0:** Docker + Docker Compose · Python 3.12+ · `uv` · a Postgres client (psql / TablePlus /
  DBeaver). *(You already have Docker, Python 3.14, and `uv`.)* Libs: `psycopg` or SQLAlchemy, plus a
  data source (`yfinance` / `nsepython` / `jugaad-data`).
- **Phase 1:** Anthropic SDK + an API key · `pydantic` · `instructor` · `numpy`/`pandas` for risk math.
- **Phase 2:** Langfuse (self-host via Docker, or cloud) · *(optional)* LangGraph.
- **Phase 3:** the `pgvector` extension (Docker image `pgvector/pgvector`) · an embeddings provider ·
  a PDF parser (`pymupdf`).
- **Phase 4:** `vectorbt` (or pure pandas) · Jupyter.
- **Phase 5:** `fastapi` + `uvicorn` · Node + SvelteKit (or Streamlit to start) · a Telegram bot
  token (from BotFather).
- **Phase 6 (optional):** Rust (`rustup`) · Kafka (Docker / Redpanda) · local K8s (kind / minikube /
  k3d) · MLflow · Feast.

---

## How to use me (self-study mode)

You're driving — install and write everything yourself; that's the entire point. I'm your **on-call
tutor, not your code generator.** Good ways to pull me in:

- *"Explain **why** X works this way"* — before or after you build it
- *"Review what I wrote for Phase N"* — paste your code, I critique it (I won't rewrite it for you)
- *"I'm stuck on this error / concept"* — I unblock your understanding without doing it for you
- *"Quiz me on Phase N"* — test yourself before moving on

**Next step:** start **Phase 0**. Open a terminal, not me. 🙂
