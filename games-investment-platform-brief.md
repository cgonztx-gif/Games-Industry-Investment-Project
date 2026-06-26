# Games Industry Investment Intelligence Platform
### Project Brief

---

## Overview

A multi-agent investment intelligence system that monitors the games industry across product, community, and financial data layers, then synthesizes signals into a weekly portfolio briefing. The core thesis: game-level data (player counts, sentiment, patch cadence, studio hiring) is a leading indicator of financial performance that traditional investors underweight.

> **Companion document:** the *Agent Components Deep-Dive* specs out the internals of every agent — its subagents, skills (with the analytical frameworks each encodes), tools, and the cross-cutting infrastructure (hooks, model tiering, observability). This brief is the system-level overview; that document is the implementation reference.

---

## Goals

- Aggregate quantitative and qualitative data across games, studios, and public equities
- Detect early signals (positive and negative) before they show up in earnings
- Generate a structured weekly briefing with portfolio recommendations and risk flags
- Build a dashboard to visualize trends over a tracked portfolio of studios/tickers

---

## Components

### 1. Market & Player Data Agent
**Purpose:** Track real-time product health via player engagement metrics.

- **Sources:** Steam API, IGDB, RAWG
- **Tracks:** Concurrent player counts, review scores, review velocity, wishlist momentum, game release calendars
- **Signal logic:** Sustained player decline + slowing review rate = deteriorating product health; wishlist spikes ahead of release = demand signal

---

### 2. Patch Notes & Update Cadence Agent
**Purpose:** Infer developer investment and live-service commitment from update behavior.

- **Sources:** Steam update RSS feeds, official developer blogs, Discord announcement channels
- **Tracks:** Update frequency, patch size/scope, content type (bug fix vs. content drop vs. monetization change)
- **Signal logic:** Slow or absent patches on a live-service title = retention risk; monetization-heavy patches without content = churn signal

---

### 3. Player Sentiment Agent
**Purpose:** Capture qualitative community mood that quantitative metrics lag behind.

- **Sources:**
  - Reddit (r/gaming, game-specific subreddits via Reddit API)
  - X/Twitter (developer accounts, gaming journalists, trending game hashtags)
  - YouTube (comment scraping on patch notes videos, review videos, developer update videos)
  - Steam discussion boards and review text
- **Method (hybrid VADER + LLM, aspect-based):** A baseline VADER pass (a rule-based scorer purpose-built for social media slang, emoji, and emphasis) produces a fast, reproducible polarity score. Claude then performs aspect-based sentiment analysis — extracting *aspect→sentiment* pairs like "monetization → negative, core gameplay → positive, server stability → negative" — which is far more actionable than a single whole-post score. Aspects are clustered into the top recurring themes.
- **Signal logic:** The key edge is the **divergence check** — comparing text-derived sentiment against the quantitative signals. A game with stable player counts but collapsing text sentiment is an early churn warning the raw numbers miss. A **vocal-minority guard** weights by engagement and account credibility to flag when negativity is concentrated in a few high-activity accounts vs. broad-based.
- **Output:** Per-game sentiment score (1–10), top 3 aspect-themes with polarity, week-over-week trend, divergence flag, vocal-minority confidence note.

---

### 4. Studio Competitive Intelligence Agent
**Purpose:** Infer what studios are building and how healthy they are organizationally.

- **Sources:** LinkedIn/Greenhouse job postings, press releases, official blogs, Steam store page changes, earnings call transcripts
- **Tracks:** Hiring spikes by role type (live-service, monetization, engine, QA), layoff announcements, executive departures, new project signals, partnership/acquisition rumors
- **Signal logic:** Hiring surge in live-service roles = new title in development or pivot; mass layoffs post-launch = studio in financial distress; executive churn = leadership instability

---

### 5. Financial Overlay Agent
**Purpose:** Map game-level signals to public equity positions.

- **Sources:** Polygon.io or Yahoo Finance API for stock data; SEC EDGAR for earnings filings
- **Tracks:** Stock price, P/E ratio, earnings dates, analyst ratings, short interest
- **Maps:** Studios to parent tickers (e.g. Activision Blizzard → MSFT, Bungie → SONY, 2K/Rockstar → TTWO)
- **Signal logic:** Correlates game-level health scores with stock performance over time to identify pricing discrepancies and pre-earnings opportunities

---

### 6. Watchlist Management System
**Purpose:** Define and maintain the universe of games and studios the system actively tracks.

#### Initial Seeding (one-time, Claude-powered)
On first run, a seeding agent builds a comprehensive starting watchlist automatically. Claude queries IGDB and RAWG for:
- All publicly traded studio parent companies (EA, Take-Two, Sony, Microsoft, Nintendo, Ubisoft, etc.) and their associated game portfolios
- Top 100 games by Steam concurrent player count
- All active live-service titles with >10K concurrent players
- All major releases from the past 24 months

