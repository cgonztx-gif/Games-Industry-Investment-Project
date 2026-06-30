# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Games Industry Investment Intelligence Platform

## Project Overview
A multi-agent investment intelligence system that monitors the games industry across product, community, and financial data layers, then synthesizes signals into a weekly portfolio briefing.

**Core thesis:** Game-level data (player counts, sentiment, patch cadence, studio hiring) leads financial performance. Traditional investors underweight it.

Full design: `project context files/games-investment-platform-brief.md`  
Agent internals: `project context files/agent-components-plan.md`  
Reddit adapter design: `project context files/reddit_source_adapter.md`  
Supabase cache design: `project context files/supabase_reddit_cache.md`

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
    sentiment/        Reddit/Steam review sentiment (VADER + Claude ABSA)
    patch_notes/      Update cadence analysis
    studio_intel/     Job postings, press releases, SEC filings
    financial_overlay/ yfinance + SEC EDGAR equity mapping
    discovery/        New watchlist candidate proposals
  synthesis/          Synthesis agent (reads all worker outputs)
  portfolio/          Portfolio manager + execution subagent
  skills/             SKILL.md files (progressive disclosure)
database/
  schema.sql          Supabase table definitions
  migrations/         Incremental schema changes (apply in Supabase SQL Editor)
scripts/              One-off maintenance scripts (rawg_backfill.py, etc.)
dashboard/            Next.js frontend (scaffolded in Phase 6)
project context files/ Planning and design documents
.github/workflows/    GitHub Actions cron pipelines
```

---

## Build Phases
| Phase | Scope | Status |
|---|---|---|
| 1 | Foundation + Watchlist Seeding | **Complete** |
| 2 | Sentiment Layer | **In progress** |
| 3 | Studio & Financial Intelligence | Partially built |
| 4 | Synthesis Agent & Briefing | Planned |
| 5 | Discovery Agent | Planned |
| 6 | Dashboard Polish | Planned |
| 7 | Portfolio Manager + Alpaca Execution | Planned |

See `tasks.md` for per-phase checklists and current status.

---

## Environment Variables
Copy `.env.example` to `.env`. Required per phase:

**Phase 1:**
- `ANTHROPIC_API_KEY`
- `SUPABASE_URL`, `SUPABASE_KEY`
- `STEAM_API_KEY`
- `IGDB_CLIENT_ID`, `IGDB_CLIENT_SECRET`
- `RAWG_API_KEY`

**Phase 2 (sentiment worker):**
- `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` — required for PRAW Reddit client; worker degrades to Steam-only if missing

**Later phases:**
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
| Haiku-class | claude-haiku-4-5-20251001 | Classification, formatting, trivial steps (ABSA) |

---

## Key Architecture Rules
1. **Workers return only structured output** — no raw post bodies or full API responses cross back to the orchestrator
2. **Skills live in `agents/skills/`** as `SKILL.md` files with frontmatter `trigger:` descriptions for progressive disclosure
3. **Subagents are strictly two levels deep** — orchestrator → workers; workers cannot spawn subagents (SDK constraint)
4. **Execution subagent has Alpaca tools only** — tool restriction is the primary safety guardrail
5. **All trade execution requires `status = 'approved'` in Supabase** — enforced by a `before-tool-call` lifecycle hook as belt-and-suspenders

### Sentiment pipeline internals (`agents/workers/sentiment/`)
The sentiment worker runs a two-pass pipeline per game:
- **VADER baseline** (`vader_scorer.py`) — deterministic rule-based polarity score over all texts, returns a 1–10 float
- **ABSA** (`absa_client.py`) — Claude Haiku extracts aspect→polarity pairs (e.g. `monetization → negative`); skipped if fewer than 5 texts; top 3 aspects returned
- **Divergence check** (`divergence.py`) — compares text sentiment against last known player metrics; sets `divergence_flag` if significant gap
- **Reddit client** (`reddit_client.py`) — PRAW OAuth (requires `REDDIT_CLIENT_ID`/`REDDIT_CLIENT_SECRET`); gracefully skips Reddit and runs Steam-only if credentials are absent. The design doc (`reddit_source_adapter.md`) describes a future unauthenticated-JSON adapter with Supabase caching for resilience on data-center IPs; the current implementation uses PRAW.

### External data caching design
`project context files/supabase_reddit_cache.md` specifies a generic `api_cache` table (`source TEXT, key TEXT, payload JSONB, fetched_at TIMESTAMPTZ`) that backs the source adapters. The table schema and TTL semantics are documented there; apply it when building the unauthenticated Reddit adapter or other volatile-source collectors.

---

## Running the Agents
```bash
# Install dependencies
pip install -r requirements.txt

# Apply pending migrations (Supabase SQL Editor or psql)
# database/migrations/001_sentiment_snapshots_unique.sql

# Run the watchlist seeding agent (one-time, idempotent)
python agents/orchestrator/seed_watchlist.py

# RAWG backfill — populate rawg_slug and steam_app_id (one-time, resumable)
python scripts/rawg_backfill.py --dry-run     # preview
python scripts/rawg_backfill.py               # full run
python scripts/rawg_backfill.py --limit 50 --offset 200  # resume from offset

# Test an individual worker
python -c "import sys; sys.path.insert(0, '.'); from dotenv import load_dotenv; load_dotenv(); from agents.workers.market_player import worker; import json; print(json.dumps(worker.run(), indent=2))"

# Run the full weekly pipeline (triggered by GitHub Actions cron)
python run_weekly.py
```
