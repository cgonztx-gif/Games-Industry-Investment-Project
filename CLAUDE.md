# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Games Industry Investment Intelligence Platform

## Project Overview
A multi-agent investment intelligence system that monitors the games industry across product, community, and financial data layers, then synthesizes signals into a weekly portfolio briefing.

**Core thesis:** Game-level data (player counts, sentiment, patch cadence, studio hiring) leads financial performance. Traditional investors underweight it.

Full design: `docs/games-investment-platform-brief.md`  
Agent internals: `docs/agent-components-plan.md`  
Risk register: `docs/data-source-risk-register.md`  
Reddit adapter design: `docs/reddit_source_adapter.md`  
Supabase cache design: `docs/supabase_reddit_cache.md`

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
dashboard/            Next.js frontend (scaffolded in Phase 7)
docs/                 Planning and design documents
.github/workflows/    GitHub Actions cron pipelines
```

---

## Build Phases
| Phase | Scope | Status |
|---|---|---|
| 1 | Foundation + Watchlist Seeding | **Complete** |
| 2 | Sentiment Layer | **In progress** |
| 3 | Studio & Financial Intelligence | Partially built |
| 4 | Synthesis Agent & Briefing | Partially built |
| 5 | Portfolio Manager + Alpaca Execution | Partially built |
| 6 | Discovery Agent | Planned |
| 7 | Dashboard Polish | Planned |

See `tasks.md` for per-phase checklists and current status.

---

## Environment Variables
Copy `.env.example` to `.env`. Required per phase:

**Phase 1:**
- `ANTHROPIC_API_KEY`
- `SUPABASE_URL`, `SUPABASE_KEY`
- `STEAM_API_KEY`
- `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`
- `RAWG_API_KEY`

**Phase 2 (sentiment worker):**
- No Reddit OAuth credentials are required. Reddit collection uses public read-only `.json` endpoints through `agents/workers/sentiment/reddit_source.py` and `api_cache`.
- `YOUTUBE_API_KEY` is required once the YouTube Data API comment collector is enabled.

**Phase 3 (patch_notes worker, optional):**
- `GAME_PATCH_PAGES` — optional JSON object mapping a watchlist game's exact `games.title` to one or more developer-blog/official patch-page URLs (string or list of strings), e.g. `{"Fortnite": ["https://www.fortnite.com/news/rss"]}`. Loaded defensively (missing/invalid → `{}`) by `agents/workers/patch_notes/blog_client.py`; most games won't have one configured. Same manually-curated-config convention as `STUDIO_ATS_BOARDS` (`agents/workers/studio_intel/ats_clients.py`).

**Phase 4 (observability, optional):**
- `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` — the pipeline runs identically with tracing fully disabled (no-op, zero network calls) if these are unset.

**Phase 4 (briefing email delivery, optional):**
- `RESEND_API_KEY`, `BRIEFING_EMAIL_TO` — the briefing still writes to Supabase with email delivery fully skipped (no-op, zero network calls) if either is unset. `BRIEFING_EMAIL_TO` may be a single address or a comma-separated list.
- `BRIEFING_EMAIL_FROM` — optional sender override; defaults to Resend's sandbox sender (`onboarding@resend.dev`), which only delivers to the Resend account owner's own address until a custom domain is verified.

**Later phases:**
- `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_BASE_URL`

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
5. **All trade execution requires `status = 'approved'` in Supabase** — enforced inside the order-placement tool, with lifecycle hooks only as an additional mirror later

### Sentiment pipeline internals (`agents/workers/sentiment/`)
The sentiment worker runs a two-pass pipeline per game:
- **VADER baseline** (`vader_scorer.py`) — deterministic rule-based polarity score over all texts, returns a 1–10 float
- **ABSA** (`absa_client.py`) — Claude Haiku extracts aspect→polarity pairs (e.g. `monetization → negative`); skipped if fewer than 5 texts; top 3 aspects returned
- **Preliminary lagged flag** (`divergence.py`) — optional hint against the latest stored player metrics; authoritative same-week divergence belongs in synthesis
- **Reddit source** (`reddit_source.py`, `reddit_cache.py`) — unauthenticated public `.json` adapter with rate limiting, Supabase-backed `api_cache`, and stale fallback. No PRAW/OAuth path is used.

### Tracing internals (`agents/tracing.py`)
`configure_tracing()` is called once at the very start of `run_weekly.py`'s pipeline run. Each worker/synthesis/crew call is wrapped via `traced_step(name)(callable)()` at the `run_weekly.py` call site — not via decorators inside each worker file, to avoid touching every worker module. The one real LLM call site (`agents/workers/sentiment/absa_client.py`) is instrumented via `langsmith.wrappers.wrap_anthropic`, which captures token usage automatically. Fully opt-in: everything no-ops with zero network calls when `LANGSMITH_API_KEY` is unset.

### Briefing email delivery (`agents/synthesis/email_delivery.py`)
`send_briefing_email(briefing)` is called from `agents/synthesis/agent.py`'s `run()` immediately after `write_weekly_briefing(db, briefing)`, wrapped in a try/except so an email failure can never prevent the briefing from being considered "done." Renders a plain HTML summary (briefing text + bullet lists for top opportunities / risk flags / notable events) and POSTs it directly to Resend's REST API (`POST https://api.resend.com/emails`) via `requests` — no `resend` SDK dependency. Fully opt-in: no-ops with zero network calls when `RESEND_API_KEY` or `BRIEFING_EMAIL_TO` is unset, and any HTTP failure is caught and logged rather than raised.

