# TradeOS — guide for Claude Code

Personal **portfolio-intelligence learning project** — NOT a product (the SaaS framing was
deliberately killed: SEBI advice regulation + weak signal alpha + saturated market). Built on my
real holdings to learn agentic LLM + quant systems and as a hireable showcase. Agents stay
**descriptive** (explain risk; never buy/sell/hold) — I make the call.

## Source of truth
- **Code** → this repo.  **Thinking, decisions, changelog** → Obsidian vault:
  `~/Documents/SecondBrain/02-Projects/TradeOS/`

## 📓 Documentation protocol (repo ↔ vault) — DO THIS
After any meaningful work here (shipped feature, decision, fix, blocker), **append a dated entry**
to the vault working log:

  `~/Documents/SecondBrain/02-Projects/TradeOS/Log/Working log — TradeOS.md`

Newest at top. Capture *what shipped / what was decided / root cause + lesson / blocked / next* —
match the existing entry style. Keep `Tech/Architecture — TradeOS.md` and
`Tech/Risk engine methodology — TradeOS.md` current when the design changes; extract reusable
insights as atomic notes in `01-Notes/`. The vault is git-backed (`sshivanshg/second-brain`) —
commit there too. This mirrors the Arth Saathi protocol.

## Conventions
- Python + `uv`. Run via `uv run …`; tests `uv run pytest`; lint `uv run ruff check .`.
- DB: local Homebrew Postgres, database `tradeos` (`DATABASE_URL` in `.env`).
- Claude API: `messages.parse` + Pydantic structured output; default model `claude-opus-4-8`.
- **git commits: NO AI watermark / co-author trailer.**

## Status
Phase 0 (data foundation) ✓ · Phase 1 (Risk Agent — quant engine: EWMA covariance, component
risk %, VaR/CVaR, stress, liquidity, limits, `--horizon` scaling) ✓. Phase 2 (multi-agent core:
hand-built orchestrator + Technical agent → per-stock cards via `tradeos-analyze`) ✓.
Next: Phase 3 (RAG over filings/concalls). Full plan in `ROADMAP.md`.
