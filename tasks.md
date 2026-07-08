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
- [x] `.env.example` added — on any new clone/machine, `cp .env.example .env` then pull `SUPABASE_URL` and the `service_role` key (not `anon` — worker writes need RLS bypass) from Supabase dashboard → Settings → API, plus fill in the other per-phase vars.

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
- [x] Apply `database/migrations/003_watchlist_sentiment_targets.sql` in Supabase SQL Editor
- [x] Apply `database/migrations/004_patch_events_source_url.sql` in Supabase SQL Editor
- [x] Apply `database/migrations/005_equity_signals.sql` in Supabase SQL Editor
- [x] Apply `database/migrations/006_patch_events_cadence_flags.sql` in Supabase SQL Editor (cadence status/baseline + monetization-without-content columns)
- [x] Apply `database/migrations/007_player_metrics_review_score_precision.sql` in Supabase SQL Editor (widens `review_score` to `numeric(5,2)` so a perfect 100.00 review score doesn't fail the upsert — added 2026-07-03 alongside the code fix in `steam_client.py`/`worker.py`; confirmed applied 2026-07-06 via `information_schema.columns` returning `(5, 2)`)

### RAWG backfill
- [x] Build standalone RAWG backfill script — `scripts/rawg_backfill.py` (resumable, `--dry-run` / `--limit` / `--offset` / `--fix-steam` flags)
- [x] Add bounded chunk mode for production-safe RAWG backfill runs: `python scripts/rawg_backfill.py --chunk-size 100`
- [ ] Run RAWG backfill against production DB in chunks: `python scripts/rawg_backfill.py --chunk-size 100 --dry-run` then without `--dry-run` (checked 2026-07-06: 1,400/4,017 games still missing `rawg_slug`, 2,295/4,017 missing `steam_app_id`; partial progress recorded in `.rawg_backfill_state.json` — 350 entries — but not complete)

### Phase 2 skill
- [x] Write `agents/skills/sentiment-analysis-methodology/SKILL.md` — encode VADER+LLM+ABSA hybrid framework

---

## Phase 3 — Studio & Financial Intelligence

### Patch Notes worker
- [x] Build `agents/workers/patch_notes/worker.py` — update cadence analysis → `patch_events`
- [x] Integrate Steam news API (`ISteamNews/GetNewsForApp`) per title
- [x] Add `web_fetch` for developer blogs and official patch pages — `agents/workers/patch_notes/blog_client.py` (RSS/Atom + HTML fallback, cached via `database/api_cache.py`, configured via `GAME_PATCH_PAGES`); merged into `worker.py`'s existing cadence loop
- [x] Implement patch classification taxonomy: hotfix / balance / content_drop / monetization / engine / other
- [x] Implement cadence baseline comparison (flag slowing or absent patches) — `_resolve_baseline_days()`/`_cadence_status()` in `worker.py`, genre-aware for live-service titles, persisted via migration `006_patch_events_cadence_flags.sql`
- [x] Implement monetization-without-content flag
- [x] Wire patch notes worker into `run_weekly.py`
- [x] Wire patch notes task in `agents/orchestrator/crew.py` (replace placeholder)

### Studio Intel enhancements
- [x] Add official Greenhouse / Lever / Ashby job-board API clients to `studio_intel` worker
- [x] Add Playwright fallback only for studios without hosted ATS boards
- [x] Add hiring-signal taxonomy: role-type spikes → intent mapping
- [x] Add distress indicator scoring (layoffs, exec departures, consolidation) — fixed 8-K item `2.06` mapping (was mislabeled `press_release`/medium, now `impairment`/high per the org-health-signal-analysis severity table); added `escalate_for_repeat_distress()` in `edgar_client.py` to bump severity to `high` when a studio has a prior layoffs/impairment signal in the trailing 12 months (`count_recent_studio_signals()` in `db_client.py`)

### Skills
- [x] Write `agents/skills/live-service-health-analysis/SKILL.md` — CCU/DAU/MAU KPI framework, retention benchmarks, genre baselines, bundled delta/rolling-avg script
- [x] Write `agents/skills/patch-cadence-analysis/SKILL.md` — update rhythm baselines, monetization flag logic, roadmap-adherence tracking
- [x] Write `agents/skills/org-health-signal-analysis/SKILL.md` — hiring taxonomy, distress indicators, leadership-stability index, acquisition/IPO detection
- [x] Write `agents/skills/equity-signal-mapping/SKILL.md` — studio→ticker resolution, materiality weighting, pre-earnings window logic, correlation tracking

---

## Phase 4 — Synthesis Agent & Briefing

- [x] Build `agents/synthesis/agent.py` — reads all Supabase worker outputs, produces weekly briefing → `weekly_briefings`
- [x] Implement convergence signal logic (multi-layer bearish/bullish scoring)
- [x] Implement divergence-opportunity logic (vocal-minority guard integration)
- [x] Implement confidence scoring for conflicting signals
- [x] Build `deep-dive-researcher` subagent dispatch (web access, returns short findings summary)
- [x] Write `agents/skills/investment-synthesis-framework/SKILL.md` — convergence/divergence rules, confidence scoring, briefing template
- [x] Integrate LangSmith tracing across all agent runs (`agents/tracing.py`, wired into `run_weekly.py`; opt-in, no-op without `LANGSMITH_API_KEY`)
- [x] Set up email delivery for weekly briefing (SendGrid or similar) — built via Resend's free tier per `docs/games-investment-platform-brief.md:344`; see `agents/synthesis/email_delivery.py` (opt-in, no-op without `RESEND_API_KEY`/`BRIEFING_EMAIL_TO`)
- [x] Wire synthesis agent into `run_weekly.py`

---

## Phase 5 — Portfolio Manager + Alpaca Execution

- [x] Create Alpaca paper trading account and generate API keys — verified 2026-07-06 via `GET /v2/account` (status `ACTIVE`, $100K cash/portfolio value)
- [x] Add `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_BASE_URL` to `.env` and GitHub secrets — present in `.env` and confirmed by user in GitHub secrets 2026-07-06
- [x] Configure Alpaca MCP server for Portfolio Manager tool calls — migrated `manager.py` + `execution_agent.py` to the Claude Agent SDK with **custom in-process MCP servers** (`agents/portfolio/alpaca_mcp.py`), not the official alpacahq server (its bare `place_order` would bypass the approval guard). Portfolio Manager (Opus, `claude-opus-4-8`) is pinned via `allowed_tools` to one read-only tool `mcp__alpaca-readonly__get_account_state`; Execution Agent dispatches deterministically (no LLM) through the guarded `alpaca-execution-guarded` server whose `place_approved_order` tool re-runs the Supabase `status='approved'` guard. Added `claude-agent-sdk` to `requirements.txt`; tests: `tests/test_portfolio_manager.py` (updated) + `tests/test_execution_agent.py` (new), all passing with zero live calls.
- [x] Build `agents/portfolio/manager.py` — reads weekly briefing + current Alpaca positions → produces trade plan → `trade_plans`
- [x] Build minimal trade-plan approval UI or CLI flow before enabling execution
- [x] Write `agents/skills/position-sizing-and-risk/SKILL.md` — max position size %, conviction-tier sizing, concentration limits, stop-loss / thesis-invalidation rules, benchmark-relative framing
- [x] Build `agents/portfolio/execution_agent.py` — thin subagent, Alpaca tools only, reads approved `trade_orders` and places them
- [x] Implement in-tool Alpaca pre-trade guard — `place_approved_order()` re-reads `status = 'approved'` in Supabase before placing orders
- [x] Implement Returns Tracker — fetch Alpaca portfolio state weekly, compute return vs. S&P 500, write to `portfolio_snapshots` — `agents/portfolio/returns_tracker.py`; tests: `tests/test_returns_tracker.py`, all passing with zero live calls
- [x] Wire portfolio manager + execution agent into `run_weekly.py` — all three Phase 5 modules (Portfolio Manager → Execution Agent → Returns Tracker) wired between synthesis and the CrewAI placeholder, in that order, with `None`-guarded summary prints; plus the `weekly.yml` fix adding `ALPACA_API_KEY`/`ALPACA_SECRET_KEY`/`ALPACA_BASE_URL` to the CI env block (2026-07-06)
- [x] Validate full pipeline end-to-end on paper trading account — ran live 2026-07-06 against the 2026-07-06 weekly briefing: Portfolio Manager produced a real `pending` trade plan (0 orders, defensive posture — briefing had 0 confirmed opportunities, correctly declined to fabricate trades), reviewed via `scripts/review_trade_plans.py list`, Execution Agent ran cleanly against live Supabase/Alpaca (0 approved orders found, no errors), Returns Tracker wrote the real first `portfolio_snapshots` row ($100,000 value/cash, 0.0% return/benchmark as the inception baseline). Confirmed working end-to-end: MCP tool calls, the approval-status guard, and the DB writes. Not yet exercised live: the actual Alpaca order-placement call (`place_approved_order`), since no plan has proposed a real trade yet — will get its first live test the first week synthesis surfaces a confirmed, materiality-mapped opportunity.

---

## Phase 6 — Discovery Agent

- [ ] Build `agents/workers/discovery/worker.py` — scans for new watchlist candidates → `watchlist_proposals`
- [ ] Integrate Steam trending / top-CCU source (top 50 by CCU / reviews, filter untracked)
- [ ] Integrate IGDB upcoming release calendar (high-hype titles releasing in 60 days)
- [ ] Integrate SEC EDGAR scan for new IPO/acquisition filings
- [ ] Integrate Reddit mention-volume spike detection for untracked titles
- [ ] Implement Claude rationale generation per proposal (investment-relevance justification)
- [ ] Implement false-positive learning (read rejection log to tighten criteria)
- [x] Write `agents/skills/watchlist-relevance-scoring/SKILL.md` — relevance criteria rubric, trigger thresholds, rationale template
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
- [x] Add LangSmith tracing to all agent runs (token spend per subagent, full trace tree) — `agents/tracing.py` + `run_weekly.py` root span, per-worker `traced_step` spans, `wrap_anthropic` on the ABSA client for token usage
- [ ] Add per-subagent token-spend logging via lifecycle hooks
- [ ] Add graceful error recovery to workers (retry on transient API errors, degrade rather than crash)
- [x] Lock model per-agent in all crew/agent configs — verify no agent defaults to most capable (audited 2026-07-03: `crew.py` workers on `claude-sonnet-4-6`, orchestrator on `claude-opus-4-8`, ABSA on `claude-haiku-4-5-20251001`, all explicit; synthesis/execution agents make no direct LLM calls. `agents/portfolio/manager.py` is now implemented and locked to `claude-opus-4-8` per the Opus-class tier. Re-confirmed 2026-07-06 after the Agent SDK/MCP migration: `manager.py` runs an Agent SDK session still locked to `claude-opus-4-8`; `agents/portfolio/execution_agent.py` remains a deterministic dispatch loop with **no direct LLM calls** — it only invokes the guarded Alpaca MCP tool in-process, so the "execution agents make no direct LLM calls" claim still holds. If a reasoning step were ever added there, Sonnet-class `claude-sonnet-4-6` would be the correct tier.)
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

---

## Reddit Alternate-Egress Remediation — 2026-07-06

- [x] Confirm live diagnosis: Reddit's unauthenticated `.json` path returns a static `403` WAF block ("You've been blocked by network security") from GitHub Actions' datacenter IPs, on every endpoint tried, 100% of runs since inception — an IP-reputation block, not rate-limiting.
- [x] Add `ProxiedJsonRedditSource` and `OAuthRedditSource` to `reddit_source.py`, gated behind `REDDIT_PROXY_URL` and `REDDIT_CLIENT_ID`+`REDDIT_CLIENT_SECRET`+`REDDIT_REFRESH_TOKEN` respectively; extract shared listing/comment/search parsers so both reuse `JsonRedditSource`'s parsing logic.
- [x] Rewrite `build_reddit_source()` as an env-var-driven `FirstAvailableRedditSource` chain (OAuth → Proxy → unauthenticated, each with its own `api_cache` namespace); add `build_subreddit_resolver()` and use it in `worker.py`'s `_resolve_subreddit_for_game()` instead of a hardcoded `JsonRedditSource()`.
- [x] Add `tests/test_reddit_source.py` — first-ever test coverage for `reddit_source.py`/`reddit_cache.py`/`worker.py`'s Reddit paths, all mocked, zero live network calls (36 tests, all passing; full suite 124/124 passing).
- [x] Update `.env.example`, `CLAUDE.md`, `docs/reddit_source_adapter.md`, `docs/data-source-risk-register.md` to document the new optional vars and the confirmed-permanent-block framing (previously described as partial/intermittent).
### User follow-up — activating a real egress path (cannot be self-certified without credentials)

Pick **one** path (or both — OAuth is tried first if both are configured). Proxy is faster to get working; OAuth is free but has an uncertain approval timeline.

**Option A — Proxy (faster to activate):**
- [ ] Sign up for a proxy service that offers a residential or "clean"/non-datacenter IP pool (a plain datacenter proxy will likely get the same WAF block GitHub Actions already gets — the whole point is a cleaner IP reputation). Get an HTTP/HTTPS proxy URL in the form `http://user:pass@host:port`.
- [ ] Set `REDDIT_PROXY_URL` to that value in `.env` (local testing) and as a GitHub Actions repo secret (Settings → Secrets and variables → Actions → New repository secret).
- [ ] Add `REDDIT_PROXY_URL: ${{ secrets.REDDIT_PROXY_URL }}` to `.github/workflows/weekly.yml`'s `env:` block (not yet done — this wiring step still needs to happen once you have a value to wire in).

**Option B — Reddit OAuth (free, but gated behind manual approval):**
- [ ] Go to https://www.reddit.com/prefs/apps → create an app. As of the Nov 2025 "Responsible Builder Policy," new app creation goes through a manual review queue, not instant self-serve — there's no published SLA, so expect a wait with no guaranteed outcome.
- [ ] Once approved, choose "script" type to get a `client_id` (shown under the app name) and `client_secret`.
- [ ] Obtain a **refresh token** (not a short-lived access token) via Reddit's OAuth2 `authorization_code` flow with `duration=permanent` — this is a one-time manual step (visit an authorize URL, approve access, exchange the returned code for tokens using your client_id/secret). `OAuthRedditSource` expects to reuse this refresh token indefinitely, not re-run this flow on every deploy.
- [ ] Set `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_REFRESH_TOKEN` in `.env` and as GitHub Actions repo secrets.
- [ ] Add all three to `.github/workflows/weekly.yml`'s `env:` block (not yet done).

**Either way, once configured:**
- [ ] Run the sentiment worker locally first as a cheap smoke test: `python -c "import sys; sys.path.insert(0, '.'); from dotenv import load_dotenv; load_dotenv(); from agents.workers.sentiment import worker; import json; print(json.dumps(worker.run(), indent=2))"` and confirm `reddit_blocked_count` is `0` (or much lower than the game count) instead of matching the total.
- [ ] Confirm in Supabase: `select count(*) from sentiment_snapshots where source = 'reddit' and date = current_date;` returns > 0 — the first time this has ever been true.
- [ ] Trigger the real GitHub Actions workflow (`workflow_dispatch` or wait for the Monday cron) and re-check the same query plus the run's log for the `[sentiment] Reddit blocked ...` line (should be absent or much reduced).
- [ ] Once confirmed working, check this whole subsection off and update the `CLAUDE.md`/risk-register notes that currently say the unauthenticated path is 100%-blocked, since that framing will be stale.

---

## Supabase Data-Gap Audit — 2026-07-06

Full-table null/coverage audit against the live Supabase project. Row counts at time of audit: `games` 4,017, `watchlist` 4,017 (all active), `player_metrics` 1,323, `sentiment_snapshots` 971, `patch_events` 794, `portfolio_positions_context` 15, `positions` 0, `trade_orders` 0, `watchlist_proposals` 0.

- [ ] Populate `player_metrics.peak_players_24h` — NULL on all 1,323 existing rows; `agents/workers/market_player/worker.py` hardcodes it to `None` at write time (no Steam endpoint currently queried supplies a true 24h peak). Either source it (Steam's `appdetails`/SteamSpy-style peak field, or compute a rolling 24h max from repeated CCU polls) or drop the column if it's not going to be filled.
- [ ] Populate `portfolio_positions_context.signal_score` — NULL on all 15 existing rows; composite health score column has no writer anywhere in `financial_overlay`/`db_client.py`. Decide the composite formula (per `agents/skills/equity-signal-mapping/SKILL.md`) and wire it into `agents/workers/financial_overlay/worker.py`, or drop the column.
- [ ] Investigate the `positions` table — 0 rows since inception, no readers or writers in `agents/portfolio/` despite the schema supporting per-position qty/entry price/unrealized P&L/holding period. Either wire the Returns Tracker (or a new step) to populate it from Alpaca's live positions, or remove it from `schema.sql` if per-position tracking is being deferred to Phase 7's dashboard reading Alpaca directly.
- [ ] Close the watchlist coverage gap: 3,504/4,017 (87%) active watchlist games have never received a `player_metrics` row, and 3,498/4,017 (87%) never received a `sentiment_snapshots` row — this is mostly downstream of the `games.steam_app_id` gap (2,028/4,017 NULL, tracked under the RAWG backfill task above) and the `watchlist.subreddit` backlog (3,017/4,017 unresolved, tracked under the Reddit remediation section above). No new work item beyond finishing those two backfills, but worth re-running this coverage check after both land to confirm the gap actually closes rather than just shifting.
- [ ] Minor/likely-benign nulls to spot-check rather than fix outright: `player_metrics.review_score`/`review_count` NULL on 42/1,323 rows and `review_velocity` NULL on 78/1,323 (games with zero Steam reviews — confirm this is the actual cause, not a silent Steam API failure being swallowed).
