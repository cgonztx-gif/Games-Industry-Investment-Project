# Repository Guidance

## Project Snapshot

This repo implements a multi-agent games-industry investment intelligence platform. The current working design is in `docs/`, with `CLAUDE.md` and `tasks.md` reflecting current implementation status. When guidance conflicts, prefer the updated planning set under `docs/`.

The implemented system is currently a Python/Supabase data pipeline with a CrewAI shell:

- `agents/orchestrator/seed_watchlist.py` seeds studios, games, and watchlist entries from IGDB, RAWG, and Steam-linked catalog data.
- `agents/workers/market_player/worker.py` writes Steam player/review metrics to `player_metrics`.
- `agents/workers/financial_overlay/worker.py` writes Alpaca/yfinance equity snapshots to `equity_signals`.
- `agents/workers/studio_intel/worker.py` writes recent SEC EDGAR 8-K signals to `studio_signals`.
- `agents/workers/sentiment/worker.py` writes Steam/Reddit `.json` sentiment snapshots to `sentiment_snapshots`.
- `agents/workers/patch_notes/worker.py` merges official Steam News with developer-blog/RSS entries (`blog_client.py`) into `patch_events`.
- `agents/synthesis/agent.py` reads all same-week worker outputs, computes the divergence check, dispatches a bounded `deep_dive.py` research call when a signal warrants it, writes `weekly_briefings`, and sends an optional Resend email (`email_delivery.py`).
- `agents/portfolio/manager.py` reads the latest briefing plus Alpaca account state and calls Claude Opus (`claude-opus-4-8`) to produce a trade plan, written to `trade_plans`/`trade_orders` at `status='pending'`.
- `scripts/review_trade_plans.py` is the human-in-the-loop CLI that approves/rejects those pending plans/orders — nothing reaches `agents/portfolio/execution_agent.py` without going through it first.
- `agents/tracing.py` wraps every worker/synthesis/crew call in `run_weekly.py` as a LangSmith span, fully opt-in and a no-op without `LANGSMITH_API_KEY`.
- `run_weekly.py` runs the worker modules and synthesis first, then starts the CrewAI summary pipeline (still placeholder confirmation tasks, not real logic).

All `agents/skills/*/SKILL.md` methodology files are now written. Planned but not yet implemented: discovery worker, returns tracker, Next.js dashboard, and wiring the Portfolio Manager + execution agent into `run_weekly.py` (currently run manually) plus live paper-trading validation on a real Alpaca account.

## Source Of Truth

- `tasks.md` is the active checklist and should be updated after completing operational tasks.
- `CLAUDE.md` is the existing local agent guide and includes current run commands.
- `docs/games-investment-platform-brief.md` is the current system-level architecture.
- `docs/agent-components-plan.md` is the current agent/skill/tool architecture.
- `docs/data-source-risk-register.md` governs source access choices and required mitigations.
- `docs/reddit_source_adapter.md` and `docs/supabase_reddit_cache.md` describe the Reddit adapter and generic cache design.
- `database/schema.sql` is the baseline Supabase schema.
- Add future schema changes as files under `database/migrations/`; do not silently edit historical schema for already-applied changes.

## Important Design Mismatches

- The current Reddit implementation uses public `.json` endpoints through `agents/workers/sentiment/reddit_source.py` plus Supabase-backed `api_cache` graceful degradation.
- Seed-time trending discovery now uses Steam official most-played/app-list APIs plus IGDB/RAWG enrichment.
- CrewAI agents in `agents/orchestrator/crew.py` mostly run placeholder confirmation tasks. The actual data collection happens in direct Python worker modules.

## Common Commands

Install dependencies:

```powershell
pip install -r requirements.txt
```

Run the one-time watchlist seeder:

```powershell
python agents/orchestrator/seed_watchlist.py
```

Run market/player worker:

```powershell
python -c "import sys; sys.path.insert(0, '.'); from dotenv import load_dotenv; load_dotenv(); from agents.workers.market_player import worker; import json; print(json.dumps(worker.run(), indent=2))"
```

Run RAWG backfill preview:

```powershell
python scripts/rawg_backfill.py --dry-run
```

Run RAWG backfill:

```powershell
python scripts/rawg_backfill.py
```

Run the full weekly pipeline:

```powershell
python run_weekly.py
```

Review and approve/reject pending trade plans (manual, not wired into `run_weekly.py`):

```powershell
python scripts/review_trade_plans.py list
python scripts/review_trade_plans.py approve --plan <plan_id>
```

## Environment Variables

Required for current pipeline work:

- `ANTHROPIC_API_KEY`
- `SUPABASE_URL`
- `SUPABASE_KEY`
- `TWITCH_CLIENT_ID`
- `TWITCH_CLIENT_SECRET`
- `RAWG_API_KEY`
- `STEAM_API_KEY`

Optional/current sentiment expansion:

- `YOUTUBE_API_KEY` once the YouTube Data API collector is enabled

Optional, no-op-safe if unset (pipeline behaves identically without them):

- `LANGSMITH_API_KEY` and `LANGSMITH_PROJECT` for tracing (`agents/tracing.py`).
- `RESEND_API_KEY`, `BRIEFING_EMAIL_TO`, `BRIEFING_EMAIL_FROM` for briefing email delivery (`agents/synthesis/email_delivery.py`).
- `GAME_PATCH_PAGES` for the patch_notes developer-blog source (`agents/workers/patch_notes/blog_client.py`).

Future phases:

- `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, and `ALPACA_BASE_URL` for paper trading — `agents/portfolio/manager.py` and `execution_agent.py` are built and tested against fakes, but not yet exercised against a real Alpaca account.

Never print `.env` contents or secrets in responses.

## Operational Notes

- The `market_player` worker makes paced per-game external API requests. A full run can take several minutes when hundreds of Steam-linked watchlist games exist.
- `scripts/rawg_backfill.py` sleeps three seconds per RAWG request and is intentionally slow to respect the free-tier limit. Use `--limit`, `--offset`, and `--fix-steam` for resumable passes.
- `sentiment_snapshots` upserts require `database/migrations/001_sentiment_snapshots_unique.sql` to be applied in Supabase.
- Tier-2 caching requires `database/migrations/002_api_cache.sql` to be applied in Supabase.
- Watchlist sentiment targeting, patch event idempotency, and equity signals require migrations `003` through `005`. Migration `006` adds patch cadence status/baseline columns. Migration `007` (`player_metrics.review_score` precision fix) exists but is not confirmed applied yet.
- GitHub Actions uses `.github/workflows/weekly.yml`; repo secrets still need to be configured externally.
- Avoid reading or displaying `.env`. It exists locally and contains sensitive values.

## Coding Guidelines For This Repo

- Follow existing direct-worker patterns before adding new orchestration abstractions.
- Keep worker outputs structured and persist them to Supabase tables.
- Prefer small source clients under each worker package for external APIs.
- For schema changes, add a migration under `database/migrations/`.
- For future skills, create directories under `agents/skills/<skill-name>/SKILL.md`.
- Keep workers resilient: catch per-item external API errors, collect them in returned summaries, and continue processing other games/tickers.
- Do not introduce dashboard code until the project intentionally enters Phase 7.
