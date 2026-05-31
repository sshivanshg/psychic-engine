-- TradeOS schema — plain PostgreSQL (runs on any Postgres 14+).
-- Apply to a native local Postgres:  psql -d tradeos -f db/init/01_init.sql
-- (Also auto-runs on first boot of the optional Docker DB in docker-compose.yml.)
--
-- Note: the original Phase 0 plan used a TimescaleDB *hypertable* to learn time-series
-- storage. A stock Postgres install doesn't ship the timescaledb extension, so we use a
-- plain table here — proper indexing covers our query patterns fine at this scale.
-- Promoting `prices` to a hypertable becomes a deliberate Phase 6 ("earn the infra") task.

CREATE TABLE IF NOT EXISTS prices (
    symbol      text        NOT NULL,
    date        date        NOT NULL,
    open        double precision,
    high        double precision,
    low         double precision,
    close       double precision,   -- split-adjusted price (levels, value, technicals)
    adj_close   double precision,   -- total-return (splits + dividends) → returns / risk
    volume      bigint,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, date)   -- makes ingestion idempotent (UPSERT on conflict)
);

CREATE INDEX IF NOT EXISTS idx_prices_symbol_date ON prices (symbol, date DESC);

-- Quarterly fundamentals (from yfinance income statement). Values are in the company's own
-- reporting currency (₹ for most NSE names, USD for ADR-listed ones like INFY) — so only RATIOS
-- (growth %, margins) are comparable across tickers, never absolute revenue.
CREATE TABLE IF NOT EXISTS fundamentals (
    symbol            text        NOT NULL,
    period_end        date        NOT NULL,   -- quarter end
    total_revenue     double precision,
    operating_income  double precision,
    net_income        double precision,
    gross_profit      double precision,
    ingested_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, period_end)
);

-- Per-security metadata (sector/industry/name) for the Macro agent's sector-exposure read.
-- yfinance .info is best-effort; columns stay nullable so a missing sector degrades gracefully.
CREATE TABLE IF NOT EXISTS security_meta (
    symbol      text PRIMARY KEY,
    sector      text,
    industry    text,
    name        text,
    ingested_at timestamptz NOT NULL DEFAULT now()
);

-- Phase 3b: RAG over concall/results documents. Needs the pgvector extension.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS doc_chunks (
    id          bigserial PRIMARY KEY,
    symbol      text        NOT NULL,
    source      text        NOT NULL,   -- file name the chunk came from
    chunk_index int         NOT NULL,
    content     text        NOT NULL,
    embedding   vector(384),            -- BAAI/bge-small-en-v1.5 dimension
    period      date,                   -- fiscal period (quarter end) the document covers, if known
    filing_date date,                   -- date the doc was filed / first public (point-in-time), if known
    source_url  text,                   -- provenance: where it was fetched from (NULL if added manually)
    ingested_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (symbol, source, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_doc_chunks_hnsw ON doc_chunks USING hnsw (embedding vector_cosine_ops);

-- Phase 3: structured guidance extracted from a holding's concall (schema-constrained, with the
-- verbatim source quotes). The Fundamental agent reads this so the (costly) LLM extraction is paid
-- once, not on every analyze. `data` holds the GuidanceExtract fields as JSON.
CREATE TABLE IF NOT EXISTS guidance (
    symbol       text  NOT NULL,
    source       text  NOT NULL,   -- the document the guidance was extracted from
    period       date,             -- quarter the guidance pertains to (the doc's period)
    data         jsonb NOT NULL,   -- revenue_outlook / margin_outlook / demand_commentary / quotes …
    extracted_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, source)
);

-- Backfill the provenance columns on databases created before they existed (idempotent). Provenance
-- is what lets `tradeos docs status` tell whether a holding's latest reported quarter has a
-- transcript, and lets the eval harness reason about point-in-time availability.
ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS period      date;
ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS filing_date date;
ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS source_url  text;

-- Per-article news sentiment for the Sentiment agent. Stored per-article (not aggregated) so a
-- point-in-time read can filter on `published`. CAVEAT: free news feeds give only a CURRENT snapshot
-- with shallow history, so sentiment is descriptive-of-now and is deliberately BARRED from the eval
-- harness (it is not reconstructable point-in-time). polarity ∈ [-1, 1] from a transparent lexicon.
CREATE TABLE IF NOT EXISTS sentiment (
    symbol      text        NOT NULL,
    title       text        NOT NULL,
    publisher   text,
    published   timestamptz,                 -- article time, if the source provides it
    polarity    double precision NOT NULL,   -- lexicon score in [-1, 1]
    ingested_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, title)
);
CREATE INDEX IF NOT EXISTS idx_sentiment_symbol ON sentiment (symbol, published DESC);

-- Per-holding ownership snapshot for the Ownership agent (institutional / insider holding). yfinance
-- exposes only a CURRENT snapshot (no history), so like sentiment this is descriptive-of-now and is
-- BARRED from the eval harness. Nullable so a missing field degrades to "no data".
CREATE TABLE IF NOT EXISTS ownership (
    symbol                text PRIMARY KEY,
    held_pct_institutions double precision,   -- fraction (0-1) held by institutions
    held_pct_insiders     double precision,   -- fraction (0-1) held by insiders/promoters (proxy)
    n_institutions        integer,
    snapshot_at           timestamptz,        -- when the snapshot was taken (the only "as-of" we have)
    ingested_at           timestamptz NOT NULL DEFAULT now()
);

-- Run snapshots for the "what-changed" delta: each `analyze` persists a compact per-stock payload so
-- the next run can diff dials / score / risk against the prior run. `as_of` is the analysis date;
-- `run_at` is wall-clock so we can always find the immediately-prior run.
CREATE TABLE IF NOT EXISTS run_snapshots (
    id      bigserial PRIMARY KEY,
    symbol  text        NOT NULL,
    as_of   date,
    run_at  timestamptz NOT NULL DEFAULT now(),
    payload jsonb       NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_run_snapshots_symbol_runat ON run_snapshots (symbol, run_at DESC);
