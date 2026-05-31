"""Shared test setup.

Integration tests run against the EXAMPLE portfolio (holdings.example.csv) whose tickers are
present in the dev DB — so they never depend on the user's real (possibly empty) holdings.csv.
"""

import pytest

from tradeos import config


@pytest.fixture(scope="session", autouse=True)
def _use_example_portfolio():
    orig = config.PORTFOLIO_FILE
    config.PORTFOLIO_FILE = config.PROJECT_ROOT / "holdings.example.csv"
    yield
    config.PORTFOLIO_FILE = orig
