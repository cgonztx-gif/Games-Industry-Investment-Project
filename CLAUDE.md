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
    sentiment/        Reddit/Steam/YouTube/news sentiment (VADER + Claude ABSA; news gets its own stance/frame classifier)
    patch_notes/      Update cadence analysis
    studio_intel/     Job postings, press releases, SEC filings
    financial_overlay/ yfinance + SEC EDGAR equity mapping
    news/             GDELT + curated RSS + Google News article ingestion & entity matching (feeds sentiment's news source)
    discovery/        Steam/IGDB/EDGAR/Reddit-sourced new watchlist candidate proposals
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
| 6 | Discovery Agent | Built (not yet run live) |
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
- No Reddit OAuth credentials are required by default. Reddit collection uses public read-only `.json` endpoints through `agents/workers/sentiment/reddit_source.py` and `api_cache`.
- `YOUTUBE_API_KEY` is required once the YouTube Data API comment collector is enabled.
- `GAME_YOUTUBE_PLAYLISTS` (optional) — JSON object mapping a watchlist game's exact `games.title` to one or more per-game/per-studio creator upload-playlist IDs, e.g. `{"Fortnite": ["UUabc123..."]}`. Same manually-curated-config convention as `GAME_PATCH_PAGES`/`STUDIO_ATS_BOARDS`; no games configured by default. Covers dedicated community/creator channels the shared `YOUTUBE_UPLOAD_PLAYLISTS` outlet list misses — see `youtube_client.py`'s docstring for how it's merged (unfiltered, unlike the title-filtered global list).
- `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_REFRESH_TOKEN` (optional, all three required together) — activates `OAuthRedditSource`, an alternate egress for when the default unauthenticated path is IP-blocked. Requires completing Reddit's manual, non-guaranteed OAuth app-approval process (no SLA as of the Nov 2025 policy).
- `REDDIT_PROXY_URL` (optional) — activates `ProxiedJsonRedditSource`, routing the default `.json` adapter through a standard HTTP/HTTPS proxy (SOCKS5 not supported). Both vars are no-ops with zero network-path change until set; see the "Sentiment pipeline internals" note below.

**Phase 2 (news worker, no vars required):**
- GDELT DOC 2.0, the curated games-press RSS feeds, and Google News RSS are all free and keyless — the news worker (`agents/workers/news/`) needs no new env vars. See the "News ingestion internals" note below.

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
6. **Schema changes always go through `database/migrations/`** — `database/schema.sql` is the original baseline only; never edit it for a post-setup change. Add a new numbered `database/migrations/NNN_description.sql` file instead (see the existing `001`-`010` files for the pattern), append it to the "Apply pending migrations" list below, and apply it via the Supabase SQL Editor. Keeps `schema.sql` a truthful historical snapshot and gives every change an explicit, individually-applicable, auditable step.

### Sentiment pipeline internals (`agents/workers/sentiment/`)
The sentiment worker runs a two-pass pipeline per game for community sources (Reddit/Steam/YouTube):
- **VADER baseline** (`vader_scorer.py`) — deterministic rule-based polarity score over all texts, returns a 1–10 float
- **ABSA** (`absa_client.py`) — Claude Haiku extracts aspect→polarity pairs (e.g. `monetization → negative`); skipped if fewer than 5 texts; top 3 aspects returned
- **Preliminary lagged flag** (`divergence.py`) — optional hint against the latest stored player metrics; authoritative same-week divergence belongs in synthesis
- **Reddit source** (`reddit_source.py`, `reddit_cache.py`) — an env-var-gated `FirstAvailableRedditSource` chain, tried in priority order: OAuth (`OAuthRedditSource`, plain `requests`, no `praw`) → HTTP/HTTPS proxy (`ProxiedJsonRedditSource`, a thin `JsonRedditSource` subclass) → the default unauthenticated public `.json` adapter (`JsonRedditSource`). Each leaf gets its own `api_cache` namespace (`reddit_oauth` / `reddit_proxy` / `reddit`) so one path's stale-serve can never mask another path's real health. Confirmed 2026-07-06: the default unauthenticated path now receives a static WAF `403` ("You've been blocked by network security") on every endpoint, every run, from GitHub Actions' datacenter IPs — an IP-reputation block, not throttling. OAuth and proxy are opt-in remediations gated behind `REDDIT_CLIENT_ID`/`REDDIT_CLIENT_SECRET`/`REDDIT_REFRESH_TOKEN`/`REDDIT_PROXY_URL` — neither is automatically active, and with none of those vars set the behavior is bit-for-bit identical to the original unauthenticated-only adapter. `build_subreddit_resolver()` mirrors the same priority chain (uncached leaves) for subreddit-name resolution, fixing a prior bug where resolution bypassed the fallback chain entirely. **Deliberately paused 2026-07-09** (scope decision, not a bug): `REDDIT_SOURCE_PAUSED=true` makes both `build_reddit_source()`/`build_subreddit_resolver()` return `NullRedditSource()` instead of building any leaf — every call raises `RedditBlocked` immediately with zero network I/O, so the sentiment and discovery workers degrade exactly the way they already do on a real block, with no code changes needed in either. This avoids paying for a guaranteed-failed request against the confirmed-permanent WAF block on every game/candidate every run, while the rest of the project (Phase 6/7) gets built out. Unset the var (or set it to `false`) to resume once a real OAuth/proxy egress path is configured.
- **`source='news'` is a fourth, separate pipeline** (`news_stance_client.py`) — see "News ingestion internals" below. It reads `news_items` (written earlier in the run by `agents/workers/news/worker.py`) and runs one aggregate Claude stance/frame call per game per week; it does **not** go through VADER/ABSA/the vocal-minority weighting above, because media coverage measures a different axis (narrative framing) than community sentiment. `agents/synthesis/agent.py::_sentiment_by_game()` keeps `source='news'` out of `avg_score` for exactly this reason — see that section's docstring before changing the split.

### News ingestion internals (`agents/workers/news/`)
Standalone ingestion module (not a sentiment sub-pipeline) that fetches games-industry news, matches it to watchlist entities, and writes matched articles into a shared `news_items` table (migration `008_news_items.sql`) — a substrate the Sentiment worker consumes but doesn't fetch itself, so other workers (Studio Intel, a future Discovery agent) can read the same table later without cross-worker coupling. Runs before the Sentiment worker in `run_weekly.py` so this week's articles are available when Sentiment reads them. All three fetch sources are free and keyless (see `docs/data-source-risk-register.md`):
- **GDELT DOC 2.0** (`gdelt_client.py`) — one query per watchlist entity (`"<title>"`), Tier-2 resilience posture (rate limiter, `api_cache` namespace `gdelt`, retry/backoff, `GdeltBlocked` → serve stale) despite being an official Tier-1-by-ToS API, since it has no SLA and is format-fragile.
- **Curated games-press RSS** (`rss_client.py`) — a fixed list of outlet feeds (`CURATED_FEEDS`), pulled once per run regardless of watchlist size; a forked, minimal RSS/Atom parser (deliberately independent of `patch_notes/blog_client.py`'s — worker packages don't cross-import) extracts title/snippet/url/date/domain only, no full article bodies.
- **Google News RSS** (`google_news_client.py`) — per-entity fallback, only queried for entities the first two sources found zero matches for this run ("thin coverage" backfill). Reuses `rss_client`'s feed parser (same-package import, not cross-package).
- **Relevance matching** (`entity_matcher.py`) — Stage 1 is a free deterministic word-boundary match against each entity's `games.title`/`games.aliases` (strong) or studio name (weak, forces Stage 2). Stage 2 is a single cached Haiku yes/no disambiguation call, triggered only for studio-only matches or entities flagged `games.title_is_ambiguous` (seed this manually for common-word titles like Control/Destiny/Rust after applying migration 008) — verdicts are cached by `(article_url, entity_title)` in `api_cache` namespace `news_disambiguation` so a given article is judged once ever. Fails closed (excludes the match) on any error.

### Tracing internals (`agents/tracing.py`)
`configure_tracing()` is called once at the very start of `run_weekly.py`'s pipeline run. Each worker/synthesis/crew call is wrapped via `traced_step(name)(callable)()` at the `run_weekly.py` call site — not via decorators inside each worker file, to avoid touching every worker module. Most direct Anthropic call sites are instrumented via `langsmith.wrappers.wrap_anthropic`, which captures token usage automatically: `absa_client.py`, `claude_rationale.py` (discovery), `news_stance_client.py`, `entity_matcher.py` (news). **Known gap:** `agents/synthesis/deep_dive.py` uses a raw (unwrapped) `anthropic.Anthropic()` client — not LangSmith-instrumented — found while adding the per-subagent token logging below; not yet fixed. Fully opt-in: everything no-ops with zero network calls when `LANGSMITH_API_KEY` is unset.

### Per-subagent token-spend logging (`agents/token_tracking.py`)
A second, LangSmith-independent instrumentation layer added 2026-07-09 alongside the loose-ends pass that activated `LANGSMITH_API_KEY`/`YOUTUBE_API_KEY`. Lives at the `agents/` top level like `agents/tracing.py`/`agents/http_retry.py` for the same cross-worker-import reason. A thread-local stack of frames is opened/closed via `token_tracked_step(name)(callable)()`, composed alongside the existing `traced_step()` at every `run_weekly.py` call site; on exit it prints `[token-spend] <name>: N call(s), X input / Y output tokens (model x count)` — nothing at all if the step made zero LLM calls (most workers). Every real Anthropic call site (the four `wrap_anthropic` ones above, plus `deep_dive.py`) calls `record_usage_from_message(model, msg)` right after `messages.create()` returns; the one Agent SDK session (`agents/portfolio/manager.py`'s Portfolio Manager) instead reads token counts off `ResultMessage.usage`. `record_usage_from_message()` is defensive (`getattr`-based, 0 fallback) so it never raises against unit-test fakes lacking a `.usage` attribute — verified this doesn't silently break error-handling by re-running the full suite after wiring it in. Not wired into the legacy CrewAI placeholder crew (`agents/orchestrator/crew.py`), since CrewAI routes model calls through `litellm`, not the raw client this hook intercepts, and that crew is a no-op confirmation pass with no durable output of its own.

### Transient-error retry (`agents/http_retry.py`)
Shared helper, added 2026-07-09, living at the `agents/` top level (like `agents/tracing.py`) rather than inside any `agents/workers/<x>/` package, so every worker can use it without violating the worker-packages-don't-cross-import convention (see the news/patch_notes RSS parser split, and `gdelt_client.py`'s own from-scratch `RateLimiter`, for why worker-specific code is otherwise deliberately duplicated instead of shared). Every worker already degrades gracefully at the per-item level (`run()`'s per-item try/except logs and continues rather than crashing the run) — this only closes the narrower "give up too easily on a single transient blip" gap. `request_with_retry(request_fn, *args, **kwargs)` wraps a one-shot `requests.get`/`post`/`session.get` call, retrying up to 3 attempts with exponential backoff (or a `Retry-After` header) on a connection/timeout error or a 429/500/502/503/504 response; any other status/exception (403, a deliberate `*Blocked` signal exception, a parse error) passes straight through unchanged on the first attempt, so each client's existing block-detection/degrade semantics are untouched. `retry_call(fn, ...)` is the generic non-HTTP-response variant, used by `financial_overlay/yfinance_client.py` since the `yfinance` library doesn't expose a raw request call site. Wired into every client that previously made a single unretried request: `market_player/steam_client.py`, `rawg_client.py`, `igdb_client.py`, `studio_intel/edgar_client.py`, `ats_clients.py`, `patch_notes/blog_client.py`, `news/rss_client.py`, `google_news_client.py`, `discovery/edgar_fulltext_client.py`, `financial_overlay/yfinance_client.py` — `agents/workers/sentiment/reddit_source.py` and `agents/workers/news/gdelt_client.py` keep their own pre-existing retry loops rather than being migrated to this shared one.

### Briefing email delivery (`agents/synthesis/email_delivery.py`)
`send_briefing_email(briefing)` is called from `agents/synthesis/agent.py`'s `run()` immediately after `write_weekly_briefing(db, briefing)`, wrapped in a try/except so an email failure can never prevent the briefing from being considered "done." Renders a plain HTML summary (briefing text + bullet lists for top opportunities / risk flags / notable events) and POSTs it directly to Resend's REST API (`POST https://api.resend.com/emails`) via `requests` — no `resend` SDK dependency. Fully opt-in: no-ops with zero network calls when `RESEND_API_KEY` or `BRIEFING_EMAIL_TO` is unset, and any HTTP failure is caught and logged rather than raised.

### Portfolio agents MCP internals (`agents/portfolio/`)
Both Phase 5 portfolio agents run on the Claude Agent SDK (`claude-agent-sdk`) with **custom in-process MCP servers** (`agents/portfolio/alpaca_mcp.py`, built via `create_sdk_mcp_server` / `@tool`) rather than the official alpacahq `alpaca-mcp-server`. The official server exposes a bare `place_order` tool that talks straight to Alpaca's REST API; handing that to an LLM would let a model place an order without the Supabase `status='approved'` re-check running — a direct violation of Key Architecture Rules 4 and 5. The custom servers wrap the *existing* `alpaca_trading_client` functions, so the guard is reused verbatim, never re-implemented.
- **Portfolio Manager (`manager.py`, model locked to `claude-opus-4-8`, Opus-class):** runs a real Agent SDK session whose tool surface is pinned via `allowed_tools` to exactly one **read-only** tool, `mcp__alpaca-readonly__get_account_state` (wraps `get_account_state()`). Claude pulls live account/position state itself instead of it being pre-fetched into a text blob. Read-only, so no approval concern — every order it *writes* still lands `status='pending'`. Graceful degradation: if the account fetch fails, the tool returns an "UNAVAILABLE — build conservatively" note (`alpaca_mcp.account_state_text`) instead of raising. The LLM-session seam is the injectable `run_agent_fn` (replaced the old injectable `client`); tests inject a fake returning scripted text so there are zero live network/CLI calls. Output Contract JSON, parsing, and the `trade_plans`/`trade_orders` writes are unchanged.
- **Returns Tracker (`returns_tracker.py`, no LLM / no model tier):** a lightweight post-execution tracker. `run()` fetches current Alpaca account state (`get_account_state()`) and upserts one `portfolio_snapshots` row per date: `total_value`, `cash`, `total_return_pct`, `benchmark_return_pct`. Both `_pct` columns are **cumulative since inception** — measured against the earliest existing `portfolio_snapshots` row, not week-over-week — to stay apples-to-apples per row. On the very first run (empty table) this run's value becomes the baseline: both `_pct` fields are `0.0` and the SPY lookup is fully short-circuited (nothing to compare against). SPY benchmark, only when a baseline exists, two independent legs each with an Alpaca→yfinance fallback: baseline-date close (`alpaca_data_client.get_historical_close` daily-bars → `yfinance_client.get_historical_close`, the latter cached **indefinitely** via `api_cache` with `max_age_hours=None` since a past close is immutable) and current price (`get_latest_price` → `get_equity_snapshot(...)["price"]`). Degradation: account fetch failure → log and `return None` (no row); SPY unavailable on both tiers of either leg → row still written with `benchmark_return_pct = None` (plain nullable column, not the LLM-facing "UNAVAILABLE" text); a stored baseline `total_value` of `0` → `total_return_pct = None` (no divide-by-zero). Every DB/API touch point is an injectable `<verb>_fn` (tests use plain fakes, zero live calls). Also replaces the `positions` table (`db_client.write_current_positions`, delete-then-insert since the table has no unique constraint and mirrors Alpaca's *current* open positions rather than accumulating history) with `ticker`/`qty`/`avg_entry_price`/`current_price`/`unrealized_pnl` from the same `get_account_state()` call — `signal_source` is deliberately left unset (no reliable one-to-one mapping from an open position back to a single originating trade_plan). A positions-write failure is caught and logged, never blocking the `portfolio_snapshots` write. Wired into `run_weekly.py` last among the three Phase 5 portfolio steps (Portfolio Manager → Execution Agent → Returns Tracker), so it snapshots the account state after any approved orders were placed this run.
- **Execution Agent (`execution_agent.py`, no LLM / no model tier):** deliberately **deterministic** — the pre-migration "for each approved order, place it" loop has no judgment call, so no LLM reasoning step was added (an LLM could skip, double-place, or hallucinate on a safety-critical path). It uses MCP only for the tool-call boundary: it invokes the custom `alpaca-execution-guarded` server's two tools (`get_approved_orders`, `place_approved_order`) in-process via `tool.handler(...)`. The placement tool wraps `place_approved_order()`, which re-reads Supabase status and raises `TradeNotApproved` unless it is exactly `'approved'` — the guard fires on every placement. That guarded server is the agent's *entire* tool surface (Rule 4), and the guard is enforced inside the placement tool (Rule 5). `run()` keeps its `orders_checked`/`orders_placed`/`error_count`/`placed`/`errors` return shape; `db`/`get_approved_fn`/`place_fn` are injectable for tests.

### Financial overlay health score (`agents/workers/financial_overlay/health_score.py`)
`equity_signals.health_score` is a deterministic, quantitative-only composite (0-10) averaging up to 4 equal-weighted components per ticker — community sentiment (`sentiment_snapshots`, `source != 'news'`), patch cadence status (latest per game), player-count momentum (most recent 2 `player_metrics` points per game), and studio distress (`studio_signals.severity`, worst-in-window; defaults to a clean 10.0 when the studio is known but has zero signal rows, since `studio_signals` is an event log written only on real distress). A component is only included when data exists; `health_score` is `None` when a ticker has zero worker coverage across all four — most of the watchlist today. This is **not** the materiality-weighted score from `agents/skills/equity-signal-mapping/SKILL.md` §2 (per-game High/Medium/Low/Immaterial weighting) — that has no persisted data source anywhere and would need an LLM reasoning step this worker doesn't have.

### Discovery agent internals (`agents/workers/discovery/`)
Scans four sources for watchlist candidates not yet tracked and writes `watchlist_proposals` rows (`status='pending'`) for human review — it never adds anything directly to the active watchlist; that only happens at manual approval, which isn't built yet (Phase 7 dashboard item). Scoring is fully deterministic (`scoring.py`, the 100-point rubric from `agents/skills/watchlist-relevance-scoring/SKILL.md` §2) — Claude (`claude_rationale.py`, Haiku) is only used for the rationale *text*, never the score, so results stay reproducible.
- **Ticker resolution is never guessed** (a hard SKILL.md constraint): a Steam/IGDB candidate's `studio_name` is matched *exactly* (case-insensitive) against `get_studios_with_tickers()`'s existing curated set. No match → candidate dropped, no proposal, no speculative studio row created.
- **Two proposal shapes.** Game-level (Steam top-CCU via `steam_client.get_top_ccu_games` / IGDB upcoming-release via the new `igdb_client.get_upcoming_releases`): a specific `game_id`, scored via the full rubric. Company-level (`edgar_fulltext_client.py`, SEC EDGAR full-text search for new S-1/8-K filings mentioning gaming, restricted to acquisition/change-of-control 8-K items `2.01`/`5.01` to cut noise): `game_id = NULL` (nullable in schema) — a filing surfaces a new public gaming company, not a specific title, so it's scored on a fixed conservative value (`_EDGAR_COMPANY_SCORE = 55`, always "watch" band) rather than the full rubric, and a new `studios` row is created directly from the filing's own disclosed ticker (the one case where Discovery does create a studio speculatively, since EDGAR itself — not a guess — is the ticker source).
- **Reddit is corroboration, not an independent trigger** (per the SKILL: "Reddit mention spikes must use the existing cached Reddit adapter," which has no global keyword search) — only runs for candidates that already passed the studio/ticker gate, folding into the "signal coverage" score component via `resolve_subreddit`/`fetch_posts` from `agents/workers/sentiment/reddit_source.py` (shared cache namespaces with the Sentiment worker, so a subreddit already resolved this week isn't re-queried).
- **False-positive learning is intentionally simple** for now: any game/company with an existing `pending`/`rejected` proposal is skipped (`get_existing_proposal_status`/`get_existing_company_proposal_status`), so a rejected candidate isn't re-proposed every week. No rejection-reason taxonomy, schema column, or review CLI yet — deferred alongside Phase 7's dashboard rather than built ahead of any review tooling to consume it.
- Every DB/API touch point in `worker.py`'s `run()` is an injectable `<verb>_fn` (tests use plain fakes, zero live calls) and each of the 3 sources is wrapped independently so one source failing (or one bad candidate within a source) degrades to a logged error rather than crashing the run.
- Not yet exercised against live Supabase/Steam/IGDB/EDGAR/Reddit — built and fully unit-tested, but running it writes real rows to production `games`/`studios`/`watchlist_proposals`, so that's a deliberate follow-up step rather than done automatically.

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
# database/migrations/008_news_items.sql
# database/migrations/009_seed_ambiguous_titles.sql
# database/migrations/010_watchlist_proposals_score.sql

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
