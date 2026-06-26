# Agent Components Deep-Dive
### Subagents, Skills, Tools, and Analytical Frameworks

This document specs out the *internals* of each agent in the Games Industry Investment Intelligence Platform. It complements the main project brief by answering: what is a subagent vs. a skill vs. a tool, which ones each agent needs, and what proven analytical methodologies the skills should encode.

---

## First: The Three Primitives (and when to use each)

The Claude Agent SDK exposes three building blocks. Choosing the wrong one is the most common architecture mistake, so the rule of thumb matters:

> **If you're writing instructions, it's a skill. If you're writing a function the model calls, it's a tool. If the work needs its own context window, its own system prompt, or its own tool restrictions, it's a subagent.**

| Primitive | What it is | When to use it | How it's defined |
|---|---|---|---|
| **Tool** | An atomic, callable function with a fixed interface and no internal decision-making — like a system call | Fetching data, placing an order, running a calculation | A function (often via MCP server) |
| **Skill** | A folder of instructions + optional scripts that Claude loads *only when relevant* | Encoding a methodology, a domain framework, or a repeatable workflow | A `SKILL.md` file in `.claude/skills/` |
| **Subagent** | A named, isolated Claude instance with its own context window, system prompt, and tool access | Isolating a noisy or specialized task so the parent context stays clean; running work in parallel | YAML config (or filesystem) |

### Why this separation matters for *this* project

- **Context isolation:** A sentiment analysis run might read 200 Reddit posts. You don't want 200 posts polluting the main synthesis agent's context. Delegate to a sentiment subagent — only its final structured output (a score + themes) returns to the parent.
- **Progressive disclosure (skills):** Claude only loads a skill's full body when the task matches its description. You can attach a comprehensive 5-page "live-service health analysis" methodology as a skill, and it costs almost nothing in context until it's actually triggered.
- **Determinism (tools/scripts):** Sorting, computing a churn rate, or normalizing a sentiment score should be code, not token generation. Skills can bundle Python scripts for exactly this.
- **Parallelism:** The five data agents have no need to talk to each other mid-run, which is precisely the condition where subagents (not agent teams) are the right call. Agent teams add billable round-trips and are only worth it when workers must share discoveries mid-task.

---

## Architecture Overview

The system uses an **orchestrator–worker pattern**: a lead orchestrator coordinates and delegates to specialized subagents that run in parallel, each with an isolated context. Only each subagent's return value crosses back to the orchestrator.

```
[Orchestrator Agent]
   │  (reads watchlist, dispatches workers in parallel)
   │
   ├── Market & Player Subagent ──────► returns structured metrics
   ├── Sentiment Subagent ────────────► returns score + themes
   ├── Patch Notes Subagent ──────────► returns cadence analysis
   ├── Studio Intelligence Subagent ──► returns org-health signals
   ├── Financial Overlay Subagent ────► returns valuation context
   └── Discovery Subagent ────────────► returns watchlist proposals
   │
   ▼
[Synthesis Agent]  ── reads all returns ──► weekly briefing
   │
   ▼
[Portfolio Manager Agent] ──► trade plan ──► [Execution Agent (Alpaca)]
```

A note on a documented SDK constraint: **subagents cannot spawn their own subagents.** So the design is strictly two levels deep — orchestrator → workers. Anything that looks like it needs a third level should instead be a skill loaded by the worker.

---

## Agent 1: Market & Player Subagent

**Role:** Collect and interpret quantitative engagement metrics.

### Tools
- `steam_api` — concurrent players, review counts/scores (via SteamCharts / Steam Web API)
- `igdb_api` — release dates, genre, platform metadata
- `rawg_api` — supplementary catalog and rating data
- `db_write` — persist to Supabase `PlayerMetrics`

### Skill: `live-service-health-analysis`
This is the most important skill in the data layer. It encodes the actual KPI framework that game analysts and investors use, so the agent interprets numbers rather than just storing them.

