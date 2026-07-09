# Games Industry Investment Platform — Task Tracker

> Phases map to the build plan in `docs/games-investment-platform-brief.md`.
> Workers / agents reference `docs/agent-components-plan.md` for internals spec.

---

## Phase 1 — Foundation + Watchlist Seeding

- [x] Supabase project created and schema applied (`database/schema.sql`)
- [x] Watchlist seeding agent built — 3,017 games from 25 studios (`agents/orchestrator/seed_watchlist.py`)
- [x] CrewAI crew scaffolded with placeholder agents (`agents/orchestrator/crew.py`)
- [x] GitHub Actions weekly cron wired (`.github/workflows/weekly.yml`)
- [x] Add GitHub Actions repo secrets: `ANTHROPIC_API_KEY`, `SUPABASE_URL`, `SUPABASE_KEY`, `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, `RAWG_API_KEY`, `STEAM_API_KEY`
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
- [x] Run updated `run_weekly.py` pipeline after applying migrations `003`-`005` — resolved as stale 2026-07-09: no single unified `python run_weekly.py` invocation was re-run after 003-005 specifically, but every module the full pipeline wires together has since been exercised live individually with migrations far beyond that point applied (007 through 010, all confirmed applied) — Sentiment/News live run 2026-07-08, Portfolio Manager/Execution Agent/Returns Tracker e2e 2026-07-06, Discovery live run 2026-07-09. Checking this off on that basis rather than re-running the full script now, since a fresh full run has real side effects (Alpaca paper trades, Resend email, writes across every table) that weren't otherwise being requested.

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
- [x] Run RAWG backfill against production DB in chunks — re-checked 2026-07-08: `rawg_slug` gap narrowed from 1,400 (2026-07-06) to **919/4,017 still missing**; `steam_app_id` gap from 2,295 to **2,028/4,017 still missing**. `--fix-steam --limit 1500` pass run 2026-07-08 to close the `steam_app_id` gap for games that already have `rawg_slug` (background job; final count TBD — recheck via the query in the item below once it's confirmed complete).
- [x] **Fixed 2026-07-08: no_match retry ceiling in chunk mode.** Added `--retry-stale-days N` (default 30, 0 disables), wired into `_state_skip_ids()`/`_chunked_rawg_pass()`; `no_match` rows older than N days (by their recorded `attempted_at`) are now re-eligible for chunk-mode retry, same pattern as `--retry-errors` for `error` rows. `matched` rows are still never retried. Verified: `--chunk-size 5 --retry-stale-days 1 --dry-run` picked up 5 previously-`no_match` rows (state was 2 days old) that a plain `--chunk-size 5` (default 30-day window) correctly still skipped as "No unattempted games remain"; a real (non-dry-run) `--chunk-size 5 --retry-stale-days 1` run re-queried RAWG for those 5 and refreshed their `attempted_at` timestamps in `.rawg_backfill_state.json` (still legitimately `no_match` — obscure/console titles not in RAWG). Added `tests/test_rawg_backfill.py` covering `_state_skip_ids`/`_is_stale_no_match`. 919/4,017 still missing `rawg_slug` as of this fix (unchanged — the fix restores the retry *path*, not an instant match; movement depends on RAWG's catalog growing over the 30-day window).
- [x] **Fixed 2026-07-08: `--fix-steam` 1000-row candidate-set truncation.** `_get_games_missing_steam()` now paginates internally in `FETCH_PAGE_SIZE` (1000-row) chunks and accumulates results, mirroring `_get_unattempted_games_missing_rawg()`; `--limit` still bounds rows *processed*, no longer the rows *fetched*. Verified: `--fix-steam --limit 1500 --dry-run` now reports the full **1,110** eligible rows (previously capped at ~1,000); `--limit 500`/`--limit 100 --dry-run` confirmed `--limit` still correctly bounds the set. Real (non-dry-run) `--fix-steam --limit 80` runs (two slices, first 80 alphabetically) completed with zero errors and correct DB read/write behavior; 0 Steam IDs found in this particular alphabetical slice (console-exclusive titles: Animal Crossing, Astro Bot, Arcade Archives, etc. — no Steam release exists, not a bug). 1,110/4,017 still missing `steam_app_id` as of this fix — the fix removes the silent-truncation ceiling so a future `--fix-steam` (no `--limit`, or `--limit` ≥ 1110) run will now actually reach every eligible row instead of stopping at ~1,000. Covered by the pagination tests in `tests/test_rawg_backfill.py`. **Full unbounded `--fix-steam` run completed 2026-07-08** (background job, no `--limit`, all 1,110 eligible rows reached in one pass — confirms the truncation fix holds at full scale): `Processed: 1110, Steam IDs found: 1, Errors: 0` — only "Yakuza: Like a Dragon - Hero Edition" matched (`steam_app_id=1235140`); `steam_app_id` gap now **1,109/4,017**. The near-zero hit rate (1/1110) is suspicious given the watchlist should include plenty of legitimately Steam-available titles among the 1,110 — worth investigating whether `get_steam_app_id()`'s RAWG `/stores` lookup (`agents/workers/market_player/rawg_client.py`) is matching correctly before spending further RAWG quota re-running `--fix-steam` on the same backlog.

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

- [x] **Built 2026-07-08:** `agents/workers/discovery/worker.py` — scans Steam/IGDB/EDGAR/Reddit for new watchlist candidates and writes `watchlist_proposals` rows (`status='pending'`). Never touches the active watchlist directly — that's still a manual approval step (Phase 7 dashboard item, not yet built). Fully dependency-injected (`run()`'s every DB/API touch point is a `<verb>_fn=`), matching `returns_tracker.py`'s testability convention rather than the untested older workers' style, since Discovery is new code with no legacy pattern to match. `agents/workers/discovery/scoring.py` implements the 100-point rubric from `agents/skills/watchlist-relevance-scoring/SKILL.md` §2 deterministically (6 components: public-parent confidence/materiality/live-service/signal-coverage/timeliness/uniqueness) — `>=70` high-confidence, `50-69` watch, `<50` no proposal. Claude (Haiku, `claude_rationale.py`, same convention as `absa_client.py`) is used only for the rationale *text*, never the score. See `CLAUDE.md`'s new "Discovery agent internals" section for the full design writeup (ticker-never-guessed gate, the two proposal shapes, Reddit-as-corroboration, simple false-positive learning). Added migration `010_watchlist_proposals_score.sql` (`score`, `recommended_sentiment_tier` columns — both named in the SKILL's output contract but had no backing column). Tests: `tests/test_discovery_scoring.py` (11 cases) + `tests/test_discovery_worker.py` (14 cases, zero live calls) — full suite 211/211 passing.
- [x] Integrate Steam trending / top-CCU source — reused the existing `steam_client.get_top_ccu_games(min_ccu=5000)` directly (already wraps the official Steam Charts API), no new Steam code needed.
- [x] Integrate IGDB upcoming release calendar (high-hype titles releasing in 60 days) — added `igdb_client.get_upcoming_releases()` + `_parse_upcoming_game()` (new `hypes` + best-effort `involved_companies` studio-name extraction, developer preferred over publisher), reusing the existing shared IGDB client's auth/`_post` rather than a new client.
- [x] Integrate SEC EDGAR scan for new IPO/acquisition filings — new `agents/workers/discovery/edgar_fulltext_client.py` (self-contained, not importing `studio_intel/edgar_client.py`, per this repo's worker-packages-don't-cross-import convention), using EDGAR's full-text search endpoint. **Verified live against the real endpoint while building this** (`https://efts.sec.gov/LATEST/search-index`, requires a declared `User-Agent` or SEC returns 403): a `forms=S-1` scan for `"video game"`/`"interactive entertainment"` correctly surfaced real, current candidates (Virtuix Holdings, FingerMotion) not yet in the `studios` table. Scoped to S-1 (new IPO registrations) + 8-K filtered to items `2.01`/`5.01` (acquisition/change-of-control) specifically — a plain keyword search on all 8-Ks was dominated by noisy `7.01`/`9.01` earnings-release exhibits with zero M&A content (confirmed live against a real GameStop earnings-release hit). These proposals are company-level (`game_id = NULL`, nullable in schema) since a filing doesn't name a specific title — a human still has to identify which game(s), if any, to attach.
- [x] Integrate Reddit mention-volume spike detection for untracked titles — scoped as **corroboration for already-gated candidates**, not an independent trigger, per the SKILL's explicit constraint ("Reddit mention spikes must use the existing cached Reddit adapter," which has no global keyword search — confirmed by reading `reddit_source.py` before building). Reuses `resolve_subreddit`/`fetch_posts`/`cached_resolve_subreddit` as-is, sharing cache namespaces with the Sentiment worker.
- [x] Implement Claude rationale generation per proposal — `agents/workers/discovery/claude_rationale.py`, Haiku, separate templates for game-level vs. company-level (EDGAR) candidates, graceful fallback string on any Claude-call failure (never blocks a proposal from being written).
- [x] Implement false-positive learning — kept intentionally simple this pass (session scope decision): skip re-proposing any game/company that already has a `pending` or `rejected` `watchlist_proposals` row (`get_existing_proposal_status`/`get_existing_company_proposal_status`, new in `db_client.py`). The SKILL's 5-code rejection-reason taxonomy has no schema column or review tooling yet — deferred rather than built ahead of anything to consume it; revisit alongside Phase 7's approval queue.
- [x] Write `agents/skills/watchlist-relevance-scoring/SKILL.md` — relevance criteria rubric, trigger thresholds, rationale template
- [x] Wire discovery worker into `run_weekly.py` — added after Sentiment, before Synthesis, matching `crew.py`'s existing task ordering.
- [x] Wire discovery task in `agents/orchestrator/crew.py` (replace placeholder) — `task_discovery` now describes the real worker like the other already-migrated tasks (`task_financial`, etc.) instead of the literal "Return the string OK" placeholder.
- [x] **Confirmed live 2026-07-09:** ran `agents/workers/discovery/worker.py` against production. Applied migration `010_watchlist_proposals_score.sql` (was missing — caused IGDB proposals to fail on first attempt). After applying: 1 proposal written — Onimusha: Way of the Sword (Capcom, IGDB upcoming release, score 63, "watch", `status='pending'`, `proposal_id=430bdaf5`). EDGAR: 0 results (no new gaming S-1/8-K filings in the 14-day window). Reddit: 403 WAF block (expected, same as Sentiment worker — non-fatal, corroboration degraded gracefully). Steam source errored (see item below).
- [x] **Fixed 2026-07-09: `ISteamApps/GetAppList/v2/` was permanently removed by Valve, not transient.** Confirmed live: the endpoint 404s with body `"Method 'GetAppList' not found in interface 'ISteamApps'"`, while every other Steam endpoint used in this repo (`ISteamChartsService/GetMostPlayedGames/v1/`, `ISteamUserStats/GetNumberOfCurrentPlayers/v1/`, `ISteamNews/GetNewsForApp/v2/`, `store.steampowered.com/api/appdetails`) returned 200 — isolated to this one endpoint. Replaced with the current official successor, `IStoreService/GetAppList/v1/`, in `agents/workers/market_player/steam_client.py::_app_name_map()`. Two behavioral differences from the old endpoint required a real code change (not just a URL swap): it **requires `STEAM_API_KEY`** (403 without one, confirmed live), and it **paginates** (max 50,000 apps/call, confirmed by testing `max_results=100000` and still getting exactly 50,000 back) via `last_appid`/`have_more_results` instead of returning the full ~190K-app catalog in one shot — `_app_name_map()` now loops until `have_more_results` is false. Verified live end-to-end through `steam_client.get_top_ccu_games()` and `agents/workers/discovery/worker.py::_default_steam_candidates()` (100 candidates resolved with real names, e.g. Counter-Strike 2, PUBG, Dota 2, ~8-11s total across the paginated calls). Added `tests/test_steam_client.py` (5 cases: single-page map, multi-page pagination stitching, empty-page early stop, endpoint/key/params correctness, `get_top_ccu_games` min-CCU filtering + name resolution) — full suite 267/267 passing.

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

