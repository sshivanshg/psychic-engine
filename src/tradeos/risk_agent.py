"""The LLM layer of the Risk Agent — a buy-side risk-manager narration.

It takes the *precomputed* quant metrics from risk.py and asks Claude to explain them like
a desk risk manager would: lead with where the risk actually lives (component contribution),
the tail loss (VaR/CVaR), what a stress day does, diversification truth (correlation), liquidity,
and any limit breaches. The model never computes — it only interprets — so it can't invent a
number. Structured output (Pydantic) guarantees parseable fields, not prose we'd have to scrape.
"""

import json
import os

from pydantic import BaseModel

from .config import CLAUDE_MODEL
from .trace import RunTrace, timed_call


class PositionNote(BaseModel):
    symbol: str
    note: str


class RiskReport(BaseModel):
    headline: str                 # the single most important risk read, up front
    risk_drivers: list[str]       # WHERE risk comes from (component contribution, beta, corr)
    tail_and_stress: str          # interpret VaR/CVaR + worst historical windows
    diversification: str          # correlation / effective holdings — real, not nominal
    liquidity: str                # days-to-liquidate read
    limit_flags: list[str]        # plain-English limit breaches (or "all within budget")
    position_notes: list[PositionNote]
    reminder: str                 # one line: descriptive risk analysis, not investment advice


SYSTEM_PROMPT = """You are a buy-side RISK MANAGER reviewing an individual investor's own book.
You are handed precomputed, factual risk metrics (EWMA covariance-based, total-return prices).
Explain the risk the way a desk risk manager would — concise, quantitative, decision-useful.

Vol and VaR/CVaR are expressed at the portfolio's stated horizon (see the 'horizon' field) —
quote that horizon when you cite them. Read the numbers like a quant, not a retail dashboard:
- LEAD with where risk actually lives: the COMPONENT risk contribution (risk_contribution_pct),
  NOT standalone weight or vol. Call out the top risk contributor and whether its share of risk
  exceeds its share of capital. (Risk contribution, beta and correlation are horizon-invariant.)
- Interpret TAILS: VaR vs CVaR at the stated horizon (CVaR is the average loss in the bad tail),
  and the worst historical 1/5/10/21-day windows as a stress read.
- Diversification TRUTH from average pairwise correlation and effective holdings — high correlation
  means the names move together and the book is less diversified than the count suggests.
- Liquidity: days-to-liquidate; flag anything slow to exit.
- Report every limit breach plainly; if all within budget, say so.

Hard rules:
- Describe and interpret. Do NOT give buy / sell / hold recommendations or price targets.
- Explain the risk a position carries and how it contributes to the book — never what to do about it.
- Cite the actual figures. Be tight and useful.
- End with a one-line reminder that this is descriptive risk analysis, not investment advice."""


def narrate_risk(risk: dict, trace: RunTrace | None = None) -> RiskReport | None:
    """Return a structured buy-side risk read, or None if no API key is set."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None

    import anthropic  # lazy import so the numbers-only path needs no SDK/key

    own = trace is None          # standalone call owns + prints its own trace; composed callers pass one in
    trace = trace or RunTrace()
    client = anthropic.Anthropic()
    response = timed_call(
        trace, "risk", CLAUDE_MODEL,
        lambda: client.messages.parse(
            model=CLAUDE_MODEL,
            max_tokens=2500,
            # cache_control marks the system prompt cacheable; only actually caches once the prefix
            # exceeds the model minimum (~4096 tokens on Opus), harmless below that.
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{
                "role": "user",
                "content": (
                    "Here are the precomputed risk metrics for my portfolio as JSON. "
                    "Give me the desk risk read.\n\n" + json.dumps(risk, indent=2)
                ),
            }],
            output_format=RiskReport,
        ),
    )
    if own:
        trace.print_summary()
    return response.parsed_output