The skill's `SKILL.md` would encode:
- **Engagement KPIs:** Concurrent Users (CCU/PCU), DAU/MAU and the DAU/MAU "stickiness" ratio
- **Retention benchmarks:** D1 (~35–40% healthy), D7, D30 (~5–10% healthy for the genre), and how to read a retention *curve* shape vs. a single number
- **Trend logic:** what a sustained PCU decline means vs. a seasonal dip; how to distinguish a content-drop spike from organic growth
- **Genre-relative interpretation:** a 5% D30 means different things for a hero shooter vs. a cozy sim — the skill includes genre baselines
- A bundled Python script for computing week-over-week deltas, rolling averages, and decline-rate flags deterministically

**Why a skill, not just code:** the *interpretation* layer ("is this decline a red flag?") is judgment that benefits from Claude reasoning over an encoded framework, while the *math* is a script the skill carries.

---

## Agent 2: Sentiment Subagent

**Role:** Convert unstructured community chatter into a structured, defensible sentiment read. This is the agent with the deepest analytical method, because naive sentiment scoring is notoriously misleading.

### Tools
- `reddit_api` (PRAW) — fetch top/hot posts and comments from game subreddits
- `x_api` — fetch posts from developer accounts, journalists, hashtags
- `youtube_scraper` (Playwright) — comment extraction on patch/review/update videos
- `steam_reviews_api` — review text + helpfulness weighting
- `vader_score` — bundled deterministic lexicon scorer (baseline)

### Skill: `sentiment-analysis-methodology`
The research is clear that the best-in-class approach is a **hybrid VADER + LLM pipeline with aspect-based analysis**, not either alone. The skill encodes this methodology:

1. **Baseline pass (VADER, deterministic):** VADER is the gold-standard rule-based scorer purpose-built for social media — it handles slang, emoji, capitalization, and degree modifiers. Run it first to get a fast, reproducible polarity baseline and to flag the magnitude of sentiment per post.

2. **Aspect-Based Sentiment Analysis (ABSA, Claude-powered):** Rather than scoring a whole post as "negative," extract *aspect–sentiment* pairs — e.g. "monetization → negative," "core gameplay → positive," "server stability → negative." This is far more actionable because it tells you *what* players are reacting to. LLMs have largely replaced dedicated ABSA models here.

3. **Thematic clustering:** Group aspects into recurring themes across the corpus (e.g. "battle pass pricing backlash," "matchmaking complaints") and surface the top 3 by volume and intensity.

4. **The divergence check (the alpha):** Compute the gap between text-derived sentiment and the quantitative signals. A documented technique — "beyond stars" — is measuring where review *text* sentiment diverges from the *star rating* or player count. A game with stable player counts but collapsing text sentiment is an early churn warning the raw numbers miss.

5. **Vocal-minority guard:** Weight by engagement and account credibility; flag when negativity is concentrated in a small number of high-activity accounts vs. broad-based. (Research on social platforms shows super-active and bot-like users can skew sentiment disproportionately.)

6. **Consistency checks:** Lightweight rule-based validation so scores stay stable across different game vocabularies.

**Output contract:** per-game sentiment score (1–10), top 3 aspect-themes with polarity, week-over-week trend, divergence flag, and a vocal-minority confidence note.

**Why this lives in a subagent:** reading hundreds of posts is exactly the high-volume, noisy work that should be isolated. Only the structured output returns to the orchestrator.

---

## Agent 3: Patch Notes & Update Cadence Subagent

**Role:** Infer developer investment and live-service commitment from update behavior.

### Tools
- `steam_rss` — official update feed per title
- `web_fetch` — developer blogs, official patch pages
- `discord_scraper` (Playwright) — announcement channels where applicable
- `db_write` — persist to `PatchEvents`

### Skill: `patch-cadence-analysis`
Encodes the framework for reading update behavior as a financial signal:
- **Classification taxonomy:** every patch tagged as hotfix / balance / content drop / monetization change / engine update
- **Cadence baselines:** what a healthy live-service update rhythm looks like by game age and genre; how to detect a slowing cadence
- **The monetization-without-content flag:** a documented churn antecedent — patches that add store items or battle passes without commensurate content signal a studio milking a declining title. Research on retention explicitly calls out overloading monetization as a trust-breaker.
- **Roadmap-adherence tracking:** comparing promised roadmap beats to shipped patches as a leadership-reliability signal

---

## Agent 4: Studio Competitive Intelligence Subagent

**Role:** Infer what studios are building and how organizationally healthy they are.