Claude then deduplicates, maps each game to its studio and parent ticker, and populates the `watchlist` table. The result is a broad, investment-relevant starting universe — roughly 150–300 games and 30–50 studios — without any manual entry.

#### Ongoing Discovery (weekly, semi-automatic)
A lightweight **Discovery Agent** runs alongside the main weekly pipeline and proposes new additions based on signals that suggest investment relevance:
- **Steam trending:** Games entering the top 50 by concurrent players or reviews that aren't already tracked
- **Upcoming releases:** Games with high wishlist counts releasing in the next 60 days (sourced from IGDB release calendar)
- **Studio news:** New studio acquisitions, spinoffs, or IPO filings detected via SEC EDGAR or press release scraping
- **Threshold crossings:** Untracked games that suddenly spike in Reddit mentions or X engagement

Proposed additions are written to a `watchlist_proposals` table with a `status` of `pending` and a Claude-generated rationale explaining why the entry is investment-relevant. They do not enter the active tracking pipeline until approved.

#### Human-in-the-Loop Approval
The Next.js dashboard surfaces pending proposals in a dedicated review queue. Each card shows the proposed game/studio, the trigger signal, Claude's rationale, and a quick-link to its Steam or IGDB page. One-click approve or reject. Approved entries flip to `active = true` and enter the tracking pipeline on the next weekly run. Rejected entries are logged so the Discovery Agent can learn which signals produce false positives over time.

**Additional tables:**
- **Watchlist:** game_id, studio_id, ticker, active, date_added, added_by (seed / discovery / manual)
- **WatchlistProposals:** proposal_id, game_id, studio_id, trigger_signal, claude_rationale, status (pending / approved / rejected), reviewed_at

---

### 7. Intelligence & Synthesis Layer (Claude Agent)
**Purpose:** Reason across all five data layers and produce actionable investment output.

- Ingests structured outputs from all worker subagents weekly
- Identifies convergence signals (e.g. declining players + negative sentiment + slow patches + studio layoffs = strong sell signal)
- Flags divergence opportunities (e.g. negative sentiment driven by vocal minority while player counts hold = potential overreaction)
- Produces a structured weekly briefing: portfolio update, top opportunities, risk flags, notable events
- Maintains a reasoning log so outputs are auditable
- Can dispatch a one-off `deep-dive-researcher` subagent (with web access) to investigate an ambiguous signal without polluting its own context — returns a short findings summary

---

### 8. Portfolio Manager Agent + Execution Layer (Alpaca)
**Purpose:** Translate the weekly briefing into a structured trade plan and execute it against a paper trading account, then track real-time returns.

#### Why Alpaca
Alpaca is a developer-first brokerage with a full REST API purpose-built for algorithmic and agentic trading. Paper trading is free, requires only an email to sign up, starts with a $100K simulated balance, uses real-time IEX market data, and shares the exact same API spec as live trading — the only difference between paper and live is the endpoint and key. No university sponsorship, no scraping, no brittle form automation. Alpaca also exposes an official MCP server, meaning the Portfolio Manager Agent can call Alpaca's API as a native tool call rather than a custom integration.

#### Portfolio Manager Agent (Claude-powered)
Runs immediately after the Synthesis Agent produces the weekly briefing. Claude reads:
- The current briefing (signal scores, risk flags, opportunity highlights)
- Current Alpaca paper portfolio positions and cash balance (fetched via Alpaca API)
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
A deliberately thin, tightly-scoped subagent whose *only* tools are the Alpaca order endpoints and database writes. It reads approved trades from Supabase and places them — nothing else. Scoping its tool access this narrowly is a safety measure: it structurally *cannot* do anything but execute pre-approved orders. A `before-tool-call` lifecycle hook adds a second layer, hard-blocking any order whose `status` isn't `approved`. Supports:
- Market and limit orders
- Fractional shares (for position sizing by dollar amount rather than share count)
- Order confirmation logging back to Supabase

#### Returns Tracker
After each weekly execution, a lightweight tracker fetches the current Alpaca portfolio state and writes it to Supabase:
- Portfolio value, cash balance, total return since inception
- Per-position P&L and holding period
- Weekly return vs. S&P 500 benchmark (fetched via yfinance)

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

See the companion **Agent Components Deep-Dive** for the full internal spec of each agent — its subagents, skills, tools, and the analytical frameworks each skill encodes.