### External data caching design
`docs/supabase_reddit_cache.md` specifies a generic `api_cache` table (`source TEXT, key TEXT, payload JSONB, fetched_at TIMESTAMPTZ`) that backs Tier-2 source adapters. The table schema and TTL semantics are documented there; apply migrations before running volatile-source collectors.

---

## Running the Agents
```bash
# Install dependencies
pip install -r requirements.txt

# Apply pending migrations (Supabase SQL Editor or psql)
# database/migrations/001_sentiment_snapshots_unique.sql
# database/migrations/002_api_cache.sql
# database/migrations/003_watchlist_sentiment_targets.sql
# database/migrations/004_patch_events_source_url.sql
# database/migrations/005_equity_signals.sql
# database/migrations/006_patch_events_cadence_flags.sql
# database/migrations/007_player_metrics_review_score_precision.sql

# Run the watchlist seeding agent (one-time, idempotent)
python agents/orchestrator/seed_watchlist.py

# RAWG backfill — populate rawg_slug and steam_app_id (one-time, resumable)
python scripts/rawg_backfill.py --dry-run                 # preview full default page
python scripts/rawg_backfill.py --chunk-size 100 --dry-run # preview next chunk
python scripts/rawg_backfill.py --chunk-size 100           # run one bounded chunk
python scripts/rawg_backfill.py --chunk-size 100 --max-chunks 5  # run up to five chunks
python scripts/rawg_backfill.py                            # full run
python scripts/rawg_backfill.py --limit 50 --offset 200    # manual page

# Review pending trade plans/orders before execution (human-in-the-loop; sets DB status only, never places orders)
python scripts/review_trade_plans.py list
python scripts/review_trade_plans.py approve --plan <plan_id>   # cascades to all pending orders under the plan
python scripts/review_trade_plans.py reject --plan <plan_id>
python scripts/review_trade_plans.py approve --order <order_id> # per-order override; does not touch the parent plan
python scripts/review_trade_plans.py reject --order <order_id>

# Test an individual worker
python -c "import sys; sys.path.insert(0, '.'); from dotenv import load_dotenv; load_dotenv(); from agents.workers.market_player import worker; import json; print(json.dumps(worker.run(), indent=2))"

# Run the full weekly pipeline (triggered by GitHub Actions cron)
python run_weekly.py
```
