"""AnalysisContext — load all shared data ONCE, then hand it to every agent.

Before this, each agent re-queried the DB independently (and the Fundamental agent opened a
connection *per holding*). The context loads the price panels and all fundamentals up front, so a
full multi-agent `analyze()` hits the database a fixed, small number of times regardless of how
many agents or holdings there are.
"""

from dataclasses import dataclass

import pandas as pd

from .config import BENCHMARK, Position, load_portfolio
from .fundamental import load_fundamentals
from .risk import _load_panels


@dataclass
class AnalysisContext:
    as_of: object
    horizon: str
    positions: list[Position]
    close: pd.DataFrame        # split-adjusted price (levels/value/technicals)
    adj: pd.DataFrame          # total-return (returns/vol/VaR/beta)
    volume: pd.DataFrame
    fundamentals: dict         # {symbol: quarterly DataFrame}

    @property
    def panels(self) -> tuple:
        return (self.close, self.adj, self.volume)

    @property
    def symbols(self) -> list[str]:
        return [p.symbol for p in self.positions]

    @classmethod
    def build(cls, as_of=None, horizon: str = "annual") -> "AnalysisContext":
        positions = load_portfolio()
        symbols = [p.symbol for p in positions]
        close, adj, volume = _load_panels(symbols + [BENCHMARK], as_of)
        fundamentals = load_fundamentals(symbols, as_of)
        return cls(as_of, horizon, positions, close, adj, volume, fundamentals)
