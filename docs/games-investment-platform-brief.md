# Games Industry Investment Intelligence Platform
### Project Brief

---

## Overview

A multi-agent investment intelligence system that monitors the games industry across product, community, and financial data layers, then synthesizes signals into a weekly portfolio briefing. The core thesis: game-level data (player counts, sentiment, patch cadence, studio hiring) is a leading indicator of financial performance that traditional investors underweight.

> **Companion documents:**
> - *Agent Components Deep-Dive* — the internals of every agent: subagents, skills (with the analytical frameworks each encodes), tools, and cross-cutting infrastructure (hooks, model tiering, observability).
> - *Data Source Risk Register* — every external source classified by access path, terms-of-service posture, and block risk, with the mitigation each tier requires. **Any new source enters the system through this register first.**
> - *RedditSource Adapter* and *SupabaseRedditCache* — the reference implementation of the resilient-source pattern the register mandates for unofficial sources.
>
> This brief is the system-level overview; those documents are the implementation reference.

---

## Goals

- Aggregate quantitative and qualitative data across games, studios, and public equities
- Detect early signals (positive and negative) before they show up in earnings
- Generate a structured weekly briefing with portfolio recommendations and risk flags
- Build a dashboard to visualize trends over a tracked portfolio of studios/tickers

---

## Components

### 1. Market & Player Data Agent
**Purpose:** Track product health week over week via player engagement metrics.

- **Sources:** Steam (`ISteamUserStats` — official Web API — for concurrent players; the public `appreviews` JSON endpoint for review data, a Tier-2 source per the risk register), IGDB, RAWG
- **Tracks:** Concurrent player counts (weekly snapshots build the historical series — the official API only exposes the current figure), review scores, review velocity, follower/hype momentum, game release calendars
- **Signal logic:** Sustained player decline + slowing review rate = deteriorating product health; follower/hype spikes ahead of release = demand signal. (Note: Steam does **not** expose per-title wishlist counts publicly — IGDB "hype" counts and Steam follower counts are the observable proxies.)

---

### 2. Patch Notes & Update Cadence Agent
**Purpose:** Infer developer investment and live-service commitment from update behavior.

- **Sources:** Steam news API (official `ISteamNews/GetNewsForApp` — per-title patch notes and announcements), official developer blogs via web fetch
- **Tracks:** Update frequency, patch size/scope, content type (bug fix vs. content drop vs. monetization change)
- **Signal logic:** Slow or absent patches on a live-service title = retention risk; monetization-heavy patches without content = churn signal
- *Discord announcement channels are deliberately excluded — reading them programmatically requires either a bot installed by each server's admins or automating a user account, which Discord's terms prohibit. The Steam news feed carries the same patch announcements for nearly every tracked title. See the risk register.*

---

### 3. Player Sentiment Agent
**Purpose:** Capture qualitative community mood that quantitative metrics lag behind.

- **Sources:**
  - Reddit (game-specific subreddits via the public read-only `.json` endpoints, accessed through the `RedditSource` adapter — the official Data API is not used; r/gaming is deliberately excluded as a low-signal, high-noise firehose)
  - YouTube (comments on patch notes, review, and developer-update videos via the **official YouTube Data API** — `commentThreads.list` costs 1 quota unit per 100 comments against a free 10,000-unit daily quota, so no scraping is needed; comments are disabled on a meaningful fraction of videos, which the agent treats as a normal miss)
  - Steam review text via the public `appreviews` JSON endpoint (with helpfulness weighting)
  - *X/Twitter is **deferred** — as of February 2026 the free API tier is discontinued and access for new developers is pay-per-use (~$0.005 per post read). A useful read volume runs $20–40/month. It's the first paid source to add if Reddit + YouTube + Steam coverage proves too thin for tracked titles; scraping and Nitter are not viable fallbacks (public Nitter instances have been dead since early 2024). Before paying for X, the free option to try is **Bluesky** — an official, open AT Protocol API with authenticated search on a free account and generous per-IP limits; smaller gaming community, zero access risk. Full analysis of both in the risk register.*
