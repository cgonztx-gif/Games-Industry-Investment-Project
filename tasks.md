# Games Industry Investment Platform — Task Tracker

> Phases map to the build plan in `project context files/games-investment-platform-brief.md`.
> Workers / agents reference `project context files/agent-components-plan.md` for internals spec.

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
- [x] `agents/workers/market_player/worker.py` — SteamSpy CCU + review metrics → `player_metrics`
- [x] `agents/workers/market_player/steam_client.py` — Steam API client
- [x] `agents/workers/market_player/igdb_client.py` — IGDB API client
- [x] `agents/workers/market_player/rawg_client.py` — RAWG API client
- [x] `agents/workers/financial_overlay/worker.py` — yfinance equity snapshots → `portfolio_positions_context`
- [x] `agents/workers/financial_overlay/yfinance_client.py` — yfinance wrapper
- [x] `agents/workers/studio_intel/worker.py` — SEC EDGAR 8-K signals → `studio_signals`
- [x] `agents/workers/studio_intel/edgar_client.py` — EDGAR API client
- [x] `run_weekly.py` wires all three workers + CrewAI crew

### Testing
- [ ] End-to-end test `market_player` worker (run against live Supabase, verify rows in `player_metrics`)
- [x] End-to-end test `financial_overlay` worker (verify rows in `portfolio_positions_context`)
- [x] End-to-end test `studio_intel` worker (verify rows in `studio_signals`)
- [x] Run full `run_weekly.py` pipeline and confirm no crashes

### Sentiment worker
- [x] Build `agents/workers/sentiment/worker.py` — Reddit/Steam review sentiment → `sentiment_snapshots`
- [x] Integrate `praw` for Reddit API (`reddit_client.py`) — degrades to Steam-only if credentials absent
- [x] Integrate Steam reviews API (`steam_reviews_client.py`)
- [x] Implement VADER baseline pass with engagement weighting (`vader_scorer.py`)
- [x] Implement Claude Haiku ABSA — extract aspect→polarity pairs (`absa_client.py`)
- [x] Implement thematic clustering — top 3 aspects by mention_count (in `absa_client.py`)
- [x] Implement divergence check — text sentiment vs. review-count signal (`divergence.py`)
- [x] Implement vocal-minority guard — engagement-weighted VADER score + divergence note
- [x] Wire sentiment worker into `run_weekly.py`
- [x] Wire sentiment task in `agents/orchestrator/crew.py` (`task_sentiment`)
- [ ] Apply `database/migrations/001_sentiment_snapshots_unique.sql` in Supabase SQL Editor (required before first run)

### RAWG backfill
- [x] Build standalone RAWG backfill script — `scripts/rawg_backfill.py` (resumable, `--dry-run` / `--limit` / `--offset` / `--fix-steam` flags)
- [ ] Run RAWG backfill against production DB: `python scripts/rawg_backfill.py --dry-run` then without flag

### Phase 2 skill
- [ ] Write `agents/skills/sentiment-analysis-methodology/SKILL.md` — encode VADER+LLM+ABSA hybrid framework

---

## Phase 3 — Studio & Financial Intelligence

### Patch Notes worker (not yet built)
- [ ] Build `agents/workers/patch_notes/worker.py` — update cadence analysis → `patch_events`
- [ ] Integrate Steam RSS update feeds per title
- [ ] Add `web_fetch` for developer blogs and official patch pages
- [ ] Implement patch classification taxonomy: hotfix / balance / content_drop / monetization / engine / other
- [ ] Implement cadence baseline comparison (flag slowing or absent patches)
- [ ] Implement monetization-without-content flag
- [ ] Wire patch notes worker into `run_weekly.py`
- [ ] Wire patch notes task in `agents/orchestrator/crew.py` (replace placeholder)

### Studio Intel enhancements
- [ ] Add Playwright-based job posting scraper (LinkedIn / Greenhouse) to `studio_intel` worker
- [ ] Add hiring-signal taxonomy: role-type spikes → intent mapping
- [ ] Add distress indicator scoring (layoffs, exec departures, consolidation)

