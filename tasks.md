# Games Industry Investment Platform — Task Tracker

> Phases map to the build plan in `docs/games-investment-platform-brief.md`.
> Workers / agents reference `docs/agent-components-plan.md` for internals spec.

---

## Phase 1 — Foundation + Watchlist Seeding

- [x] Supabase project created and schema applied (`database/schema.sql`)
- [x] Watchlist seeding agent built — 3,017 games from 25 studios (`agents/orchestrator/seed_watchlist.py`)
- [x] CrewAI crew scaffolded with placeholder agents (`agents/orchestrator/crew.py`)
- [x] GitHub Actions weekly cron wired (`.github/workflows/weekly.yml`)
- [ ] Add GitHub Actions repo secrets: `ANTHROPIC_API_KEY`, `SUPABASE_URL`, `SUPABASE_KEY`, `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, `RAWG_API_KEY`, `STEAM_API_KEY`

---

## Phase 2 — Sentiment Layer

### Workers built (need end-to-end testing)
- [x] `agents/workers/market_player/worker.py` — official Steam CCU + cached appreviews metrics → `player_metrics`
- [x] `agents/workers/market_player/steam_client.py` — Steam API client
- [x] `agents/workers/market_player/igdb_client.py` — IGDB API client
- [x] `agents/workers/market_player/rawg_client.py` — RAWG API client
- [x] `agents/workers/financial_overlay/worker.py` — Alpaca/yfinance equity snapshots → `equity_signals`
- [x] `agents/workers/financial_overlay/yfinance_client.py` — yfinance wrapper
- [x] `agents/workers/studio_intel/worker.py` — SEC EDGAR 8-K signals → `studio_signals`
- [x] `agents/workers/studio_intel/edgar_client.py` — EDGAR API client
- [x] `run_weekly.py` wires direct workers + synthesis + CrewAI crew

### Testing
- [x] End-to-end test `market_player` worker (run against live Supabase, verify rows in `player_metrics`)
- [x] End-to-end test `financial_overlay` worker (verify rows in `equity_signals`)
- [x] End-to-end test `studio_intel` worker (verify rows in `studio_signals`)
- [x] Run legacy `run_weekly.py` pipeline and confirm no crashes
- [ ] Run updated `run_weekly.py` pipeline after applying migrations `003`-`005`

### Sentiment worker
- [x] Build `agents/workers/sentiment/worker.py` — Reddit/Steam/YouTube sentiment → `sentiment_snapshots`
- [x] Integrate Reddit via unauthenticated `.json` adapter (`reddit_source.py`, `reddit_cache.py`) — rate-limited, Supabase-cached, graceful degradation; no OAuth credentials required
- [x] Integrate Steam reviews API (`steam_reviews_client.py`)
- [x] Implement VADER baseline pass with engagement weighting (`vader_scorer.py`)
- [x] Implement Claude Haiku ABSA — extract aspect→polarity pairs (`absa_client.py`)
- [x] Implement thematic clustering — top 3 aspects by mention_count (in `absa_client.py`)
- [x] Implement preliminary lagged sentiment flag (`divergence.py`); authoritative divergence now belongs to synthesis
- [x] Implement vocal-minority guard — engagement-weighted VADER score + divergence note
- [x] Wire sentiment worker into `run_weekly.py`
- [x] Wire sentiment task in `agents/orchestrator/crew.py` (`task_sentiment`)
- [x] Apply `database/migrations/001_sentiment_snapshots_unique.sql` in Supabase SQL Editor (required before first run)
- [x] Apply `database/migrations/002_api_cache.sql` in Supabase SQL Editor (required before first Reddit adapter run)
- [ ] Apply `database/migrations/003_watchlist_sentiment_targets.sql` in Supabase SQL Editor
- [ ] Apply `database/migrations/004_patch_events_source_url.sql` in Supabase SQL Editor
- [ ] Apply `database/migrations/005_equity_signals.sql` in Supabase SQL Editor

### RAWG backfill
- [x] Build standalone RAWG backfill script — `scripts/rawg_backfill.py` (resumable, `--dry-run` / `--limit` / `--offset` / `--fix-steam` flags)
- [x] Add bounded chunk mode for production-safe RAWG backfill runs: `python scripts/rawg_backfill.py --chunk-size 100`
- [ ] Run RAWG backfill against production DB in chunks: `python scripts/rawg_backfill.py --chunk-size 100 --dry-run` then without `--dry-run`

### Phase 2 skill
- [x] Write `agents/skills/sentiment-analysis-methodology/SKILL.md` — encode VADER+LLM+ABSA hybrid framework

---

## Phase 3 — Studio & Financial Intelligence

### Patch Notes worker
- [x] Build `agents/workers/patch_notes/worker.py` — update cadence analysis → `patch_events`
- [x] Integrate Steam news API (`ISteamNews/GetNewsForApp`) per title
- [ ] Add `web_fetch` for developer blogs and official patch pages
- [x] Implement patch classification taxonomy: hotfix / balance / content_drop / monetization / engine / other
- [ ] Implement cadence baseline comparison (flag slowing or absent patches)
- [x] Implement monetization-without-content flag
- [x] Wire patch notes worker into `run_weekly.py`
- [x] Wire patch notes task in `agents/orchestrator/crew.py` (replace placeholder)

### Studio Intel enhancements
- [x] Add official Greenhouse / Lever / Ashby job-board API clients to `studio_intel` worker
- [x] Add Playwright fallback only for studios without hosted ATS boards
- [x] Add hiring-signal taxonomy: role-type spikes → intent mapping
- [ ] Add distress indicator scoring (layoffs, exec departures, consolidation)

### Skills
- [ ] Write `agents/skills/live-service-health-analysis/SKILL.md` — CCU/DAU/MAU KPI framework, retention benchmarks, genre baselines, bundled delta/rolling-avg script
- [ ] Write `agents/skills/patch-cadence-analysis/SKILL.md` — update rhythm baselines, monetization flag logic, roadmap-adherence tracking
- [ ] Write `agents/skills/org-health-signal-analysis/SKILL.md` — hiring taxonomy, distress indicators, leadership-stability index, acquisition/IPO detection
- [ ] Write `agents/skills/equity-signal-mapping/SKILL.md` — studio→ticker resolution, materiality weighting, pre-earnings window logic, correlation tracking

---

## Phase 4 — Synthesis Agent & Briefing

- [x] Build `agents/synthesis/agent.py` — reads all Supabase worker outputs, produces weekly briefing → `weekly_briefings`
- [x] Implement convergence signal logic (multi-layer bearish/bullish scoring)
- [x] Implement divergence-opportunity logic (vocal-minority guard integration)
- [x] Implement confidence scoring for conflicting signals
- [ ] Build `deep-dive-researcher` subagent dispatch (web access, returns short findings summary)
- [ ] Write `agents/skills/investment-synthesis-framework/SKILL.md` — convergence/divergence rules, confidence scoring, briefing template
- [ ] Integrate LangSmith tracing across all agent runs
- [ ] Set up email delivery for weekly briefing (SendGrid or similar)
- [x] Wire synthesis agent into `run_weekly.py`

---

## Phase 5 — Portfolio Manager + Alpaca Execution

- [ ] Create Alpaca paper trading account and generate API keys
- [ ] Add `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_BASE_URL` to `.env` and GitHub secrets
- [ ] Configure Alpaca MCP server for Portfolio Manager tool calls
- [ ] Build `agents/portfolio/manager.py` — reads weekly briefing + current Alpaca positions → produces trade plan → `trade_plans`
- [ ] Build minimal trade-plan approval UI or CLI flow before enabling execution
- [ ] Write `agents/skills/position-sizing-and-risk/SKILL.md` — max position size %, conviction-tier sizing, concentration limits, stop-loss / thesis-invalidation rules, benchmark-relative framing
- [x] Build `agents/portfolio/execution_agent.py` — thin subagent, Alpaca tools only, reads approved `trade_orders` and places them
- [x] Implement in-tool Alpaca pre-trade guard — `place_approved_order()` re-reads `status = 'approved'` in Supabase before placing orders
- [ ] Implement Returns Tracker — fetch Alpaca portfolio state weekly, compute return vs. S&P 500, write to `portfolio_snapshots`
- [ ] Wire portfolio manager + execution agent into `run_weekly.py`
- [ ] Validate full pipeline end-to-end on paper trading account

---

## Phase 6 — Discovery Agent

- [ ] Build `agents/workers/discovery/worker.py` — scans for new watchlist candidates → `watchlist_proposals`
- [ ] Integrate Steam trending / top-CCU source (top 50 by CCU / reviews, filter untracked)
- [ ] Integrate IGDB upcoming release calendar (high-hype titles releasing in 60 days)
- [ ] Integrate SEC EDGAR scan for new IPO/acquisition filings
- [ ] Integrate Reddit mention-volume spike detection for untracked titles
- [ ] Implement Claude rationale generation per proposal (investment-relevance justification)
- [ ] Implement false-positive learning (read rejection log to tighten criteria)
- [ ] Write `agents/skills/watchlist-relevance-scoring/SKILL.md` — relevance criteria rubric, trigger thresholds, rationale template
- [ ] Wire discovery worker into `run_weekly.py`
- [ ] Wire discovery task in `agents/orchestrator/crew.py` (replace placeholder)

---

## Phase 7 — Dashboard Polish

- [ ] Scaffold Next.js 16 app in `dashboard/` using Shadcn Admin starter
- [ ] Configure Supabase client (read-only API key for frontend)
- [ ] Build portfolio overview page — current positions, total return vs. S&P 500 benchmark
- [ ] Build per-game signal cards — CCU trend, sentiment score, patch cadence indicator
- [ ] Build Recharts sentiment trend charts (week-over-week per game)
- [ ] Build weekly briefing feed page — latest briefing, reasoning log
- [ ] Build watchlist proposal review queue — approve/reject UI with one-click actions
- [ ] Build trade plan approval UI — per-trade approve/reject, bulk approve
- [ ] Build trade history log with original Claude rationale per trade
- [ ] Build cumulative return chart + position breakdown view
- [ ] Deploy dashboard to Vercel (Hobby tier)
- [ ] Configure Vercel environment variables (Supabase URL + anon key)

---

## Cross-Cutting / Infrastructure

- [ ] Add `LANGSMITH_API_KEY` and `LANGSMITH_PROJECT` to `.env` and GitHub secrets
- [ ] Add LangSmith tracing to all agent runs (token spend per subagent, full trace tree)
- [ ] Add per-subagent token-spend logging via lifecycle hooks
- [ ] Add graceful error recovery to workers (retry on transient API errors, degrade rather than crash)
- [ ] Lock model per-agent in all crew/agent configs — verify no agent defaults to most capable
- [ ] Add `YOUTUBE_API_KEY` to `.env` and GitHub Actions secrets once the YouTube Data API collector is enabled
- [ ] Add `database/migrations/` pattern — write a migration for any future schema change rather than modifying `schema.sql` directly

---

## Updated Docs Compliance Review — 2026-07-01

### Step 1 — Source-of-truth cleanup
- [x] Update `tasks.md`, `CLAUDE.md`, and any repo guidance that still points to deleted `project context files/` paths; the current planning set now lives under `docs/`.
- [x] Remove stale PRAW/OAuth guidance from `CLAUDE.md` and task lists; Reddit collection now uses unauthenticated public `.json` endpoints through `reddit_source.py` plus `api_cache`.
- [x] Remove or defer `X_BEARER_TOKEN` setup from MVP tasks; updated docs classify X/Twitter as Tier 3 deferred and say to try Bluesky before paid X access.
- [x] Remove Discord scraping references from dependency comments and task language; Discord is Tier 4 excluded in the risk register.
- [x] Reorder task phases to match `docs/games-investment-platform-brief.md`: Phase 5 = Portfolio Manager + Alpaca Execution, Phase 6 = Discovery Agent, Phase 7 = Dashboard Polish.

### Step 2 — Watchlist and seeding alignment
- [x] Add a migration for `watchlist.sentiment_tier` and backfill tier assignments so Reddit collection can distinguish Tier A full post/comment coverage from tail listing-only coverage.
- [x] Persist subreddit mappings or another explicit community target per tracked game instead of resolving every subreddit opportunistically during the sentiment run.
- [x] Update the seeding path to assign sentiment tiers at seed time and use the shared `watchlist-relevance-scoring` rubric once that skill exists.
- [x] Confirm whether SteamSpy remains acceptable for seed-time trending discovery; replaced with Steam official most-played/app-list APIs plus IGDB/RAWG enrichment.

### Step 3 — Market and player data alignment
- [x] Replace SteamSpy `appdetails` usage in `market_player` with Steam's official `ISteamUserStats/GetNumberOfCurrentPlayers` for current CCU snapshots.
- [x] Wrap Steam `appreviews` access in a Tier-2 adapter with rate limiting, `api_cache`, and stale fallback before using it for review scores, review velocity, or review text.
- [x] Update `.github/workflows/weekly.yml` to install from `requirements.txt` or otherwise include all runtime dependencies (`yfinance`, `vaderSentiment`, etc.).

### Step 4 — Sentiment layer alignment
- [x] Add the YouTube Data API comment collector described in the docs; avoid scraping and avoid quota-expensive `search.list` discovery.
- [x] Use `watchlist.sentiment_tier` to decide when Reddit comments are fetched; current worker only scores listing posts and never calls `fetch_comments()`.
- [x] Move authoritative divergence logic out of the sentiment worker and into the Phase 4 synthesis agent; any sentiment-side flag must be clearly labeled as lagged/preliminary.
- [x] Revise `agents/skills/sentiment-analysis-methodology/SKILL.md` so it matches the updated docs: sentiment emits clean ABSA inputs, while synthesis owns same-week text-vs-quant divergence.
- [x] Align `SupabaseRedditCache.get()` with the documented `limit(1).execute()` lookup instead of `maybe_single()` if client-version zero-row behavior becomes noisy.

### Step 5 — Patch notes and studio intelligence alignment
- [x] Replace the existing "Steam RSS" patch task with official `ISteamNews/GetNewsForApp` collection.
- [x] Keep Discord excluded from patch-note collection; use Steam news plus official developer blogs instead.
- [x] Replace the "LinkedIn / Greenhouse" studio-intel task with official Greenhouse, Lever, and Ashby job-board API clients; LinkedIn remains excluded.
- [x] Add Playwright only as a low-volume fallback for studios without hosted ATS boards, with per-studio failures isolated from the weekly run.

### Step 6 — Financial overlay alignment
- [x] Add an `equity_signals` migration or compatibility view and update `financial_overlay`/`db_client` writes away from the older `portfolio_positions_context` name.
- [x] Wrap yfinance in the same Tier-2 adapter/cache/stale-fallback pattern mandated by the risk register.
- [x] Add Alpaca Market Data as the official price and SPY benchmark source, with yfinance limited to fundamentals-adjacent fields or fallback behavior.
- [x] Implement materiality-aware studio-to-ticker mapping instead of deduplicating each ticker to the first studio row encountered.

### Step 7 — Synthesis alignment
- [x] Build `agents/synthesis/agent.py` as the first place that reads same-week worker outputs together.
- [x] Implement the authoritative same-week divergence check in synthesis using sentiment, player metrics, review velocity, and patch cadence.
- [x] Persist synthesis outputs to `weekly_briefings` with a reasoning log and structured portfolio update/opportunity/risk fields.

### Step 8 — Portfolio, discovery, dashboard, and ops alignment
- [x] Move Portfolio Manager + Alpaca Execution ahead of Discovery/Dashboard polish in the task tracker to preserve the updated resume-complete cut line.
- [x] Implement the Alpaca order pre-trade guard inside the order-placement tool itself; lifecycle hooks can mirror it later but must not be the only guard.
- [x] Add the midweek Supabase keepalive GitHub Actions job required by the docs to avoid free-tier project pauses.
- [x] Add a scheduled `api_cache` pruning step or Supabase `pg_cron` job for the 14-day cache retention policy.