#### Three primitives, used deliberately
- **Tools** — atomic callable functions (fetch data, place an order). Provided via MCP servers or direct API wrappers.
- **Skills** — folders of instructions (a `SKILL.md` plus optional scripts) that Claude loads *only when relevant*, via progressive disclosure. Each agent's domain methodology lives in a skill, so it costs almost nothing in context until triggered.
- **Subagents** — isolated Claude instances with their own context, system prompt, and *restricted tool access*. Used both for context isolation and as a safety guardrail (see the Execution Agent).

### LLM Layer: Claude API (Anthropic)
Claude handles all qualitative synthesis — sentiment scoring, briefing generation, and signal reasoning. The same Anthropic API used in Claude Code. Anthropic's free tier covers early development; usage-based billing applies as agent runs scale.

### Scheduling: GitHub Actions (free)
Native cron scheduling via GitHub Actions eliminates the need for a separate scheduler service. Each agent run is triggered on schedule (e.g., weekly), executes, writes to the database, and exits. Free for public repos; 2,000 minutes/month on private repos. No Celery or Redis required at MVP scale.

### Database: Supabase (free tier)
Supabase provides a managed PostgreSQL database, auto-generated REST API, authentication, and real-time subscriptions — all on a generous free tier (500MB DB, 2 projects, 50K monthly active users). The free tier is sufficient to validate the entire project. Its pgvector extension also supports future embedding-based similarity search if the system evolves toward RAG.

### Data & APIs
| Source | Tool | Cost |
|---|---|---|
| Player metrics | Steam API, IGDB, RAWG | Free |
| Stock & financial data | Yahoo Finance (yfinance Python lib) | Free |
| SEC filings | SEC EDGAR full-text search API | Free |
| Reddit sentiment | Reddit API (PRAW) | Free |
| X/Twitter sentiment | X Basic API (limited) or Nitter RSS | Free / limited |
| Web scraping | Playwright (headless browser, job boards, YouTube comments) | Free |
| Paper trading execution | Alpaca Paper Trading API | Free |

### Frontend: Next.js + shadcn/ui + Recharts
**Next.js 16** with the App Router is the current standard for data dashboards. Paired with **shadcn/ui** (free, MIT — components are copied into your project, no lock-in) and **Recharts** for charts, this stack produces a production-quality analytics dashboard without a template purchase. The Shadcn Admin open-source starter (~6K GitHub stars) provides a complete foundation including sidebar navigation, data tables via TanStack Table, and dark mode out of the box.

### Observability: LangSmith (free developer plan)
LangSmith traces every agent decision — what prompt was sent, what tools were called, what the output was. Free developer plan covers MVP-scale usage. Because a single run can expand into dozens of turns across multiple subagents, this trace tree is essential for debugging (e.g. "why did the sentiment subagent return empty" or "why did synthesis burn 40K tokens").

### Model Tiering (cost discipline)
Match model to task rather than defaulting everything to the most capable model, and lock the model per subagent in its config so cost stays predictable:
- **Opus-class** — Synthesis Agent and Portfolio Manager (complex multi-signal reasoning)
- **Sonnet-class** — the data worker subagents (structured extraction + framework application)
- **Haiku-class** — trivial steps (simple classification, formatting)

### Lifecycle Hooks (safety + control)
Hooks fire at agent lifecycle points (before a tool call, after a response, on error). Used here for: a **pre-trade guard** that blocks any Alpaca order not marked `approved`, per-subagent token-spend logging, and graceful error recovery (retry or degrade rather than crash the weekly run).

### Hosting: Vercel (frontend) + Supabase (backend)
Vercel's free Hobby tier handles the Next.js dashboard with zero configuration. Supabase handles the database. Both are free at MVP scale with no credit card required to start.

---

## Data Model (Simplified)

- **Watchlist:** game_id, studio_id, ticker, active, date_added, added_by
- **WatchlistProposals:** proposal_id, game_id, studio_id, trigger_signal, claude_rationale, status, reviewed_at
- **Games:** game_id, title, studio_id, genre, release_date, is_live_service
- **PlayerMetrics:** game_id, date, concurrent_players, review_score, review_count
- **SentimentSnapshot:** game_id, date, source, sentiment_score, top_themes, flagged_events
- **PatchEvents:** game_id, date, patch_type, scope_summary, cadence_delta
- **StudioSignals:** studio_id, date, signal_type, description, severity
- **PortfolioPositions:** ticker, studio_id, entry_date, current_signal, recommendation
- **TradePlans:** plan_id, week_of, claude_rationale, status, reviewed_at
- **TradeOrders:** order_id, plan_id, ticker, action, size, alpaca_order_id, status, filled_at
- **PortfolioSnapshots:** snapshot_id, date, total_value, cash, total_return_pct, benchmark_return_pct
- **Positions:** position_id, ticker, qty, avg_entry_price, current_price, unrealized_pnl, signal_source

---

## Agent Orchestration Flow

