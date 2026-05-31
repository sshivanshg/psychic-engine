"""Central configuration.

Everything tunable is read from environment variables (with sane defaults), so no
secrets or machine-specific values are hard-coded in the logic.
"""

import csv
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # loads a .env file at the project root, if one exists

# src/tradeos/config.py -> parents[2] is the project root (where holdings.csv lives)
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Connection string for the Postgres/TimescaleDB container from docker-compose.yml.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://tradeos:tradeos@localhost:5432/tradeos",
)

# How much daily history to pull per ticker (any yfinance period: 1y, 2y, 5y, max).
HISTORY_PERIOD = os.getenv("HISTORY_PERIOD", "2y")

# Benchmark index for beta / market-relative risk. ^NSEI = NIFTY 50 on yfinance.
BENCHMARK = os.getenv("BENCHMARK", "^NSEI")

# Claude model for the risk narration (Phase 1). Default to the most capable model;
# override with claude-sonnet-4-6 / claude-haiku-4-5 if you want it cheaper.
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-4-8")

# Risk budget — the limits the agent checks the portfolio against and flags breaches on.
# These are sane defaults for a concentrated long-only book; tune them to your own mandate.
RISK_LIMITS = {
    "max_name_weight_pct": 30.0,        # no single holding > 30% of capital
    "max_name_risk_pct": 40.0,          # no single holding > 40% of total portfolio RISK
    "max_annual_vol_pct": 25.0,         # ex-ante annualised portfolio volatility ceiling
    "max_var99_pct": 4.0,               # 1-day 99% VaR ceiling (% of portfolio)
    "min_effective_holdings": 3.0,      # diversification floor (1 / sum(weight^2))
    "max_days_to_liquidate": 5.0,       # any name taking > 5 days to exit is a liquidity risk
}

# Covariance shrinkage (Ledoit-Wolf toward a constant-correlation target). Default on; COV_SHRINKAGE=0 to disable.
COV_SHRINKAGE = os.getenv("COV_SHRINKAGE", "1").lower() not in ("0", "false", "no")

# Fundamentals are only *known* ~this many days after quarter-end (results announcement). Point-in-time
# fundamental reads filter on period_end + this lag, so a back-test never sees results before they were public.
ANNOUNCEMENT_LAG_DAYS = int(os.getenv("ANNOUNCEMENT_LAG_DAYS", "45"))

# RAG (Phase 3): if even the closest retrieved chunk is farther than this cosine distance, `ask()`
# flags the answer as weak/low-confidence instead of confidently answering over chunks that don't
# cover the question (it never hides the evidence — only adds a caution). Calibrated for
# bge-small-en-v1.5 (on-topic best hit ~0.30, off-topic best ~0.47); tune per corpus & embedding model.
RAG_MAX_DISTANCE = float(os.getenv("RAG_MAX_DISTANCE", "0.45"))

# Your portfolio: CSV with columns symbol,quantity,avg_cost ('#' lines are comments).
PORTFOLIO_FILE = Path(os.getenv("PORTFOLIO_FILE", PROJECT_ROOT / "holdings.csv"))


@dataclass(frozen=True)
class Position:
    """One holding: a ticker, how many units you own, and what you paid on average."""

    symbol: str
    quantity: float
    avg_cost: float | None  # optional — None if you didn't record it


def load_portfolio() -> list[Position]:
    """Parse holdings.csv into Position objects.

    Lines starting with '#' (and blank lines) are ignored. The first remaining line
    must be the header `symbol,quantity,avg_cost`. avg_cost may be left blank.
    """
    if not PORTFOLIO_FILE.exists():
        raise FileNotFoundError(
            f"Portfolio file not found: {PORTFOLIO_FILE}\n"
            "Create it with a header line `symbol,quantity,avg_cost`, then one row per holding."
        )

    # Drop comments/blank lines first so csv sees a clean header + rows.
    data_lines = [
        line for raw in PORTFOLIO_FILE.read_text().splitlines()
        if (line := raw.split("#", 1)[0].strip())
    ]
    if not data_lines:
        raise ValueError(f"No holdings found in {PORTFOLIO_FILE}.")

    positions: list[Position] = []
    for row in csv.DictReader(data_lines):
        symbol = (row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        quantity = float(row["quantity"]) if (row.get("quantity") or "").strip() else 0.0
        raw_cost = (row.get("avg_cost") or "").strip()
        positions.append(Position(symbol, quantity, float(raw_cost) if raw_cost else None))

    if not positions:
        raise ValueError(f"No valid holdings parsed from {PORTFOLIO_FILE}.")
    return positions


def load_holdings() -> list[str]:
    """Just the ticker symbols (used by the ingester)."""
    return [p.symbol for p in load_portfolio()]


PORTFOLIO_HEADER = "symbol,quantity,avg_cost"
_PORTFOLIO_PREAMBLE = (
    "# TradeOS — your portfolio. One row per holding.\n"
    "#   columns: symbol,quantity,avg_cost   (avg_cost optional)\n"
    "#   NSE = .NS suffix, BSE = .BO   (e.g. RELIANCE.NS)\n"
    "#   manage with `tradeos add / remove / holdings`, or edit this file directly.\n"
)


def _fmt_num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


def _safe_load() -> list[Position]:
    """load_portfolio() but returns [] instead of raising on a missing/empty file."""
    try:
        return load_portfolio()
    except (FileNotFoundError, ValueError):
        return []


def save_portfolio(positions: list[Position]) -> None:
    """Rewrite holdings.csv (preamble + header + one row per holding), sorted by symbol."""
    lines = [_PORTFOLIO_PREAMBLE + PORTFOLIO_HEADER]
    for p in sorted(positions, key=lambda x: x.symbol):
        cost = "" if p.avg_cost is None else _fmt_num(p.avg_cost)
        lines.append(f"{p.symbol},{_fmt_num(p.quantity)},{cost}")
    PORTFOLIO_FILE.write_text("\n".join(lines) + "\n")


def add_holding(symbol: str, quantity: float, avg_cost: float | None = None) -> list[Position]:
    """Add or replace a holding and persist. Returns the updated portfolio (sorted)."""
    symbol = symbol.strip().upper()
    positions = [p for p in _safe_load() if p.symbol != symbol]
    cost = float(avg_cost) if avg_cost is not None else None
    positions.append(Position(symbol, float(quantity), cost))
    save_portfolio(positions)
    return sorted(positions, key=lambda p: p.symbol)


def remove_holding(symbol: str) -> list[Position]:
    """Remove a holding and persist. Returns the updated portfolio."""
    symbol = symbol.strip().upper()
    positions = [p for p in _safe_load() if p.symbol != symbol]
    save_portfolio(positions)
    return positions