- **Method (hybrid VADER + LLM, aspect-based):** A baseline VADER pass (a rule-based scorer purpose-built for social media slang, emoji, and emphasis) produces a fast, reproducible polarity score. Claude then performs aspect-based sentiment analysis — extracting *aspect→sentiment* pairs like "monetization → negative, core gameplay → positive, server stability → negative" — which is far more actionable than a single whole-post score. Aspects are clustered into the top recurring themes.
- **Signal logic:** A **vocal-minority guard** weights by engagement and account credibility to flag when negativity is concentrated in a few high-activity accounts vs. broad-based. The agent may also emit a *preliminary* divergence note by comparing this week's text sentiment against **last week's** stored player metrics (clearly labeled as lagged).
- **Output:** Per-game sentiment score (1–10), top 3 aspect-themes with polarity, week-over-week trend, vocal-minority confidence note — persisted to Supabase as inputs to the Synthesis Agent's divergence check.
- **Division of labor — who computes divergence:** the sentiment agent runs *in parallel with* the quantitative workers, so it never sees the same week's player metrics. The authoritative **divergence check** (text sentiment vs. same-week quantitative signals — the system's alpha) is therefore computed by the **Synthesis Agent**, the only actor that reads all of the week's worker outputs together. This keeps the workers isolated and the comparison honest.

---

### 4. Studio Competitive Intelligence Agent
**Purpose:** Infer what studios are building and how healthy they are organizationally.

- **Sources:** Public job-board APIs (**Greenhouse Job Board API, Lever Postings API, Ashby posting API** — official, documented, no auth required for reads), studio careers pages via Playwright for the minority of studios not on a hosted ATS, press releases, official blogs, Steam store page changes, SEC filings (10-K/10-Q/8-K via EDGAR) and company investor-relations pages
- *LinkedIn is deliberately excluded: its terms prohibit scraping, its anti-bot defenses are aggressive, and the hosted-ATS APIs above cover most of the same postings with zero risk. See the risk register.*
- **Tracks:** Hiring spikes by role type (live-service, monetization, engine, QA), layoff announcements, executive departures, new project signals, partnership/acquisition rumors
- **Signal logic:** Hiring surge in live-service roles = new title in development or pivot; mass layoffs post-launch = studio in financial distress; executive churn = leadership instability

---

### 5. Financial Overlay Agent
**Purpose:** Map game-level signals to public equity positions.

- **Sources:** **yfinance** (primary for fundamentals-adjacent fields — P/E, earnings dates, analyst ratings, short interest; note it is an *unofficial* Yahoo Finance wrapper and gets the Tier-2 adapter treatment per the risk register), **Alpaca Market Data API** (official, free, ~200 req/min, same keys as the execution layer — used for prices and the S&P 500 benchmark via SPY bars, and the natural fallback if Yahoo throttles), SEC EDGAR for filings
- **Tracks:** Stock price, P/E ratio, earnings dates, analyst ratings, short interest
- **Maps:** Studios to parent tickers (e.g. Activision Blizzard → MSFT, Bungie → SONY, 2K/Rockstar → TTWO)
- **Signal logic:** Correlates game-level health scores with stock performance over time to identify pricing discrepancies and pre-earnings opportunities; writes weekly per-ticker rows to `EquitySignals`

---

### 6. Watchlist Management System
**Purpose:** Define and maintain the universe of games and studios the system actively tracks.

#### Initial Seeding (one-time, Claude-powered)
On first run, a seeding agent builds a comprehensive starting watchlist automatically. Claude queries IGDB and RAWG for:
- All publicly traded studio parent companies (EA, Take-Two, Sony, Microsoft, Nintendo, Ubisoft, etc.) and their associated game portfolios
- Top 100 games by Steam concurrent player count
- All active live-service titles with >10K concurrent players
- All major releases from the past 24 months

