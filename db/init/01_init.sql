-- Runs automatically the first time the DB container initializes (empty data volume).
-- If you change this file later, you must reset the volume to re-run it:
--   docker compose down -v && docker compose up -d

-- 1. Turn on TimescaleDB (the image has it installed; we just enable it in this DB).
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- 2. A plain relational table for daily price candles.
--    PRIMARY KEY (symbol, date) is what makes ingestion idempotent: re-ingesting the
--    same day for the same symbol updates the row instead of creating a duplicate.
--    NOTE: a hypertable's unique key MUST include the partitioning column (`date`).
CREATE TABLE IF NOT EXISTS prices (
    symbol      text        NOT NULL,
    date        date        NOT NULL,
    open        double precision,
    high        double precision,
    low         double precision,
    close       double precision,
    volume      bigint,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, date)
);

-- 3. Promote it to a TimescaleDB hypertable, partitioned by time (`date`).
--    A hypertable looks/queries exactly like a normal table, but Timescale splits it
--    into time-based "chunks" under the hood — the thing you're here to learn.
--    (Modern equivalent: create_hypertable('prices', by_range('date'), ...);)
SELECT create_hypertable('prices', 'date', if_not_exists => TRUE);

-- 4. A helper index for "give me one symbol's history, newest first" queries.
CREATE INDEX IF NOT EXISTS idx_prices_symbol_date ON prices (symbol, date DESC);
