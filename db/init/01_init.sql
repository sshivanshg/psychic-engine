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