Claude then deduplicates, maps each game to its studio and parent ticker, and populates the `watchlist` table. The result is a broad, investment-relevant starting universe — roughly 150–300 games and 30–50 studios — without any manual entry. Each game with an active subreddit also gets a **sentiment tier** (see the RedditSource adapter's operational notes): Tier A titles get full post + comment collection; the long tail gets listing-only coverage so the weekly Reddit budget stays small.

#### Ongoing Discovery (weekly, semi-automatic)
A lightweight **Discovery Agent** runs alongside the main weekly pipeline and proposes new additions based on signals that suggest investment relevance:
- **Steam trending:** Games entering the top 50 by concurrent players or reviews that aren't already tracked
- **Upcoming releases:** Games with high follower/hype counts releasing in the next 60 days (sourced from the IGDB release calendar)
- **Studio news:** New studio acquisitions, spinoffs, or IPO filings detected via SEC EDGAR or press-release monitoring
- **Threshold crossings:** Untracked games that suddenly spike in Reddit mention volume

Proposed additions are written to a `watchlist_proposals` table with a `status` of `pending` and a Claude-generated rationale explaining why the entry is investment-relevant. They do not enter the active tracking pipeline until approved.

#### Human-in-the-Loop Approval
The Next.js dashboard surfaces pending proposals in a dedicated review queue. Each card shows the proposed game/studio, the trigger signal, Claude's rationale, and a quick-link to its Steam or IGDB page. One-click approve or reject. Approved entries flip to `active = true` and enter the tracking pipeline on the next weekly run. Rejected entries are logged so the Discovery Agent can learn which signals produce false positives over time.

**Additional tables:**
- **Watchlist:** game_id, studio_id, ticker, active, sentiment_tier, date_added, added_by (seed / discovery / manual)
- **WatchlistProposals:** proposal_id, game_id, studio_id, trigger_signal, claude_rationale, status (pending / approved / rejected), reviewed_at

---

### 7. Intelligence & Synthesis Layer (Claude Agent)
**Purpose:** Reason across all five data layers and produce actionable investment output.

- Ingests structured outputs from all worker subagents weekly
- **Owns the divergence check:** compares same-week text sentiment against same-week quantitative signals (player counts, review velocity, cadence). A game with stable player counts but collapsing text sentiment is an early churn warning the raw numbers miss — and because the workers run in parallel and isolated, this layer is the only place both sides of that comparison exist for the same week.
- Identifies convergence signals (e.g. declining players + negative sentiment + slow patches + studio layoffs = strong sell signal)
- Flags divergence opportunities (e.g. negative sentiment driven by a vocal minority while player counts hold = potential overreaction)
- Produces a structured weekly briefing: portfolio update, top opportunities, risk flags, notable events
- Maintains a reasoning log so outputs are auditable
- Can dispatch a one-off `deep-dive-researcher` subagent (with web access) to investigate an ambiguous signal without polluting its own context — returns a short findings summary

---

### 8. Portfolio Manager Agent + Execution Layer (Alpaca)
**Purpose:** Translate the weekly briefing into a structured trade plan and execute it against a paper trading account, then track weekly returns against a benchmark.

#### Why Alpaca
Alpaca is a developer-first brokerage with a full REST API purpose-built for algorithmic and agentic trading. Paper trading is free, requires only an email to sign up, starts with a $100K simulated balance, uses real-time IEX market data, and shares the exact same API spec as live trading — the only difference between paper and live is the endpoint and key. No university sponsorship, no scraping, no brittle form automation. Alpaca also ships an **official MCP server** (open-source, maintained by Alpaca, ~60+ tools as of the 2026 v2 rewrite, paper-trading mode on by default), meaning the Portfolio Manager Agent can eventually call Alpaca as a native MCP tool. At MVP on CrewAI, the same endpoints are wrapped as a thin `alpaca-py` tool — the MCP server becomes the integration path after the Agent SDK migration.

#### Portfolio Manager Agent (Claude-powered)
Runs immediately after the Synthesis Agent produces the weekly briefing. Claude reads:
- The current briefing (signal scores, risk flags, opportunity highlights)
- Current Alpaca paper portfolio positions and cash balance (fetched via the Alpaca API)
- Historical trade log and return performance from Supabase

Claude then reasons over the full picture and produces a structured **trade plan**:
- Positions to open (ticker, size, rationale, signal source)
- Positions to close or reduce (ticker, rationale)
- Positions to hold (with updated thesis)
- Overall portfolio risk posture for the week

The trade plan is written to a `trade_plans` table in Supabase with full Claude reasoning attached. It does **not** execute automatically.

#### Human-in-the-Loop Approval
The dashboard surfaces the trade plan in a dedicated approval UI before any order is placed. Each proposed trade shows: ticker, action, size, and the specific briefing signal that drove it. One-click approve or reject per trade, or bulk approve the full plan. Only approved trades proceed to execution.

#### Execution Agent
A deliberately thin, tightly-scoped subagent whose *only* tools are the Alpaca order endpoints and database writes. It reads approved trades from Supabase and places them — nothing else. Scoping its tool access this narrowly is a safety measure: it structurally *cannot* do anything but execute pre-approved orders. As a second layer, the **pre-trade guard lives inside the order-placement tool itself** — plain code that re-reads the trade's `status` from Supabase and refuses anything not `approved`. Because the check is in the tool, not the framework, it holds identically under CrewAI today and as a formal `before-tool-call` hook after the Agent SDK migration. Supports:
- Market and limit orders
- Fractional shares (for position sizing by dollar amount rather than share count)
- Order confirmation logging back to Supabase

#### Returns Tracker
After each weekly execution, a lightweight tracker fetches the current Alpaca portfolio state and writes it to Supabase:
- Portfolio value, cash balance, total return since inception
- Per-position P&L and holding period
- Weekly return vs. S&P 500 benchmark (SPY bars via the Alpaca data API, with yfinance as fallback)

The dashboard visualizes cumulative return, position breakdown, and a trade history log with the original Claude rationale for each trade — making the system's decision-making fully auditable.

**Additional tables:**
- **TradePlans:** plan_id, week_of, claude_rationale, status (pending / approved / rejected), reviewed_at
- **TradeOrders:** order_id, plan_id, ticker, action, size, alpaca_order_id, status, filled_at
- **PortfolioSnapshots:** snapshot_id, date, total_value, cash, total_return_pct, benchmark_return_pct
- **Positions:** position_id, ticker, qty, avg_entry_price, current_price, unrealized_pnl, signal_source

---

## Tech Stack

### Agent Orchestration: Orchestrator–Worker Pattern (CrewAI → LangGraph / Claude Agent SDK)
The system uses an **orchestrator–worker architecture**: a lead orchestrator reads the watchlist, dispatches specialized worker subagents in parallel, and each worker runs in its own isolated context window — only its structured return value crosses back to the orchestrator. This is the correct pattern here because the data workers don't need to communicate with each other mid-run (which would call for agent *teams* and billable inter-agent round-trips); they run independently, so isolated subagents are both cleaner and cheaper.

**CrewAI** (free, MIT) is the fastest path to a working multi-agent prototype — its role-based model maps directly onto the worker subagents. Start here. As the system matures and needs production-grade state management, conditional branching, retry logic, and lifecycle hooks, migrate to **LangGraph** (free, MIT) or the **Claude Agent SDK** (the same architecture that powers Claude Code, with first-class subagents, hooks, and skills). All integrate with LangSmith for observability.

**What the target vocabulary means at MVP:** the companion deep-dive is written in Agent SDK terms (skills, subagents, hooks) because that's the destination architecture. Under CrewAI, each maps to a plainer equivalent — a *skill* is a versioned methodology document injected into the agent's prompt (loaded in full; no progressive disclosure) with its bundled scripts exposed as ordinary tools; a *subagent* is a CrewAI agent with a deliberately restricted tool list; a *hook* is an explicit in-code check (the pre-trade guard lives inside the order tool itself). The full mapping table is in the deep-dive. Nothing about the architecture *depends* on SDK primitives — they formalize discipline the MVP already practices.

See the companion **Agent Components Deep-Dive** for the full internal spec of each agent — its subagents, skills, tools, and the analytical frameworks each skill encodes.

#### Three primitives, used deliberately
- **Tools** — atomic callable functions (fetch data, place an order). Provided via MCP servers or direct API wrappers.
- **Skills** — folders of instructions (a `SKILL.md` plus optional scripts) that Claude loads *only when relevant*, via progressive disclosure. Each agent's domain methodology lives in a skill, so it costs almost nothing in context until triggered. (MVP equivalent: versioned prompt documents, loaded in full.)
- **Subagents** — isolated Claude instances with their own context, system prompt, and *restricted tool access*. Used both for context isolation and as a safety guardrail (see the Execution Agent).

### LLM Layer: Claude API (Anthropic)
Claude handles all qualitative synthesis — sentiment scoring, briefing generation, and signal reasoning. The same Anthropic API used in Claude Code. **There is no ongoing free tier** — new accounts may receive small trial credits, but plan for usage-based billing from week one. With model tiering (below) and a weekly cadence, expect roughly **$10–25/month**: the five data workers on a Sonnet-class model dominate token volume but are cheap per token; the Opus-class synthesis and portfolio-manager steps are expensive per token but small. Per-subagent token logging (see Lifecycle Hooks) keeps the estimate honest.

### Scheduling: GitHub Actions (free)
Native cron scheduling via GitHub Actions eliminates the need for a separate scheduler service. Each agent run is triggered on schedule (e.g., weekly), executes, writes to the database, and exits. No Celery or Redis required at MVP scale. Two operational notes that matter here:
- **Make the repository public.** Public repos get unlimited Actions minutes; private repos get 2,000/month on the free plan, and the paced Reddit collection step alone (~45–90 min/run — see the adapter doc) would burn 10–25% of that. Public also strengthens the portfolio story. Keep every secret (Supabase service key, Alpaca keys, Anthropic key) in Actions **secrets** — never in the repo.
- **Scheduled workflows auto-disable after 60 days without repository activity.** Commits reset the clock; workflow runs do not. During active development this is a non-issue — once the project goes into maintenance mode, set a reminder or push a trivial commit monthly.

### Database: Supabase (free tier)
Supabase provides a managed PostgreSQL database, auto-generated REST API, authentication, and real-time subscriptions — all on a generous free tier (500MB database, 2 active projects, 50K monthly active users, unlimited API requests). The free tier is sufficient to validate the entire project. Its pgvector extension also supports future embedding-based similarity search if the system evolves toward RAG. It also backs a generic `api_cache` table (`SupabaseRedditCache`) used to cache and serve last-known-good payloads from volatile external sources.

**One real free-tier gotcha:** projects are **paused after 7 days without database activity**, and a weekly cron sits exactly on that boundary — one delayed run and the next one finds a paused database. Mitigation is trivial: a second, midweek GitHub Actions job that performs a single tiny read/write (a keepalive ping). Same pattern the community has standardized on; costs seconds of Actions time.

### Data & APIs
Full tiering, ToS posture, and fallbacks live in the **Data Source Risk Register**; this is the summary.

| Source | Tool | Cost | Tier |
|---|---|---|---|
| Player metrics | Steam Web API (official), IGDB, RAWG | Free | 1 |
| Patch notes | Steam news API (`ISteamNews`, official), dev blogs | Free | 1 |
| Stock & financial data | yfinance (unofficial wrapper) + Alpaca Market Data API (official) | Free | 2 / 1 |
| SEC filings | SEC EDGAR full-text search API | Free | 1 |
| Reddit sentiment | Public read-only `.json` endpoints via the `RedditSource` adapter (rate-limited, cached, graceful degradation) | Free | 2 |
| YouTube sentiment | YouTube Data API v3 (official; 10K units/day, comments = 1 unit per 100) | Free | 1 |
| Steam review text | Public `appreviews` JSON endpoint | Free | 2 |
| Job postings | Greenhouse / Lever / Ashby public board APIs (official, no auth) | Free | 1 |
| Web scraping (last resort) | Playwright — studio careers pages only | Free | 2 |
| Paper trading + market data | Alpaca Paper Trading API + free data plan | Free | 1 |
| X/Twitter sentiment | **Deferred** — pay-per-use API (~$0.005/post read as of Feb 2026); ~$20–40/mo at useful volume | Paid | 3 |
| LinkedIn, Discord | **Excluded** — ToS-prohibited access paths with official-API alternatives above | — | 4 |

**Source adapters & resilience.** Volatile or unofficial sources are wrapped behind a single swappable adapter interface rather than called directly, so a provider policy change touches one file instead of the whole pipeline. Reddit is the first case: with self-service Data API access closed off (Nov 2025 Responsible Builder Policy), the system reads the public, read-only `.json` endpoints through a `RedditSource` adapter that enforces a conservative request pace (well under the ~10 req/min per-IP unauthenticated ceiling), caches every payload in Supabase, and **degrades gracefully** — serving last-known-good data instead of failing the weekly run when Reddit throttles or blocks the (data-center) GitHub Actions IP. The same interface lets an alternate egress (proxy or managed scraper) drop in later with no downstream change. Every Tier-2 source in the register gets this treatment. See the *RedditSource Adapter* and *SupabaseRedditCache* design docs.

### Frontend: Next.js + shadcn/ui + Recharts
**Next.js 16** with the App Router is the current standard for data dashboards. Paired with **shadcn/ui** (free, MIT — components are copied into your project, no lock-in) and **Recharts** for charts, this stack produces a production-quality analytics dashboard without a template purchase. The open-source Shadcn Admin starter provides a complete foundation including sidebar navigation, data tables via TanStack Table, and dark mode out of the box.

### Hosting: Vercel (frontend) + Supabase (backend)
Vercel's free Hobby tier handles the Next.js dashboard with zero configuration. Supabase handles the database. Both are free at MVP scale with no credit card required to start.

### Observability: LangSmith (free developer plan)
LangSmith traces every agent decision — what prompt was sent, what tools were called, what the output was. The free developer plan covers MVP-scale usage. Because a single run can expand into dozens of turns across multiple subagents, this trace tree is essential for debugging (e.g. "why did the sentiment subagent return empty" or "why did synthesis burn 40K tokens").

### Model Tiering (cost discipline)
Match model to task rather than defaulting everything to the most capable model, and lock the model per subagent in its config so cost stays predictable:
- **Opus-class** — Synthesis Agent and Portfolio Manager (complex multi-signal reasoning)
- **Sonnet-class** — the data worker subagents (structured extraction + framework application)
- **Haiku-class** — trivial steps (simple classification, formatting)

### Lifecycle Hooks (safety + control)
Hooks fire at agent lifecycle points (before a tool call, after a response, on error). Used here for: a **pre-trade guard** that blocks any Alpaca order not marked `approved`, per-subagent token-spend logging, and graceful error recovery (retry or degrade rather than crash the weekly run). At MVP these are explicit in-code checks — the pre-trade guard, in particular, lives *inside* the order tool so it can't be bypassed by orchestration changes; the Agent SDK later formalizes the same checks as `before-tool-call` hooks.

---

## Data Model (Simplified)

- **Watchlist:** game_id, studio_id, ticker, active, sentiment_tier, date_added, added_by
- **WatchlistProposals:** proposal_id, game_id, studio_id, trigger_signal, claude_rationale, status, reviewed_at
- **Games:** game_id, title, studio_id, genre, release_date, is_live_service
- **PlayerMetrics:** game_id, date, concurrent_players, review_score, review_count
- **SentimentSnapshot:** game_id, date, source, sentiment_score, top_themes, flagged_events
- **ApiCache:** source, key, payload (JSONB), fetched_at — generic cache backing the source adapters (Reddit `.json` first; reusable for yfinance, Steam reviews, and other volatile sources)
- **PatchEvents:** game_id, date, patch_type, scope_summary, cadence_delta
- **StudioSignals:** studio_id, date, signal_type, description, severity
- **EquitySignals:** ticker, studio_id, date, health_score, current_signal, recommendation — the Financial Overlay Agent's weekly per-ticker output (formerly "PortfolioPositions," renamed to stop colliding with the broker-state `Positions` table below)
- **TradePlans:** plan_id, week_of, claude_rationale, status, reviewed_at
- **TradeOrders:** order_id, plan_id, ticker, action, size, alpaca_order_id, status, filled_at
- **PortfolioSnapshots:** snapshot_id, date, total_value, cash, total_return_pct, benchmark_return_pct
- **Positions:** position_id, ticker, qty, avg_entry_price, current_price, unrealized_pnl, signal_source — mirrors live Alpaca broker state; distinct from `EquitySignals`, which is analytical

---

## Agent Orchestration Flow

```
[ONE-TIME: Watchlist Seeding Agent]
  Claude queries IGDB, RAWG, Steam →
  Deduplicates + maps studios to tickers →
  Populates watchlist table (150–300 games, 30–50 studios)
        |
        v
[GitHub Actions: weekly cron trigger]        [midweek keepalive job → Supabase ping]
        |
        v
[Orchestrator — reads active watchlist, dispatches workers in parallel]
        |
        ├──────────────────────────────────────┐
        v                                      v
[Worker subagents (isolated contexts)]    [Discovery subagent]
  ├── Market & Player    → metrics          Scans Steam trending,
  ├── Sentiment          → score+themes     IGDB calendar, SEC EDGAR,
  ├── Patch Notes        → cadence          Reddit mention spikes →
  ├── Studio Intel       → org signals      Writes proposals to
  └── Financial Overlay  → EquitySignals    WatchlistProposals table
   (each returns only structured output)    with Claude rationale
        |                                      |
        v                                      v
[Structured outputs → Supabase]        [Dashboard: proposal review queue]
        |                                 User approves/rejects →
        v                                 Approved entries → active = true
[Synthesis Agent (Claude API)]
  Reads all worker returns →
  Computes the divergence check
  (same-week text vs. quant — only this
  layer sees both sides of the same week) →
  (may dispatch deep-dive-researcher subagent) →
  Produces weekly briefing:
  ├── Portfolio recommendations
  ├── Risk flags
  ├── Opportunity highlights
  └── Sentiment narratives
        |
        v
[Portfolio Manager Agent (Claude API)]
  Reads briefing + current Alpaca positions →
  Applies position-sizing-and-risk skill →
  Produces structured trade plan with rationale →
  Writes to TradePlans table (status: pending)
        |
        v
[Dashboard: trade plan approval UI]
  User reviews proposed trades →
  Approves / rejects per trade →
  Approved trades → status: approved
        |
        v
[Execution subagent — Alpaca tools ONLY]
  (in-tool pre-trade guard blocks anything not 'approved') →
  Places orders via Alpaca Paper Trading API →
  Logs order confirmations to TradeOrders table
        |
        v
[Returns Tracker]
  Fetches Alpaca portfolio state →
  Computes return vs. S&P 500 benchmark →
  Writes PortfolioSnapshots to Supabase →
  Dashboard: cumulative return, P&L, trade history

[LangSmith traces every agent decision throughout]
```

---

## Build Phases

Phases 1–5 are the core, resume-complete system; Phases 6–7 are stretch. The cut line is explicit below.

**Phase 1 — Foundation + Watchlist Seeding (Weeks 1–2)**
Set up the Supabase project and schema including the `watchlist` and `watchlist_proposals` tables. Make the GitHub repo public and put all keys in Actions secrets. Build the one-time Claude-powered seeding agent that queries IGDB, RAWG, and Steam to populate the initial watchlist (including per-game sentiment tiers). Scaffold the CrewAI crew with placeholder agents. Set up the weekly GitHub Actions cron plus the midweek Supabase keepalive job. Verify the watchlist is populated and data is flowing.

**Phase 2 — Sentiment Layer (Weeks 3–4)**
Build the `RedditSource` adapter — unauthenticated read-only `.json` endpoints, with rate limiting, Supabase-backed caching (`SupabaseRedditCache`), and graceful degradation when blocked. Wire the YouTube Data API comment collector (official API — no scraping) and the Steam `appreviews` reader. Build the hybrid sentiment pipeline: VADER baseline pass plus the Claude-powered aspect-based analysis (`sentiment-analysis-methodology` skill). Establish per-game scoring and aspect-theme extraction, and persist the structured outputs the Synthesis Agent's divergence check will consume in Phase 4. (X integration is deliberately absent — see the risk register.)

**Phase 3 — Studio & Financial Intelligence (Weeks 5–6)**
Build the Greenhouse/Lever/Ashby job-board API clients (Playwright only for studios without a hosted board — never LinkedIn), map studios to tickers, integrate yfinance + Alpaca market data + SEC EDGAR. Build the financial overlay agent, the `EquitySignals` writes, and the signal correlation logic.

**Phase 4 — Synthesis Agent & Briefing (Weeks 7–8)**
Build the master Claude synthesis agent that reads all Supabase outputs, computes the same-week divergence check, and produces the weekly briefing. Add LangSmith tracing. Set up email delivery of the briefing (e.g., Resend's free tier).

**Phase 5 — Portfolio Manager + Alpaca Execution (Weeks 9–10)**
Set up the Alpaca paper trading account and configure API keys. Build the Portfolio Manager Agent that reads the weekly briefing and produces a structured trade plan. Build a minimal trade-plan approval UI (a simple table view is enough at this stage). Build the Execution Agent with the in-tool pre-trade guard. Implement the Returns Tracker (weekly snapshots, return vs. S&P 500). This phase completes the closed loop — signals → briefing → approved trades → tracked returns — which is the resume centerpiece, so it runs *before* the stretch phases.

**— Cut line — everything below is stretch scope.** Fourteen weeks solo is ambitious; if the calendar slips, cut from the bottom. Phases 1–5 alone are the full, demonstrable, closed-loop system.

**Phase 6 — Discovery Agent (Weeks 11–12)**
Build the Discovery Agent that runs weekly alongside the main pipeline, scanning Steam trending, the IGDB calendar, SEC EDGAR, and Reddit mention spikes for new candidates. Wire Claude rationale generation and `watchlist_proposals` writes. Build the proposal review queue UI in the dashboard (it reuses the trade-approval components from Phase 5).

**Phase 7 — Dashboard Polish (Weeks 13–14)**
Build out the full Next.js + shadcn/ui dashboard using the open-source Shadcn Admin starter. Add the portfolio view, per-game signal cards, Recharts-powered sentiment trend charts, the weekly briefing feed, the cumulative-return chart, position breakdown, and the auditable trade-history log. Deploy to Vercel.

---

## Resume Framing

> "Built a closed-loop multi-agent investment intelligence platform for the games industry, integrating Steam, Reddit, YouTube, SEC EDGAR, and brokerage APIs across a pipeline of five parallel data-collection agents plus seeding, discovery, synthesis, portfolio-management, and execution agents. Designed a Claude-powered seeding and discovery system that autonomously builds and expands a tracked universe of 150–300 games and 30–50 studios with human-in-the-loop approval, and a risk-tiered source-adapter layer that keeps the pipeline running through provider blocks and policy changes. Implemented a synthesis and portfolio management layer that translates game-level signals into weekly trade plans, executes approved orders via the Alpaca Paper Trading API, and tracks weekly returns against an S&P 500 benchmark — with full decision auditability through LangSmith."

Ties directly to: AI Solutions Architect goal, Stock-Trak macro strategy experience, Sony SIE business analysis, and end-to-end agentic system design.
