# TradeOS — Phase 0: Data Foundation

The smallest useful vertical slice: **one command brings up a TimescaleDB database, one
command pulls ~2 years of daily prices for your holdings into it, and one command verifies
the data is there.** No LLMs yet — Phase 0 is pure data plumbing, on purpose. You can't
analyze data you can't reliably store and query.

> This scaffold was built *for* you, but it's written to be **read**. Skim every file —
> the comments explain the *why*. Owning this code is the point.

---

## What you'll learn here
- Modern Python project layout + tooling with **`uv`** (env, lockfile, scripts)
- Running **Postgres + TimescaleDB** locally with **Docker Compose**
- The difference between a plain table and a **hypertable** (time-series storage)
- **Idempotent ingestion** via `INSERT ... ON CONFLICT` (UPSERT) — re-runnable, no dupes
- Clean config-from-environment and a `src/` package layout

---

## Prerequisites (install these yourself)
- **Docker Desktop** — and make sure it's *running* before step 2
- **`uv`** — the Python package + environment manager  ·  install: https://docs.astral.sh/uv/
- You already have these on this machine. Nothing else to install for Phase 0.

> We pin Python to **3.12** via the `.python-version` file. `uv` will fetch that exact
> interpreter for you — independent of whatever Python your system has — so dependency
> wheels resolve cleanly. (Bleeding-edge Pythons sometimes lack prebuilt wheels.)

---

## Project layout
```
tradeos/
├── docker-compose.yml      # brings up Postgres + TimescaleDB
├── db/init/01_init.sql     # runs ONCE on first DB boot: creates table + hypertable
├── holdings.txt            # YOUR tickers, one per line  ← edit this
├── pyproject.toml          # deps + the `tradeos-ingest` / `tradeos-check` commands
├── .python-version         # pins Python 3.12 for uv
├── .env.example            # copy to .env only if you change DB creds
└── src/tradeos/
    ├── config.py           # env-driven settings + holdings loader
    ├── db.py               # one place that knows how to connect
    ├── ingest.py           # fetch OHLCV (yfinance) → UPSERT into Postgres
    └── check.py            # prints row counts + date ranges (your "done" check)
```

---

## Run it (5 steps)

**1 — Put in your holdings.** Edit `holdings.txt`, one ticker per line. NSE = `.NS` suffix
(e.g. `RELIANCE.NS`), BSE = `.BO`. (Examples are prefilled; swap in your real holdings.)

**2 — Start the database.**
```bash
docker compose up -d
```
First run pulls the image and runs `db/init/01_init.sql` to create the `prices` hypertable.
Check it's healthy: `docker compose ps` (look for `healthy`).

**3 — Install dependencies.**
```bash
uv sync
```
Creates `.venv/`, fetches Python 3.12 if needed, installs deps, and writes `uv.lock`.

**4 — Ingest prices.**
```bash
uv run tradeos-ingest
```
Re-run anytime (e.g. daily) — it UPSERTs, so no duplicates.

**5 — Verify (definition of done).**
```bash
uv run tradeos-check
```
You should see a row per ticker with a count (~250 rows/yr of trading days) and a date range.

Prefer raw SQL? That's the real goal — being able to query your own data:
```bash
docker compose exec db psql -U tradeos -d tradeos \
  -c "SELECT symbol, count(*), min(date), max(date) FROM prices GROUP BY symbol;"
```

✅ **Phase 0 is done when** that query returns ~1+ year of daily candles for each of your holdings.

---

## How the key pieces work (the learning bit)

- **Hypertable** (`01_init.sql`): `create_hypertable('prices', 'date')` tells TimescaleDB to
  transparently partition `prices` into time-based chunks. You still `SELECT` from it like a
  normal table — Timescale just makes time-range queries and retention scale. Rule you hit
  here: a hypertable's unique key **must include the partition column**, which is why the
  primary key is `(symbol, date)` and not just `symbol`.
- **Idempotency** (`ingest.py`): the `ON CONFLICT (symbol, date) DO UPDATE` clause means
  "insert, or if this symbol+day already exists, overwrite it." That's what lets you run the
  ingest on a schedule without ever creating duplicate rows — a core data-engineering habit.
- **Config from env** (`config.py`): no connection strings or paths baked into logic. Change
  behavior with env vars / `.env`, not code edits.
- **`src/` layout + `[project.scripts]`**: keeps importable code in one package and gives you
  clean CLI commands (`uv run tradeos-ingest`) instead of loose scripts.

---

## Troubleshooting
- **`connection refused`** → the DB isn't up/healthy yet. `docker compose ps`; wait for `healthy`.
- **`no data returned` for a ticker** → wrong symbol or suffix. Indian stocks need `.NS`/`.BO`.
- **Changed `01_init.sql` and it didn't apply** → init scripts run only on a *fresh* volume:
  `docker compose down -v && docker compose up -d` (this deletes the stored data).
- **Port 5432 already in use** → another Postgres is running; stop it or change the host port
  in `docker-compose.yml` (and `DATABASE_URL`).

## Reset everything
```bash
docker compose down -v     # stops the DB and DELETES its data volume
```

---

## What's next — Phase 1
With reliable price data in your own DB, you build the **Risk Agent**: compute real portfolio
risk (concentration, volatility, beta, drawdown) from this data, then add a thin LLM layer that
*explains* it in plain English. See `ROADMAP.md` → Phase 1.
