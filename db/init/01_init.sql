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

-- Backfill the provenance columns on databases created before they existed (idempotent). Provenance
-- is what lets `tradeos docs status` tell whether a holding's latest reported quarter has a
-- transcript, and lets the eval harness reason about point-in-time availability.
ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS period      date;
ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS filing_date date;
ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS source_url  text;
