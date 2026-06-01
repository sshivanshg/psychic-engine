"""AnalysisContext — load all shared data ONCE, then hand it to every agent.

Before this, each agent re-queried the DB independently (and the Fundamental agent opened a
connection *per holding*). The context loads the price panels and all fundamentals up front, so a
full multi-agent `analyze()` hits the database a fixed, small number of times regardless of how
many agents or holdings there are.
"""

from dataclasses import dataclass

import pandas as pd

from .config import BENCHMARK, Position, load_portfolio
from .extraction import load_all_guidance
from .fundamental import load_fundamentals
from .macro import load_sectors
from .ownership import load_ownership
from .risk import _load_panels
from .sentiment import load_sentiment


@dataclass
class AnalysisContext:
    as_of: object
    horizon: str
    positions: list[Position]
    close: pd.DataFrame        # split-adjusted price (levels/value/technicals)
    adj: pd.DataFrame          # total-return (returns/vol/VaR/beta)
    volume: pd.DataFrame
    fundamentals: dict         # {symbol: quarterly DataFrame}
    sectors: dict              # {symbol: sector} for the macro agent
    guidance: dict             # {symbol: extracted concall guidance} for the fundamental agent
    sentiment: dict            # {symbol: [news articles]} for the sentiment agent
    ownership: dict            # {symbol: ownership snapshot} for the ownership agent

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
        sectors = load_sectors(symbols)
        guidance = load_all_guidance(symbols, as_of)   # point-in-time: only docs public by as_of
        sentiment = load_sentiment(symbols, as_of)     # current snapshot (eval-barred), as_of-filtered if dated
        ownership = load_ownership(symbols, as_of)     # current snapshot; hidden on a historical replay (PIT)
        return cls(as_of, horizon, positions, close, adj, volume, fundamentals, sectors, guidance,
                   sentiment, ownership)
