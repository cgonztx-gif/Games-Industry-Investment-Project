# Games Industry Investment Intelligence Platform

## Project Overview
A multi-agent investment intelligence system that monitors the games industry across product, community, and financial data layers, then synthesizes signals into a weekly portfolio briefing.

**Core thesis:** Game-level data (player counts, sentiment, patch cadence, studio hiring) leads financial performance. Traditional investors underweight it.

Full design: `docs/games-investment-platform-brief.md`  
Agent internals: `docs/agent-components-plan.md`

---

## Tech Stack
- **Agent orchestration:** CrewAI (MVP prototype) → LangGraph / Claude Agent SDK (production)
- **LLM:** Claude API (Anthropic) — model tiering documented below
- **Database:** Supabase (PostgreSQL + pgvector extension)
- **Scheduling:** GitHub Actions weekly cron
- **Frontend:** Next.js 16 + shadcn/ui + Recharts
- **Observability:** LangSmith
- **Paper trading:** Alpaca API (official MCP server)

---

## Folder Structure
```
agents/
  orchestrator/       Lead orchestrator that dispatches workers
  workers/            Specialized data-collection subagents
    market_player/    Steam/IGDB/RAWG engagement metrics
    sentiment/        Reddit/X/YouTube/Steam review sentiment
    patch_notes/      Update cadence analysis
    studio_intel/     Job postings, press releases, SEC filings
    financial_overlay/ yfinance + SEC EDGAR equity mapping
    discovery/        New watchlist candidate proposals
  synthesis/          Synthesis agent (reads all worker outputs)
  portfolio/          Portfolio manager + execution subagent
  skills/             SKILL.md files (progressive disclosure)
database/
  schema.sql          Supabase table definitions
  migrations/         Incremental schema changes
dashboard/            Next.js frontend (scaffolded in Phase 6)
docs/                 Planning and design documents
.github/workflows/    GitHub Actions cron pipelines
```

---

## Build Phases
| Phase | Scope | Status |
|---|---|---|
| 1 | Foundation + Watchlist Seeding | **In progress** |
| 2 | Sentiment Layer | Planned |
| 3 | Studio & Financial Intelligence | Planned |
| 4 | Synthesis Agent & Briefing | Planned |
| 5 | Discovery Agent | Planned |
| 6 | Dashboard Polish | Planned |
| 7 | Portfolio Manager + Alpaca Execution | Planned |

**Phase 1 checklist:**
- [ ] Supabase project created, schema applied
- [ ] Watchlist seeding agent (IGDB + RAWG + Steam → 150–300 games)
- [ ] CrewAI crew scaffolded with placeholder agents
- [ ] GitHub Actions weekly cron trigger wired

---

## Environment Variables
Copy `.env.example` to `.env`. Required per phase:

**Phase 1:**
- `ANTHROPIC_API_KEY`
- `SUPABASE_URL`, `SUPABASE_KEY`
- `STEAM_API_KEY`
- `IGDB_CLIENT_ID`, `IGDB_CLIENT_SECRET`
- `RAWG_API_KEY`

**Later phases:**
- `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT`
- `X_BEARER_TOKEN`
- `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_BASE_URL`
- `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`

---

## Agent Model Tiering
Always lock the model per-agent in config; never default to the most capable.

| Tier | Model | Used for |
|---|---|---|
| Opus-class | claude-opus-4-8 | Synthesis Agent, Portfolio Manager |
| Sonnet-class | claude-sonnet-4-6 | All data worker subagents |
| Haiku-class | claude-haiku-4-5-20251001 | Classification, formatting, trivial steps |

---

## Key Architecture Rules
1. **Workers return only structured output** — no raw post bodies or full API responses cross back to the orchestrator
2. **Skills live in `agents/skills/`** as `SKILL.md` files with frontmatter `trigger:` descriptions for progressive disclosure
3. **Subagents are strictly two levels deep** — orchestrator → workers; workers cannot spawn subagents (SDK constraint)
4. **Execution subagent has Alpaca tools only** — tool restriction is the primary safety guardrail
5. **All trade execution requires `status = 'approved'` in Supabase** — enforced by a `before-tool-call` lifecycle hook as belt-and-suspenders

---

## Running the Agents (Phase 1)
```bash
# Install dependencies
pip install -r requirements.txt

# Run the watchlist seeding agent (one-time)
python agents/orchestrator/seed_watchlist.py

# Run the full weekly pipeline (eventually triggered by GitHub Actions)
python agents/orchestrator/run_pipeline.py
```