- [x] Add `LANGSMITH_API_KEY` and `LANGSMITH_PROJECT` to `.env` and GitHub secrets — confirmed 2026-07-09; `weekly.yml` already wired both into its `env:` block from the earlier tracing work.
- [x] Add LangSmith tracing to all agent runs (token spend per subagent, full trace tree) — `agents/tracing.py` + `run_weekly.py` root span, per-worker `traced_step` spans, `wrap_anthropic` on the ABSA client for token usage
- [x] Add per-subagent token-spend logging via lifecycle hooks — new `agents/token_tracking.py` (top-level, mirrors `agents/tracing.py`'s pattern): a thread-local stack of frames, opened/closed at each `run_weekly.py` call site via `token_tracked_step(name)(callable)()` composed alongside the existing `traced_step()`. Independent of LangSmith — logs a `[token-spend] <name>: N call(s), X input / Y output tokens (model x count)` line with zero network calls and zero cost when a step makes no LLM call. Every real Anthropic call site now reports into it via `record_usage_from_message()`: `absa_client.py`, `claude_rationale.py`, `news_stance_client.py`, `entity_matcher.py`, and `agents/synthesis/deep_dive.py` (which also turned out to be missing `wrap_anthropic`/LangSmith instrumentation entirely until this pass — a stale gap from CLAUDE.md's "the one real LLM call site" framing, now inaccurate; not fixed here, flagged as a follow-up). The one Agent SDK session (`agents/portfolio/manager.py`'s Portfolio Manager) reads `ResultMessage.usage` instead of a raw `messages.create()` response. `record_usage_from_message()` is defensive (`getattr` with 0 fallback) so it never raises against the many existing unit-test fakes that don't set a `.usage` attribute. New `tests/test_token_tracking.py` (6 cases); full suite 296/296 passing. Live-smoke-tested against a real Haiku ABSA call: printed `[token-spend] smoke_test_absa: 1 call(s), 309 input / 215 output tokens (claude-haiku-4-5-20251001 x1)` correctly. Not wired into the legacy CrewAI placeholder crew (`agents/orchestrator/crew.py`'s `games_intel_crew.kickoff()`) since CrewAI calls models via `litellm`, not the raw `anthropic.Anthropic()` client this hook intercepts — that crew is a no-op confirmation pass per its own docstring, not a real output-producing pipeline, so instrumenting it wasn't judged worth a separate litellm-callback integration.
- [x] Add graceful error recovery to workers (retry on transient API errors, degrade rather than crash) — **2026-07-09:** the "degrade rather than crash" half was already solid everywhere (every worker's `run()` has a per-item try/except that logs and continues; `reddit_source.py`/`gdelt_client.py` already had their own retry-with-backoff). The real gap was "retry on transient API errors": most one-shot HTTP call sites gave up immediately on a single connection blip or transient 429/5xx. Added `agents/http_retry.py` (top-level shared helper, alongside `agents/tracing.py` — not inside any `agents/workers/<x>/` package, so it doesn't violate the worker-packages-don't-cross-import convention): `request_with_retry()` wraps a raw `requests.get`/`post`/`session.get` call with up to 3 attempts, exponential backoff (or a `Retry-After` header), retrying only connection/timeout errors and 429/500/502/503/504 — any other status or exception (403, a deliberate `*Blocked` signal, a parse error) passes straight through unchanged, so existing block/degrade semantics are untouched. `retry_call()` is the generic non-HTTP-response variant for `yfinance_client.py`, which doesn't expose a raw request call site. Wired into every one-shot client that lacked it: `market_player/steam_client.py`, `market_player/rawg_client.py`, `market_player/igdb_client.py`, `studio_intel/edgar_client.py`, `studio_intel/ats_clients.py`, `patch_notes/blog_client.py`, `news/rss_client.py`, `news/google_news_client.py`, `discovery/edgar_fulltext_client.py`, `financial_overlay/yfinance_client.py`. New `tests/test_http_retry.py` (13 cases) covers the shared helper directly; added retry-specific regression tests to the already-tested `test_steam_client.py`, `test_blog_client.py`, `test_rss_client.py`, `test_google_news_client.py`, `test_edgar_fulltext_client.py` confirming a transient 503/connection-error is retried then succeeds via the real client function. Full suite 290/290 passing. Not done: `rawg_client.py`/`igdb_client.py`/`studio_intel/edgar_client.py`/`ats_clients.py`/`yfinance_client.py` had zero test coverage before this change and still do (retry was wired in but not independently regression-tested per-module, since that's really a separate "add test coverage" task, not "add retry") — worth a follow-up if full coverage there is wanted later.
- [x] Lock model per-agent in all crew/agent configs — verify no agent defaults to most capable (audited 2026-07-03: `crew.py` workers on `claude-sonnet-4-6`, orchestrator on `claude-opus-4-8`, ABSA on `claude-haiku-4-5-20251001`, all explicit; synthesis/execution agents make no direct LLM calls. `agents/portfolio/manager.py` is now implemented and locked to `claude-opus-4-8` per the Opus-class tier. Re-confirmed 2026-07-06 after the Agent SDK/MCP migration: `manager.py` runs an Agent SDK session still locked to `claude-opus-4-8`; `agents/portfolio/execution_agent.py` remains a deterministic dispatch loop with **no direct LLM calls** — it only invokes the guarded Alpaca MCP tool in-process, so the "execution agents make no direct LLM calls" claim still holds. If a reasoning step were ever added there, Sonnet-class `claude-sonnet-4-6` would be the correct tier.)
- [x] Add `YOUTUBE_API_KEY` to `.env` and GitHub Actions secrets once the YouTube Data API collector is enabled — done 2026-07-09: wired `YOUTUBE_API_KEY`/`YOUTUBE_UPLOAD_PLAYLISTS` into `weekly.yml`'s `env:` block and `.env`/`.env.example`. `YOUTUBE_UPLOAD_PLAYLISTS` (previously undocumented but required alongside the key — `fetch_youtube_comments()` no-ops without both) was resolved via one-time `channels.list?forHandle=` lookups (1 quota unit/channel, not the quota-expensive `search.list` the collector deliberately avoids) for 6 broad-coverage game review channels: IGN, GameSpot, Eurogamer, Digital Foundry, Skill Up, ACG. Pushed as a GitHub Actions secret (`gh secret set`). Smoke-tested live against "Elden Ring" — 20 real comments returned end-to-end.
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

**Paused 2026-07-09 (deliberate scope decision):** deprioritized in favor of finishing the rest of the project first; revisit and pick Option A or B below later. Set `REDDIT_SOURCE_PAUSED=true` (`.env`, already set) so `build_reddit_source()`/`build_subreddit_resolver()` return a `NullRedditSource()` — zero network calls, sentiment/discovery workers degrade exactly like a real block, no code changes needed elsewhere. See `agents/workers/sentiment/reddit_source.py`'s module docstring and `CLAUDE.md`'s sentiment-internals note. To resume: unset `REDDIT_SOURCE_PAUSED` (or set to `false`) and pick up at the "User follow-up" subsection below.

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

- [x] **Resolved 2026-07-09: populated `player_metrics.peak_players_24h`** from Steam's official `ISteamChartsService/GetMostPlayedGames/v1/` chart (already used by `get_top_ccu_games`/`get_live_service_candidates`) — its `peak_in_game` field is the same 24h-peak value backing Steam's public "24-Hour Peak" stats page. New `steam_client.get_peak_players_24h_map()` builds an appid→peak dict from that chart (confirmed live: field names/semantics verified directly against the raw API response); `worker.py` looks up each game's Steam ID in that map once per run (cached in-memory, not per-game). **Real limitation, not fabricated for the rest:** the chart only covers the top ~100 games by peak concurrent players — Valve exposes no per-arbitrary-app-id 24h-peak endpoint — so `peak_players_24h` stays `None` for the long tail, same "only populate real data" convention as `health_score.py`. Read-only overlap check confirmed real coverage before building: 82/531 Steam-linked watchlist games matched the chart. Tests: 2 new cases in `tests/test_steam_client.py`; full suite 298/298 passing. **Confirmed live end-to-end 2026-07-09:** ran the full `market_player` worker against production (531 processed, 0 errors) — 82/531 rows now have a real non-null `peak_players_24h` (e.g. Apex Legends: Season 5 = 284,062; Counter-Strike: Global Offensive = 1,275,982).
- [x] **Resolved 2026-07-08: wired `equity_signals.health_score`** (the live successor to `portfolio_positions_context.signal_score`, renamed by migration 005 — the 15 legacy `portfolio_positions_context` rows predate that migration and are not written to anymore). New `agents/workers/financial_overlay/health_score.py`: a deterministic, quantitative-only composite (0-10) averaging up to 4 equal-weighted components per ticker — community sentiment (`sentiment_snapshots`, `source != 'news'`, 14-day lookback), patch cadence status (`patch_events.cadence_status`, latest per game, 60-day lookback), player-count momentum (`player_metrics.concurrent_players`, most recent 2 points per game, 30-day lookback), and studio distress (`studio_signals.severity`, worst-in-window, 90-day lookback; defaults to a clean 10.0 when the ticker has a known studio mapping but zero signal rows, since `studio_signals` is an event log written only on real distress — see `write_studio_signal`). A component is only included when data exists; `health_score` is `None` (not a fabricated neutral value) when a ticker has zero worker coverage across all four, which is still true for most of the watchlist per the coverage-gap item below. **Deliberately not** the materiality-weighted score from `agents/skills/equity-signal-mapping/SKILL.md` §2 (High/Medium/Low/Immaterial per game) — that weighting has no persisted data source anywhere and would need an LLM reasoning step this worker doesn't have; documented in the module docstring as a known scope limit, not silently approximated. Extended `get_watchlist_tickers()` to also return `game_ids`/full `studio_ids` per ticker, and added 4 new batched `get_recent_*_for_games`/`get_recent_studio_signals_for_studios` fetchers in `db_client.py`. `current_signal` now includes the score + component breakdown (or an explicit "unavailable" note) instead of just the tracked-games/studios count. `recommendation` remains unset — left out of scope, arguably a synthesis-level judgment rather than this worker's. Also fixed a real bug introduced and caught during this change: the `run()` return dict's `"date"` field would have silently become a `datetime.date` object instead of the prior ISO string once `today` was needed as a `date` for the lookback-window math. Tests: new `tests/test_health_score.py` (9 cases covering each component's present/absent path and the all-absent None floor) — full suite 186/186 passing. Not yet exercised against live Supabase (no test coverage previously existed for `financial_overlay/worker.py` itself, consistent with the rest of that file).
- [x] **Resolved 2026-07-08: wired the Returns Tracker to populate `positions` from Alpaca's live positions.** `alpaca_trading_client.get_account_state()` now also pulls `avg_entry_price`/`current_price`/`unrealized_pl` per position from `GET /v2/positions` (previously only `symbol`/`qty`/`market_value`) — a side benefit is the Portfolio Manager's `get_account_state` MCP tool now surfaces entry price and unrealized P&L to the LLM too, since `account_state_text` just JSON-dumps the whole dict. Added `db_client.write_current_positions()`: delete-then-insert (the table has no unique constraint to upsert against, and it's meant to mirror Alpaca's *current* open positions rather than accumulate a per-run history like `portfolio_snapshots` already does — a closed-out position correctly disappears instead of lingering as a stale row). `returns_tracker.run()` calls it right after every successful account fetch, via a new injectable `write_positions_fn`; a write failure there is caught and logged, never blocking the `portfolio_snapshots` write. Known/accepted limitation: `signal_source` is left unset — an open position can be the net result of multiple orders across multiple weeks/plans, so there's no reliable one-to-one mapping back to a single originating trade_plan; documented in the module docstring rather than fabricated. Tests: 3 new cases in `tests/test_returns_tracker.py` (fields mapped correctly, empty-positions still clears the table, write failure degrades gracefully) — full suite 177/177 passing. Not yet exercised against live Alpaca/Supabase (no open paper positions exist yet per the Phase 5 validation note above); will get its first live proof the first week a trade actually fills.
- [ ] Close the watchlist coverage gap: 3,504/4,017 (87%) active watchlist games have never received a `player_metrics` row, and 3,498/4,017 (87%) never received a `sentiment_snapshots` row — this is mostly downstream of the `games.steam_app_id` gap (2,028/4,017 NULL, tracked under the RAWG backfill task above) and the `watchlist.subreddit` backlog (3,017/4,017 unresolved, tracked under the Reddit remediation section above). No new work item beyond finishing those two backfills, but worth re-running this coverage check after both land to confirm the gap actually closes rather than just shifting.
- [x] **Confirmed benign 2026-07-09:** spot-checked the `review_score`/`review_count`/`review_velocity` NULL rows. 40/42 (95%) of the NULL `review_score` rows are dated `2026-06-30` — snapshots written *before* the RAWG/Steam-ID backfill (2026-07-08) resolved `steam_app_id` for those titles, not a swallowed failure on a currently-working title. Verified directly: hit Steam's live `appreviews` endpoint for "Battlefield 6" (`steam_app_id=2807960`, one of the stale NULL rows) and got a real `query_summary` (350,983 total reviews) — the API call itself works fine today; the NULL is just a stale historical row that a fresh run replaces. Confirmed via a full live `market_player` worker re-run (see `peak_players_24h` item below) that today's rows for these titles now populate correctly. The few genuinely-NULL-today cases (e.g. "Keeper", `steam_app_id=1547130`) are legitimate: Steam's `GetNumberOfCurrentPlayers` 404s (`result: 42`, an invalid/restricted-visibility app for that endpoint) and `appreviews` correctly reports `"No user reviews"` (0 total) — both genuine data-source limits for an obscure title, already handled by the existing per-item try/except (logs and continues), not a silent bug.

---

## News Article Stream — 2026-07-08

Added a fourth sentiment source (`source='news'` in `sentiment_snapshots`) per `docs/news-source-decision-memo.md`'s research/planning pass. News is deliberately **not** scored like community sentiment (no VADER/ABSA/vocal-minority reuse — see the memo's §3) and is ingested by a standalone module rather than folded into the Sentiment worker's own fetch logic, so Studio Intel/Discovery can read the same substrate later without cross-worker coupling.

- [x] Apply `database/migrations/008_news_items.sql` — `news_items` table + `games.aliases`/`games.title_is_ambiguous` columns (applied 2026-07-08)
- [x] Build `agents/workers/news/` — standalone news ingestion worker: `gdelt_client.py` (GDELT DOC 2.0, Tier-1-by-ToS/Tier-2-resilience-posture), `rss_client.py` (curated games-press RSS/Atom, independent fork of `patch_notes/blog_client.py`'s parser per the no-cross-import convention), `google_news_client.py` (per-entity thin-coverage fallback), `entity_matcher.py` (two-stage relevance matching: free deterministic word-boundary match + cached Haiku disambiguation for ambiguous/studio-only matches, fails closed), `worker.py` (orchestrates fetch → dedupe → match → write `news_items`)
- [x] Build `agents/workers/sentiment/news_stance_client.py` — aggregate per-game-per-week media stance/frame classifier (Haiku, Sonnet for `tier_a` games); replaces VADER+ABSA+vocal-minority for this source only
- [x] Wire news consumption into `agents/workers/sentiment/worker.py` (`_write_news_snapshot`) and news ingestion into `run_weekly.py` (runs before Sentiment so this week's articles are available when it reads them)
- [x] **Fixed a real bug found while planning this feature**: `agents/synthesis/agent.py::_sentiment_by_game()` was averaging `sentiment_score` across *every* source with no filter — the moment a `source='news'` row existed it would have silently blended into `avg_score` alongside reddit/steam/youtube, corrupting `_compute_divergence`/`_compute_risks`. Split into `avg_score` (community only) / `news_score` (news only); added a `news_community_divergence` signal when the two disagree by >= 2.5 points. Regression test: `tests/test_synthesis_sentiment_split.py`.
- [x] Add 6 rows to `docs/data-source-risk-register.md` (GDELT, curated RSS, Google News RSS, plus Tier-4 exclusions for NewsAPI.org/GNews free tiers naming the above as substitutes)
- [x] Update `CLAUDE.md` (folder structure, migration list, new "News ingestion internals" section, sentiment-internals note) and `agents/skills/investment-synthesis-framework/SKILL.md` (documents the community/news split so it doesn't regress)
- [x] Tests: `tests/test_gdelt_client.py`, `tests/test_rss_client.py`, `tests/test_entity_matcher.py`, `tests/test_synthesis_sentiment_split.py` (39 new tests; full suite 159/159 passing)
- [x] Manually seed `games.title_is_ambiguous = true` for common-word titles so entity matching forces Stage-2 disambiguation on them — migration 008 only added the column at `false` for every row. Note: the watchlist's actual `Control`/`Destiny`/`Halo` rows are all compound editions (`Destiny 2: Forsaken`, `Halo Infinite: Operation - Anvil`, etc.), not bare single-word titles, so full-title word-boundary matching on those specific rows is already fairly safe; the real ambiguity risk is concentrated in the watchlist's 90 literal single-word titles. Added `database/migrations/009_seed_ambiguous_titles.sql` (curated to 30 titles that are themselves common English words — `Anthem`, `Arms`, `Ball`, `Bound`, `Concord`, `Doom`, `Dreams`, `Erica`, `Golf`, `Grounded`, `Humankind`, `Judgment`, `Keeper`, `Kiln`, `Kitchen`, `Maiden`, `Marathon`, `Overwatch`, `Prey`, `Quake`, `Rad`, `Rise`, `Rust`, `SCUM`, `Siren`, `Spatter`, `Squad`, `Stifled`, `Uno`, `Vermin`) and applied it directly against production 2026-07-08 (34 rows updated — several titles like `Doom`/`Quake`/`Judgment` have multiple watchlist rows); confirmed via `select count(*) from games where title_is_ambiguous = true` returning 34.
- [x] Run `agents/workers/news/worker.py::run()` once against production to confirm `news_items` actually populates, then a `sentiment` worker run to confirm a `source='news'` row lands correctly end-to-end — confirmed working 2026-07-08, scoped to 5 high-coverage watchlist titles (Halo Infinite, Destiny-adjacent/Prey/Rust/Starfield/Diablo IV) rather than all 4,017 entities (GDELT alone would be ~1.5-2h of throttled per-entity queries for a full run; scope decision reviewed with the user first). `news_items`: 511 articles fetched, 238 matched & written, 5/5 entities got coverage. Sentiment worker: `_write_news_snapshot` wrote a real `source='news'` row for Halo Infinite (`sentiment_score` 5.5, populated `top_themes`/`vocal_minority_note`, real Claude Sonnet stance/frame call since it's a `tier_a` game). Found and fixed two real bugs surfaced by this first live exercise:
  - `agents/workers/news/gdelt_client.py`'s `GdeltSource.search()` only wrapped HTTP-status and JSON-parse failures in `GdeltBlocked` — a raw connection error (`RemoteDisconnected`, confirmed live: `api.gdeltproject.org` was unreachable/timing out from this dev sandbox specifically, while google.com/reddit.com both returned 200) propagated past `worker.py`'s `except GdeltBlocked` and crashed the whole run instead of degrading gracefully for just that one entity. Now catches `requests.exceptions.RequestException` and retries like the other transient-failure branches. Added regression tests in `tests/test_gdelt_client.py` (connection-error-then-succeeds, and exhausted-retries-raises-`GdeltBlocked`). Note: this GDELT unreachability looked environment-specific to this sandbox, not necessarily a signal about GitHub Actions' network path — worth a quick check the first time this runs for real in CI.
  - `database/db_client.py::get_recent_news_items()` used `.contains("matched_entities", [game_id])`; postgrest-py's `.contains()` only JSON-encodes `dict` values, so a plain list got rendered as a Postgres array literal (`{game_id}`) instead of a JSON array — invalid syntax against the `jsonb` `matched_entities` column, raising `invalid input syntax for type json` on every call. Fixed by building the `cs` filter manually with `json.dumps([game_id])`. No prior test coverage existed for `db_client.py`'s query builders (consistent with the rest of that file); caught only by this live run.
  - Also seeded `database/migrations/009_seed_ambiguous_titles.sql` this session (see above) and applied it before this run, so `Prey`/`Rust`'s Stage-2 disambiguation path was exercised too, not just Stage-1 title matching.
- [x] Update `docs/games-investment-platform-brief.md` (Component #3 Player Sentiment's Sources list + the Data & APIs summary table) and `docs/agent-components-plan.md` (Agent 2 Sentiment Subagent's tool list + output contract) to mention the news source

---

## YouTube Channel Coverage — 2026-07-09

`YOUTUBE_UPLOAD_PLAYLISTS` (see Cross-Cutting/Infrastructure) is currently a fixed, global list of 6 broad-coverage outlet channels (IGN, GameSpot, Eurogamer, Digital Foundry, Skill Up, ACG) resolved once via a one-time `channels.list?forHandle=` lookup — every watchlist game is matched against the same shared playlist set by title/description text match in `youtube_client.py::_candidate_videos()`. This misses per-game and per-studio individual content creators (a specific game's dedicated community/streamer/co-op-partner channels) that outlet channels don't cover.

- [ ] Design and build per-game/per-studio YouTube channel discovery: for each watchlist game (or its studio), find individual content creators/channels that cover that specific title, not just the shared outlet list. Needs a discovery mechanism — likely a curated per-game config (same convention as `GAME_PATCH_PAGES`/`STUDIO_ATS_BOARDS`) to avoid `search.list`'s quota cost, or a bounded one-time `search.list` pass per game (budget against YouTube's daily quota ceiling before choosing this path).
- [ ] Decide storage shape: extend `YOUTUBE_UPLOAD_PLAYLISTS` to be per-game rather than global (would need a new column, e.g. `watchlist.youtube_playlists`, since a single global env var can't scale to per-game creator channels), or a separate config source.
- [ ] Update `agents/workers/sentiment/youtube_client.py::fetch_youtube_comments()`/`worker.py` to look up game-specific playlists in addition to (or instead of) the shared global list.
- [ ] Confirm quota impact before rolling out broadly — even the current global-list design does `max_videos` playlistItems.list calls x every watchlist game with a Steam ID (531 games), so a per-game creator list multiplies call volume by however many creator channels are added per game.