### Skills
- [ ] Write `agents/skills/live-service-health-analysis/SKILL.md` — CCU/DAU/MAU KPI framework, retention benchmarks, genre baselines, bundled delta/rolling-avg script
- [ ] Write `agents/skills/patch-cadence-analysis/SKILL.md` — update rhythm baselines, monetization flag logic, roadmap-adherence tracking
- [ ] Write `agents/skills/org-health-signal-analysis/SKILL.md` — hiring taxonomy, distress indicators, leadership-stability index, acquisition/IPO detection
- [ ] Write `agents/skills/equity-signal-mapping/SKILL.md` — studio→ticker resolution, materiality weighting, pre-earnings window logic, correlation tracking

---

## Phase 4 — Synthesis Agent & Briefing

- [ ] Build `agents/synthesis/agent.py` — reads all Supabase worker outputs, produces weekly briefing → `weekly_briefings`
- [ ] Implement convergence signal logic (multi-layer bearish/bullish scoring)
- [ ] Implement divergence-opportunity logic (vocal-minority guard integration)
- [ ] Implement confidence scoring for conflicting signals
- [ ] Build `deep-dive-researcher` subagent dispatch (web access, returns short findings summary)
- [ ] Write `agents/skills/investment-synthesis-framework/SKILL.md` — convergence/divergence rules, confidence scoring, briefing template
- [ ] Integrate LangSmith tracing across all agent runs
- [ ] Set up email delivery for weekly briefing (SendGrid or similar)
- [ ] Wire synthesis agent into `run_weekly.py`

---

## Phase 5 — Discovery Agent

- [ ] Build `agents/workers/discovery/worker.py` — scans for new watchlist candidates → `watchlist_proposals`
- [ ] Integrate Steam trending chart scrape (top 50 by CCU / reviews, filter untracked)
- [ ] Integrate IGDB upcoming release calendar (high-wishlist titles releasing in 60 days)
- [ ] Integrate SEC EDGAR scan for new IPO/acquisition filings
- [ ] Integrate Reddit/X mention-volume spike detection for untracked titles
- [ ] Implement Claude rationale generation per proposal (investment-relevance justification)
- [ ] Implement false-positive learning (read rejection log to tighten criteria)
- [ ] Write `agents/skills/watchlist-relevance-scoring/SKILL.md` — relevance criteria rubric, trigger thresholds, rationale template
- [ ] Wire discovery worker into `run_weekly.py`
- [ ] Wire discovery task in `agents/orchestrator/crew.py` (replace placeholder)

---

## Phase 6 — Dashboard

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

## Phase 7 — Portfolio Manager + Alpaca Execution

- [ ] Create Alpaca paper trading account and generate API keys
- [ ] Add `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_BASE_URL` to `.env` and GitHub secrets
- [ ] Configure Alpaca MCP server for Portfolio Manager tool calls
- [ ] Build `agents/portfolio/manager.py` — reads weekly briefing + current Alpaca positions → produces trade plan → `trade_plans`
- [ ] Write `agents/skills/position-sizing-and-risk/SKILL.md` — max position size %, conviction-tier sizing, concentration limits, stop-loss / thesis-invalidation rules, benchmark-relative framing
- [ ] Build `agents/portfolio/execution_agent.py` — thin subagent, Alpaca tools only, reads approved `trade_orders` and places them
- [ ] Implement `before-tool-call` lifecycle hook — hard-block any Alpaca order without `status = 'approved'` in Supabase
- [ ] Implement Returns Tracker — fetch Alpaca portfolio state weekly, compute return vs. S&P 500, write to `portfolio_snapshots`
- [ ] Wire portfolio manager + execution agent into `run_weekly.py`
- [ ] Validate full pipeline end-to-end on paper trading account

---

## Cross-Cutting / Infrastructure

- [ ] Add `LANGSMITH_API_KEY` and `LANGSMITH_PROJECT` to `.env` and GitHub secrets
- [ ] Add LangSmith tracing to all agent runs (token spend per subagent, full trace tree)
- [ ] Add per-subagent token-spend logging via lifecycle hooks
- [ ] Add graceful error recovery to workers (retry on transient API errors, degrade rather than crash)
- [ ] Lock model per-agent in all crew/agent configs — verify no agent defaults to most capable
- [ ] Add `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` to `.env` and GitHub Actions secrets (sentiment worker is built; credentials needed to enable Reddit collection)
- [ ] Add `X_BEARER_TOKEN` to `.env` once X integration is built
- [ ] Add `database/migrations/` pattern — write a migration for any future schema change rather than modifying `schema.sql` directly