### Tools
- `job_board_scraper` (Playwright) — LinkedIn / Greenhouse postings
- `web_fetch` — press releases, official blogs, Steam store page diffs
- `sec_edgar_api` — filings, earnings transcripts for public parents
- `db_write` — persist to `StudioSignals`

### Skill: `org-health-signal-analysis`
Encodes how to read organizational signals:
- **Hiring-signal taxonomy:** role-type spikes mapped to intent (live-service/monetization roles → new live title or pivot; engine roles → new tech investment; QA surge → imminent launch)
- **Distress indicators:** layoffs post-launch, executive departures, studio consolidation — scored by severity
- **Leadership-stability index:** executive churn rate as a leading risk signal
- **Acquisition/IPO detection:** parsing filings and press releases for ownership changes that re-map a studio to a different ticker

---

## Agent 5: Financial Overlay Subagent

**Role:** Map game-level signals to public equity context.

### Tools
- `yfinance` — price, P/E, earnings dates, analyst ratings, short interest
- `sec_edgar_api` — fundamentals from filings
- `db_write` — persist to `PortfolioPositions` context tables

### Skill: `equity-signal-mapping`
- **Studio→ticker resolution:** the canonical mapping table and the logic for parents with multiple studios (a single game signal must be weighted by that title's materiality to total parent revenue — a hit indie game barely moves a mega-cap)
- **Materiality weighting:** framework for how much a game-level signal *should* move the thesis given the parent's size and portfolio
- **Pre-earnings windows:** flag when a tracked title shows divergence inside the 3–4 weeks before a parent's earnings date — the highest-signal moment
- **Correlation tracking:** rolling correlation between game-health scores and ticker performance to validate (or invalidate) the thesis over time

---

## Agent 6: Discovery Subagent

**Role:** Propose new watchlist entries. Runs in parallel with the main pipeline.

### Tools
- `steam_api` — trending / top-by-CCU charts
- `igdb_api` — upcoming release calendar with wishlist signals
- `sec_edgar_api` — new filings (IPOs, acquisitions)
- `reddit_api` / `x_api` — mention-volume spikes for untracked titles
- `db_write` — write to `WatchlistProposals`

### Skill: `watchlist-relevance-scoring`
- **Investment-relevance criteria:** the rubric for what makes a game worth tracking (public parent, material player base, live-service model, etc.)
- **Trigger thresholds:** what counts as a meaningful trending spike vs. noise
- **Rationale generation:** a structured template so every proposal arrives with a consistent, reviewable justification
- **False-positive learning:** reads the log of past rejections to tighten criteria over time

---

## Agent 7: Synthesis Agent (Orchestrator-level)

**Role:** Reason across all worker outputs and produce the weekly briefing. This is *not* a subagent — it's the lead reasoning step that consumes the workers' structured returns.

### Tools
- `db_read` — pull the week's structured outputs from all tables
- `db_write` — persist the briefing

### Skill: `investment-synthesis-framework`
- **Convergence logic:** the rules for combining signals — e.g. declining players + negative aspect-sentiment + slow cadence + studio layoffs = high-conviction sell; each individually = watch
- **Divergence-opportunity logic:** negative sentiment from a vocal minority while player counts and fundamentals hold = potential overreaction / contrarian setup
- **Confidence scoring:** how to weight conflicting signals and express uncertainty rather than false precision
- **Briefing template:** consistent structure — portfolio update, top opportunities, risk flags, notable events — with a reasoning log for auditability

### Subagent it can call: `deep-dive-researcher`
When a signal is ambiguous, the synthesis agent can dispatch a one-off research subagent with web access to investigate a specific question (e.g. "is the Helldivers 2 sentiment drop about the PSN controversy or the balance patch?") without polluting its own context. Returns a short findings summary.

---

## Agent 8: Portfolio Manager Agent + Execution

**Role:** Translate the briefing into a trade plan, then execute approved trades.

### Tools
- `alpaca_mcp` — **the Alpaca MCP server**, called as a native tool: fetch positions, check balances, place orders in natural language. This is the cleanest integration available and replaces any custom REST wrapper.
- `db_read` / `db_write` — trade plans, orders, snapshots
- `yfinance` — S&P 500 benchmark for return comparison

### Skill: `position-sizing-and-risk`
Encodes disciplined portfolio construction so Claude isn't sizing positions arbitrarily:
- **Sizing rules:** max position size as % of portfolio, sizing by conviction tier and by the materiality weight from the financial overlay
- **Risk posture:** sector-concentration limits (don't end up 80% in one publisher), cash-buffer rules
- **Entry/exit discipline:** when to scale in vs. take a full position; stop-loss and thesis-invalidation rules (close when the signal that justified entry reverses)
- **Benchmark-relative framing:** always report performance against the S&P 500, not in isolation

### Subagent: `execution-agent`
A deliberately thin, tightly-scoped subagent whose *only* tools are the Alpaca order endpoints and `db_write`. It reads approved trades and places them — nothing else. Scoping it narrowly is a safety measure: it structurally *cannot* do anything but execute pre-approved orders. This is subagent isolation used as a guardrail.

---

## Cross-Cutting Infrastructure

### Hooks (lifecycle interception)
The Claude Agent SDK supports **hooks** that fire at lifecycle points (before a tool call, after a response, on error). Use them for:
- **Pre-trade guard:** a `before-tool-call` hook on any Alpaca order tool that hard-blocks execution unless the trade's `status` is `approved` in Supabase — a belt-and-suspenders check on top of the approval UI
- **Cost control:** logging token spend per subagent run
- **Error recovery:** catching a failed API call and retrying or degrading gracefully rather than crashing the run

### Observability (LangSmith / OpenTelemetry)
Every `query()` can expand into dozens of turns and tool calls across subagents. Standard OpenTelemetry-based tracing captures the full trace tree — every LLM turn, every tool call with arguments and results, token counts per step, grouped by session. Essential for debugging "why did the sentiment subagent come back empty" or "why did the synthesis agent burn 40K tokens."

### Model tiering (cost discipline)
Match model to task rather than defaulting everything to the most capable model:
- **Opus-class** for the Synthesis Agent and Portfolio Manager (complex multi-signal reasoning)
- **Sonnet-class** for the data subagents (structured extraction and framework application)
- **Haiku-class** for trivial steps (simple classification, formatting)

Lock the model per subagent in its config so cost stays predictable.

### Skill governance
As the skill library grows, prevent conflicts with:
- **Non-overlapping trigger descriptions** in each `SKILL.md` frontmatter (the primary conflict-avoidance mechanism)
- **Subagent isolation** as a structural conflict resolver (skills loaded in one worker don't bleed into another)
- **Version control + semantic versioning** for each skill directory, treated like the rest of the codebase

---

## Summary Table

| Agent | Type | Key Skill | Key Tools | Subagents |
|---|---|---|---|---|
| Market & Player | Subagent | live-service-health-analysis | Steam, IGDB, RAWG | — |
| Sentiment | Subagent | sentiment-analysis-methodology (VADER+LLM+ABSA) | Reddit, X, YouTube, Steam reviews | — |
| Patch Notes | Subagent | patch-cadence-analysis | Steam RSS, web fetch, Discord | — |
| Studio Intelligence | Subagent | org-health-signal-analysis | Job boards, SEC EDGAR | — |
| Financial Overlay | Subagent | equity-signal-mapping | yfinance, SEC EDGAR | — |
| Discovery | Subagent | watchlist-relevance-scoring | Steam, IGDB, EDGAR, Reddit/X | — |
| Synthesis | Orchestrator | investment-synthesis-framework | DB read/write | deep-dive-researcher |
| Portfolio Manager | Orchestrator | position-sizing-and-risk | Alpaca MCP, yfinance, DB | execution-agent |

---

## What This Buys You (resume + engineering)

This internal structure is what separates "I called some APIs and an LLM" from "I designed a production agentic system." Specifically it demonstrates:
- **Correct primitive selection** (tools vs. skills vs. subagents) — the single clearest signal of agentic-systems maturity
- **Context engineering** via isolation and progressive disclosure
- **Encoded domain methodology** (real KPI frameworks, the VADER+LLM+ABSA hybrid, materiality weighting) rather than generic prompting
- **Safety-by-architecture** (the execution subagent's tool restriction + the pre-trade hook)
- **Cost discipline** (model tiering, locked configs, tracing)

These are exactly the competencies an AI Solutions Architect is hired to bring.
