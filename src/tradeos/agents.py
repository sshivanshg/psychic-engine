"""The agent framework — a uniform interface over the analyzer agents.

Every analyzer is an `Agent` with a `name`, a `scope` ("portfolio" or "per_stock"), and a
`run(ctx)` that reads from the shared AnalysisContext (no direct DB access). The orchestrator
iterates `REGISTRY` — adding a new agent (macro, sentiment, …) is just: write its compute, wrap it
here, append to the registry. The compute math lives in risk.py / technical.py / fundamental.py and
is unchanged; these classes only adapt it to the common interface + shared context.
"""

from .context import AnalysisContext
from .fundamental import compute_all_fundamental
from .risk import compute_risk
from .technical import compute_all_technical


class Agent:
    name: str = "agent"
    scope: str = "per_stock"  # "portfolio" -> one dict; "per_stock" -> {symbol: dict}

    def run(self, ctx: AnalysisContext) -> dict:
        raise NotImplementedError


class RiskAgent(Agent):
    name = "risk"
    scope = "portfolio"

    def run(self, ctx: AnalysisContext) -> dict:
        return compute_risk(as_of=ctx.as_of, horizon=ctx.horizon,
                            panels=ctx.panels, positions=ctx.positions)


class TechnicalAgent(Agent):
    name = "technical"
    scope = "per_stock"

    def run(self, ctx: AnalysisContext) -> dict:
        return compute_all_technical(as_of=ctx.as_of, panels=ctx.panels, positions=ctx.positions)


class FundamentalAgent(Agent):
    name = "fundamental"
    scope = "per_stock"

    def run(self, ctx: AnalysisContext) -> dict:
        return compute_all_fundamental(as_of=ctx.as_of, fundamentals=ctx.fundamentals,
                                       positions=ctx.positions)


REGISTRY: list[Agent] = [RiskAgent(), TechnicalAgent(), FundamentalAgent()]
