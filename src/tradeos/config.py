"""Central configuration.

Everything tunable lives here and is read from environment variables (with sane
defaults), so no secrets or machine-specific values are hard-coded in the logic.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # loads a .env file at the project root, if one exists

# src/tradeos/config.py -> parents[2] is the project root (where holdings.txt lives)
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Connection string for the Postgres/TimescaleDB container from docker-compose.yml.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://tradeos:tradeos@localhost:5432/tradeos",
)

# How much daily history to pull per ticker (any yfinance period: 1y, 2y, 5y, max).
HISTORY_PERIOD = os.getenv("HISTORY_PERIOD", "2y")

# Plain-text file of tickers, one per line ('#' starts a comment).
HOLDINGS_FILE = Path(os.getenv("HOLDINGS_FILE", PROJECT_ROOT / "holdings.txt"))


def load_holdings() -> list[str]:
    """Read tickers from HOLDINGS_FILE.

    One ticker per line; blank lines and '#' comments are ignored; symbols are
    upper-cased so 'reliance.ns' and 'RELIANCE.NS' are treated the same.
    """
    if not HOLDINGS_FILE.exists():
        raise FileNotFoundError(
            f"Holdings file not found: {HOLDINGS_FILE}\n"
            "Create it with one ticker per line (e.g. RELIANCE.NS)."
        )

    tickers: list[str] = []
    for raw in HOLDINGS_FILE.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()  # drop inline comments + surrounding space
        if line:
            tickers.append(line.upper())

    if not tickers:
        raise ValueError(f"No tickers found in {HOLDINGS_FILE}. Add at least one.")
    return tickers
