# Games Industry Investment Intelligence Platform

A multi-agent investment intelligence system that monitors the games industry and synthesizes signals into weekly portfolio briefings.

**Core thesis:** Game-level data (player counts, sentiment, patch cadence, studio hiring) leads financial performance by weeks. Traditional investors underweight it.

---

## What It Does

- Tracks **150–300 games** and **30–50 studios** across player metrics, community sentiment, patch behavior, and organizational signals
- Synthesizes signals weekly into a portfolio briefing with buy/sell/hold recommendations and risk flags
- Generates a structured trade plan, surfaces it for **human approval**, then executes via Alpaca paper trading
- Visualizes everything — sentiment trends, portfolio P&L, trade history with Claude's full reasoning — in a Next.js dashboard

---

## Architecture

Eight specialized Claude agents in an orchestrator–worker pattern:

```
[GitHub Actions: weekly cron]
        ↓
[Orchestrator] → dispatches workers in parallel
  ├── Market & Player Subagent     (Steam/IGDB/RAWG metrics)
  ├── Sentiment Subagent           (Reddit/X/YouTube/Steam reviews)
  ├── Patch Notes Subagent         (update cadence analysis)
  ├── Studio Intelligence Subagent (hiring, layoffs, SEC filings)
  ├── Financial Overlay Subagent   (yfinance, earnings context)
  └── Discovery Subagent           (new watchlist proposals)
        ↓
[Synthesis Agent]  → weekly briefing
        ↓
[Portfolio Manager Agent] → trade plan → human approval → [Execution Agent → Alpaca]
```

Full design: [`docs/games-investment-platform-brief.md`](docs/games-investment-platform-brief.md)  
Agent internals: [`docs/agent-components-plan.md`](docs/agent-components-plan.md)

---

## Tech Stack

| Layer | Technology |
|---|---|
| Agent orchestration | CrewAI (MVP) → LangGraph / Claude Agent SDK |
| LLM | Claude API (Anthropic) |
| Database | Supabase (PostgreSQL) |
| Scheduling | GitHub Actions |
| Frontend | Next.js 16 + shadcn/ui + Recharts |
| Observability | LangSmith |
| Paper trading | Alpaca API |

---

## Build Phases

- [x] **Phase 0** — Planning docs
- [ ] **Phase 1** — Foundation + Watchlist Seeding ← _current_
- [ ] **Phase 2** — Sentiment Layer
- [ ] **Phase 3** — Studio & Financial Intelligence
- [ ] **Phase 4** — Synthesis Agent & Briefing
- [ ] **Phase 5** — Discovery Agent
- [ ] **Phase 6** — Dashboard Polish
- [ ] **Phase 7** — Portfolio Manager + Alpaca Execution

---

## Setup

> Detailed setup instructions will be added as each phase completes.

```bash
# Clone the repo
git clone <repo-url>
cd games-industry-investment-platform

# Install Python dependencies
pip install -r requirements.txt

# Copy and fill in environment variables
cp .env.example .env

# Run the watchlist seeding agent (Phase 1 — one-time)
python agents/orchestrator/seed_watchlist.py
```