```
[ONE-TIME: Watchlist Seeding Agent]
  Claude queries IGDB, RAWG, Steam →
  Deduplicates + maps studios to tickers →
  Populates watchlist table (150–300 games, 30–50 studios)
        |
        v
[GitHub Actions: weekly cron trigger]
        |
        v
[Orchestrator — reads active watchlist, dispatches workers in parallel]
        |
        ├──────────────────────────────────────┐
        v                                      v
[Worker subagents (isolated contexts)]    [Discovery subagent]
  ├── Market & Player    → metrics          Scans Steam trending,
  ├── Sentiment          → score+themes     IGDB calendar, SEC EDGAR,
  ├── Patch Notes        → cadence          Reddit/X spikes →
  ├── Studio Intel       → org signals      Writes proposals to
  └── Financial Overlay  → valuation        WatchlistProposals table
   (each returns only structured output)    with Claude rationale
        |                                      |
        v                                      v
[Structured outputs → Supabase]        [Dashboard: proposal review queue]
        |                                 User approves/rejects →
        v                                 Approved entries → active = true
[Synthesis Agent (Claude API)]
  Reads all worker returns →
  (may dispatch deep-dive-researcher subagent) →
  Produces weekly briefing:
  ├── Portfolio recommendations
  ├── Risk flags
  ├── Opportunity highlights
  └── Sentiment narratives
        |
        v
[Portfolio Manager Agent (Claude API)]
  Reads briefing + current Alpaca positions (via Alpaca MCP) →
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
  (before-tool-call hook blocks anything not 'approved') →
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

**Phase 1 — Foundation + Watchlist Seeding (Weeks 1–2)**
Set up Supabase project and schema including the `watchlist` and `watchlist_proposals` tables. Build the one-time Claude-powered seeding agent that queries IGDB, RAWG, and Steam to populate the initial watchlist. Scaffold the CrewAI crew with placeholder agents. Set up GitHub Actions cron trigger. Verify the watchlist is populated and data is flowing.

**Phase 2 — Sentiment Layer (Weeks 3–4)**
Integrate Reddit API (PRAW) and X API. Build the hybrid sentiment pipeline: VADER baseline pass plus the Claude-powered aspect-based analysis (`sentiment-analysis-methodology` skill). Establish per-game scoring, aspect-theme extraction, and the divergence check against quantitative signals. Add Playwright scraper for Steam discussion boards and YouTube comments.

**Phase 3 — Studio & Financial Intelligence (Weeks 5–6)**
Add Playwright-based job posting scraper, map studios to tickers, integrate yfinance and SEC EDGAR. Build the financial overlay agent and signal correlation logic.

**Phase 4 — Synthesis Agent & Briefing (Weeks 7–8)**
Build the master Claude synthesis agent that reads all Supabase outputs and produces the weekly briefing. Add LangSmith tracing. Set up email delivery.

**Phase 5 — Discovery Agent (Weeks 9–10)**
Build the Discovery Agent that runs weekly alongside the main pipeline, scanning Steam trending, IGDB calendar, SEC EDGAR, and social spikes for new candidates. Wire Claude rationale generation and `watchlist_proposals` writes. Build the proposal review queue UI in the dashboard.

**Phase 6 — Dashboard Polish (Weeks 11–12)**
Build out the full Next.js + shadcn/ui dashboard using the open-source Shadcn Admin starter. Add portfolio view, per-game signal cards, Recharts-powered sentiment trend charts, weekly briefing feed, and proposal review queue. Set up email delivery for the weekly briefing. Deploy to Vercel.

**Phase 7 — Portfolio Manager + Alpaca Execution (Weeks 13–14)**
Set up Alpaca paper trading account and configure API keys. Build the Portfolio Manager Agent that reads the weekly briefing and produces a structured trade plan. Build the trade plan approval UI in the dashboard. Build the Execution Agent that places approved trades via the Alpaca API. Implement the Returns Tracker that fetches portfolio snapshots weekly and computes return vs. S&P 500 benchmark. Wire the cumulative return chart, position breakdown, and auditable trade history log into the dashboard.

---

## Resume Framing

> "Built a closed-loop multi-agent investment intelligence platform for the games industry, integrating Steam, Reddit, X, and financial APIs across eight specialized agents. Designed a Claude-powered seeding and discovery system that autonomously builds and expands a tracked universe of 150–300 games and 30–50 studios with human-in-the-loop approval. Implemented a synthesis and portfolio management layer that translates game-level signals into weekly trade plans, executes approved orders via the Alpaca Paper Trading API, and tracks real-time returns against an S&P 500 benchmark — with full decision auditability through LangSmith."

Ties directly to: AI Solutions Architect goal, Stock-Trak macro strategy experience, Sony SIE business analysis, and end-to-end agentic system design.
