# Agent Components Deep-Dive
### Subagents, Skills, Tools, and Analytical Frameworks

This document specs out the *internals* of each agent in the Games Industry Investment Intelligence Platform. It complements the main project brief by answering: what is a subagent vs. a skill vs. a tool, which ones each agent needs, and what proven analytical methodologies the skills should encode. Source-access decisions (which APIs, which scraping is off-limits, and why) live in the companion **Data Source Risk Register** — this doc references its conclusions rather than re-arguing them.

---

## First: The Three Primitives (and when to use each)

The Claude Agent SDK exposes three building blocks. Choosing the wrong one is the most common architecture mistake, so the rule of thumb matters:

> **If you're writing instructions, it's a skill. If you're writing a function the model calls, it's a tool. If the work needs its own context window, its own system prompt, or its own tool restrictions, it's a subagent.**

| Primitive | What it is | When to use it | How it's defined |
|---|---|---|---|
| **Tool** | An atomic, callable function with a fixed interface and no internal decision-making — like a system call | Fetching data, placing an order, running a calculation | A function (often via MCP server) |
| **Skill** | A folder of instructions + optional scripts that Claude loads *only when relevant* | Encoding a methodology, a domain framework, or a repeatable workflow | A `SKILL.md` file in `.claude/skills/` |
| **Subagent** | A named, isolated Claude instance with its own context window, system prompt, and tool access | Isolating a noisy or specialized task so the parent context stays clean; running work in parallel | YAML config (or filesystem) |

### Framework note: SDK vocabulary, CrewAI MVP

This document is written in **Claude Agent SDK** terms because that is the target architecture. The MVP, however, ships on **CrewAI** (per the brief's build phases). Every SDK primitive has a plainer CrewAI-era equivalent, and the design deliberately never depends on anything that lacks one:

| SDK primitive (target) | CrewAI equivalent (MVP) |
|---|---|
| **Skill** — `SKILL.md` + scripts, loaded via progressive disclosure | A versioned methodology document (same content, same repo, same semver) injected into the agent's prompt/task description — loaded in full, since CrewAI has no progressive disclosure. Bundled scripts are registered as ordinary tools. |
| **Subagent** — isolated context, own system prompt, *restricted tool list* | A CrewAI `Agent` with a deliberately scoped `tools=[...]` list, run as an isolated task. Tool restriction — the part that matters for safety — works identically. |
| **Hook** — `before-tool-call` / lifecycle interception | An explicit in-code check. Critically, the **pre-trade guard lives inside the order-placement tool itself** (see Agent 8), so it holds under any framework and can't be bypassed by orchestration changes. |
| **MCP server** — native tool transport | A thin direct API wrapper (e.g. `alpaca-py`) registered as a custom tool. The official Alpaca MCP server slots in after migration with no behavioral change. |
| *No nested subagents* (documented SDK constraint) | Enforced by convention: the crew stays strictly two levels deep — orchestrator → workers. |

The migration is therefore a mechanical re-homing of the same artifacts, not a redesign — which is exactly the point of writing the spec in the target vocabulary now.

### Why this separation matters for *this* project

- **Context isolation:** A sentiment analysis run might read 200 Reddit posts. You don't want 200 posts polluting the main synthesis agent's context. Delegate to a sentiment subagent — only its final structured output (a score + themes) returns to the parent.
- **Progressive disclosure (skills):** Claude only loads a skill's full body when the task matches its description. You can attach a comprehensive 5-page "live-service health analysis" methodology as a skill, and it costs almost nothing in context until it's actually triggered. (At MVP the same document is simply injected in full — the cost discipline arrives with the migration; the authoring discipline starts now.)
- **Determinism (tools/scripts):** Sorting, computing a churn rate, or normalizing a sentiment score should be code, not token generation. Skills can bundle Python scripts for exactly this.
- **Parallelism:** The five data agents have no need to talk to each other mid-run, which is precisely the condition where subagents (not agent teams) are the right call. Agent teams add billable round-trips and are only worth it when workers must share discoveries mid-task.

---

## Architecture Overview

The system uses an **orchestrator–worker pattern**: a lead orchestrator coordinates and delegates to specialized subagents that run in parallel, each with an isolated context. Only each subagent's return value crosses back to the orchestrator.

```
[Watchlist Seeding Agent]  ── one-time ──► populates watchlist
   │
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
[Synthesis Agent]  ── reads all returns, computes divergence ──► weekly briefing
   │
   ▼
[Portfolio Manager Agent] ──► trade plan ──► [Execution Agent (Alpaca)]
```

A note on a documented SDK constraint: **subagents cannot spawn their own subagents.** So the design is strictly two levels deep — orchestrator → workers. Anything that looks like it needs a third level should instead be a skill loaded by the worker. (Under CrewAI the same two-level rule is held by convention.)

A note on **parallelism and the divergence check:** because the workers run in parallel and isolated, no worker ever sees another worker's *same-week* output. Any analysis that compares two workers' outputs — most importantly the text-vs-quant divergence check — therefore belongs to the **Synthesis Agent**, the first actor that holds all of the week's returns at once. This single sentence resolves what would otherwise be the architecture's most tempting inconsistency: putting the divergence computation inside the Sentiment Subagent, which structurally cannot have the data.

---

## Agent 0: Watchlist Seeding Agent (one-time)

**Role:** Build the initial tracked universe — roughly 150–300 games and 30–50 studios — so no manual entry is ever needed. Runs once at project start; Discovery (Agent 6) handles growth from then on.

### Tools
- `igdb_api` — publicly-traded-parent portfolios, release history, hype/follows
- `rawg_api` — supplementary catalog coverage for dedup
- `steam_api` — top titles by concurrent players, live-service candidates
- `db_write` — populate `Watchlist` and `Games`

### Skill: `watchlist-relevance-scoring` (shared with Discovery)
The seeding agent applies the **same relevance rubric** the Discovery Agent uses weekly — public parent, material player base, live-service model, mappable ticker — plus two seeding-only responsibilities:
- **Studio→ticker mapping:** applies the canonical mapping table maintained in `equity-signal-mapping` (Agent 5's skill), flagging unmappable studios as private/excluded rather than guessing
- **Sentiment tiering:** assigns each game a sentiment tier (Tier A = full Reddit post+comment collection; tail = listing-only) so the weekly Reddit request budget is set at seed time, not discovered in production (see the adapter doc's operational notes)

Sharing the skill rather than writing a second rubric is deliberate: seed-time and discovery-time relevance must agree, or the watchlist drifts.

---

## Agent 1: Market & Player Subagent

**Role:** Collect and interpret quantitative engagement metrics.

### Tools
- `steam_api` — concurrent players via the official `ISteamUserStats/GetNumberOfCurrentPlayers` (current value only — the weekly snapshots build the historical series), review counts/scores via the public `appreviews` JSON endpoint
- `igdb_api` — release dates, genre, platform metadata, hype/follows
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
- `reddit_source` — fetch top/hot posts and comments from game subreddits via the public read-only `.json` endpoints (the official Data API is not used); rate-limited, cached, and degrades gracefully when blocked (see the *RedditSource Adapter* and *SupabaseRedditCache* designs). Comment fetches respect the per-game sentiment tier set at seeding.
- `youtube_data_api` — comment extraction on patch/review/update videos via the **official Data API** (`commentThreads.list` = 1 quota unit per 100 comments against a free 10,000-unit daily quota; `search.list` is avoided — it costs 100 units and sits in its own tightly capped bucket, so video discovery goes through tracked channels' upload playlists at 1 unit per 50 videos instead). Handles `commentsDisabled` as a normal miss, not an error.
- `steam_reviews_api` — review text + helpfulness weighting via the public `appreviews` endpoint
- `vader_score` — bundled deterministic lexicon scorer (baseline)
- `db_read` — **last week's** `PlayerMetrics` only, for the optional lagged preliminary flag (step 4 below)
- *(no `x_api` at MVP — X access is pay-per-use as of Feb 2026 and deferred; see the risk register for the cost math and revisit criteria)*

### Skill: `sentiment-analysis-methodology`
The research is clear that the best-in-class approach is a **hybrid VADER + LLM pipeline with aspect-based analysis**, not either alone. The skill encodes this methodology:

1. **Baseline pass (VADER, deterministic):** VADER is the gold-standard rule-based scorer purpose-built for social media — it handles slang, emoji, capitalization, and degree modifiers. Run it first to get a fast, reproducible polarity baseline and to flag the magnitude of sentiment per post.

2. **Aspect-Based Sentiment Analysis (ABSA, Claude-powered):** Rather than scoring a whole post as "negative," extract *aspect–sentiment* pairs — e.g. "monetization → negative," "core gameplay → positive," "server stability → negative." This is far more actionable because it tells you *what* players are reacting to. LLMs have largely replaced dedicated ABSA models here.

3. **Thematic clustering:** Group aspects into recurring themes across the corpus (e.g. "battle pass pricing backlash," "matchmaking complaints") and surface the top 3 by volume and intensity.

4. **Divergence *inputs*, not the divergence *check*:** the check itself — text sentiment vs. **same-week** quantitative signals, the system's alpha (the documented "beyond stars" technique of measuring where review *text* diverges from ratings or player counts) — is computed by the **Synthesis Agent**, because this subagent runs in parallel with the quant workers and never sees their same-week output. This skill's job is to make the check possible: emit clean, comparable, per-aspect scores. Optionally, it may attach a *preliminary* flag computed against **last week's** player metrics read from Supabase — always labeled as lagged, never presented as the real check.

5. **Vocal-minority guard:** Weight by engagement and account credibility; flag when negativity is concentrated in a small number of high-activity accounts vs. broad-based. (Research on social platforms shows super-active and bot-like users can skew sentiment disproportionately.)

6. **Consistency checks:** Lightweight rule-based validation so scores stay stable across different game vocabularies.

**Output contract:** per-game sentiment score (1–10), top 3 aspect-themes with polarity, week-over-week trend, vocal-minority confidence note, and (optional, labeled) lagged preliminary flag — persisted for the Synthesis Agent's divergence check.

**Why this lives in a subagent:** reading hundreds of posts is exactly the high-volume, noisy work that should be isolated. Only the structured output returns to the orchestrator.

---

## Agent 3: Patch Notes & Update Cadence Subagent

**Role:** Infer developer investment and live-service commitment from update behavior.

### Tools
- `steam_news_api` — official `ISteamNews/GetNewsForApp` per title (patch notes and announcements; this replaces both RSS scraping and any Discord access)
- `web_fetch` — developer blogs, official patch pages
- `db_write` — persist to `PatchEvents`
- *(no `discord_scraper` — reading Discord announcement channels programmatically requires a bot installed by each server's admins or automating a user account, which Discord's ToS prohibits; the Steam news feed mirrors the same announcements for nearly every tracked title. See the risk register.)*

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
- `greenhouse_boards_api` / `lever_postings_api` / `ashby_postings_api` — the hosted-ATS **public job-board APIs** (official, documented, no auth for reads); these cover the large majority of game-studio postings
- `careers_page_fetch` (Playwright) — low-volume fallback for the minority of studios not on a hosted ATS; robots-respecting, never LinkedIn (ToS-prohibited with aggressive anti-bot enforcement — see the risk register)
- `web_fetch` — press releases, official blogs, Steam store page diffs, investor-relations pages
- `sec_edgar_api` — 10-K/10-Q/8-K filings for public parents
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
- `yfinance` — P/E, earnings dates, analyst ratings, short interest (unofficial Yahoo wrapper; wrapped in the same adapter+cache pattern as Reddit per the risk register)
- `alpaca_data_api` — official free market data (~200 req/min): prices and SPY benchmark bars; the drop-in fallback when Yahoo throttles
- `sec_edgar_api` — fundamentals from filings
- `db_write` — persist weekly per-ticker rows to `EquitySignals` (renamed from "PortfolioPositions" to stop colliding with the broker-state `Positions` table)

### Skill: `equity-signal-mapping`
- **Studio→ticker resolution:** the canonical mapping table and the logic for parents with multiple studios (a single game signal must be weighted by that title's materiality to total parent revenue — a hit indie game barely moves a mega-cap). The Seeding Agent (Agent 0) applies this same table at seed time.
- **Materiality weighting:** framework for how much a game-level signal *should* move the thesis given the parent's size and portfolio
- **Pre-earnings windows:** flag when a tracked title shows divergence inside the 3–4 weeks before a parent's earnings date — the highest-signal moment
- **Correlation tracking:** rolling correlation between game-health scores and ticker performance to validate (or invalidate) the thesis over time

---

## Agent 6: Discovery Subagent

**Role:** Propose new watchlist entries. Runs in parallel with the main pipeline.

### Tools
- `steam_api` — trending / top-by-CCU charts
- `igdb_api` — upcoming release calendar with hype/follow signals
- `sec_edgar_api` — new filings (IPOs, acquisitions)
- `reddit_source` — mention-volume spikes for untracked titles, via the shared `RedditSource` adapter (X spike detection is deferred along with X access generally)
- `db_write` — write to `WatchlistProposals`

### Skill: `watchlist-relevance-scoring`
- **Investment-relevance criteria:** the rubric for what makes a game worth tracking (public parent, material player base, live-service model, etc.) — shared with the Seeding Agent so seed-time and discovery-time judgments agree
- **Trigger thresholds:** what counts as a meaningful trending spike vs. noise
- **Rationale generation:** a structured template so every proposal arrives with a consistent, reviewable justification
- **False-positive learning:** reads the log of past rejections to tighten criteria over time

---

## Agent 7: Synthesis Agent (Orchestrator-level)

**Role:** Reason across all worker outputs and produce the weekly briefing. This is *not* a subagent — it's the lead reasoning step that consumes the workers' structured returns. It is also, by architecture, **the home of the divergence check**: the workers run parallel and isolated, so this is the first place same-week text and same-week quant coexist.

### Tools
- `db_read` — pull the week's structured outputs from all tables
- `db_write` — persist the briefing

### Skill: `investment-synthesis-framework`
- **The divergence check (owned here):** compute the gap between the Sentiment Subagent's same-week aspect scores and the same-week quantitative signals (player counts, review velocity, cadence). Stable players + collapsing text sentiment = early churn warning; the inverse = possible recovery before the numbers show it. Any lagged preliminary flag from the sentiment worker is treated as a hint, and superseded here.
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
- `alpaca_trading` — at MVP, a thin custom tool wrapping `alpaca-py` (fetch positions, check balances, place orders). Post-migration this becomes **the official Alpaca MCP server** (open-source, Alpaca-maintained, paper-trading mode on by default) called as a native tool — same endpoints, cleaner transport, zero behavioral change.
- `db_read` / `db_write` — trade plans, orders, snapshots
- `alpaca_data_api` / `yfinance` — S&P 500 benchmark (SPY bars) for return comparison

### Skill: `position-sizing-and-risk`
Encodes disciplined portfolio construction so Claude isn't sizing positions arbitrarily:
- **Sizing rules:** max position size as % of portfolio, sizing by conviction tier and by the materiality weight from the financial overlay
- **Risk posture:** sector-concentration limits (don't end up 80% in one publisher), cash-buffer rules
- **Entry/exit discipline:** when to scale in vs. take a full position; stop-loss and thesis-invalidation rules (close when the signal that justified entry reverses)
- **Benchmark-relative framing:** always report performance against the S&P 500, not in isolation

### Subagent: `execution-agent`
A deliberately thin, tightly-scoped subagent whose *only* tools are the Alpaca order endpoints and `db_write`. It reads approved trades and places them — nothing else. Scoping it narrowly is a safety measure: it structurally *cannot* do anything but execute pre-approved orders. This is subagent isolation used as a guardrail — and it works identically as a restricted-tool CrewAI agent at MVP.

**The pre-trade guard is code inside the order tool, not framework machinery.** The order-placement function itself re-reads the trade's `status` from Supabase and raises unless it is `approved`. This means the guard survives any orchestration change, any framework migration, and any prompt-level confusion — the model literally has no callable path to an unapproved order. The Agent SDK's `before-tool-call` hook later wraps the same check as a second, declarative layer; it does not replace the in-tool check.

---

## Cross-Cutting Infrastructure

### Hooks (lifecycle interception)
The Claude Agent SDK supports **hooks** that fire at lifecycle points (before a tool call, after a response, on error). At MVP these are explicit in-code checks; the SDK migration formalizes them without changing what they verify. Use them for:
- **Pre-trade guard:** the in-tool `status == approved` check described in Agent 8, later mirrored as a `before-tool-call` hook on any Alpaca order tool — belt-and-suspenders on top of the approval UI
- **Cost control:** logging token spend per subagent run
- **Error recovery:** catching a failed API call and retrying or degrading gracefully rather than crashing the run — e.g. the `RedditSource` adapter serving last-known-good data from its Supabase cache when Reddit throttles or blocks the run

### Observability (LangSmith / OpenTelemetry)
Every `query()` can expand into dozens of turns and tool calls across subagents. Standard OpenTelemetry-based tracing captures the full trace tree — every LLM turn, every tool call with arguments and results, token counts per step, grouped by session. Essential for debugging "why did the sentiment subagent come back empty" or "why did the synthesis agent burn 40K tokens."

### Model tiering (cost discipline)
Match model to task rather than defaulting everything to the most capable model:
- **Opus-class** for the Synthesis Agent and Portfolio Manager (complex multi-signal reasoning)
- **Sonnet-class** for the data subagents (structured extraction and framework application)
- **Haiku-class** for trivial steps (simple classification, formatting)

Lock the model per subagent in its config so cost stays predictable. There is no ongoing Anthropic free tier — the brief budgets roughly $10–25/month at weekly cadence with this tiering, and the per-subagent token logging above is what keeps that number honest.

### Skill governance
As the skill library grows, prevent conflicts with:
- **Non-overlapping trigger descriptions** in each `SKILL.md` frontmatter (the primary conflict-avoidance mechanism)
- **Subagent isolation** as a structural conflict resolver (skills loaded in one worker don't bleed into another)
- **Version control + semantic versioning** for each skill directory, treated like the rest of the codebase (this applies from day one, while the "skills" are still CrewAI prompt documents — same files, same discipline)

### External data-source adapters (resilience)
Volatile or unofficial upstreams are isolated behind a single swappable adapter interface so a provider policy change is contained to one module. The reference case is `RedditSource`: with self-service Data API access closed off (Nov 2025 Responsible Builder Policy), the system reads public read-only `.json` endpoints through an adapter that paces requests under the ~10 req/min per-IP unauthenticated ceiling, caches every payload (`SupabaseRedditCache`, backed by a generic `api_cache` table), and degrades to last-known-good on a block rather than failing the run — relevant because the pipeline runs from data-center (GitHub Actions) IPs, which Reddit throttles first. Because every layer (raw fetch, cache wrapper, fallback chain) implements the same interface, an alternate egress (proxy or managed scraper) drops in with no change to the Sentiment or Discovery subagents that depend on it. **The Data Source Risk Register makes this pattern mandatory for every Tier-2 source** — yfinance and the Steam `appreviews` endpoint are next in line. See the dedicated *RedditSource Adapter* and *SupabaseRedditCache* design docs.

---

## Summary Table

| Agent | Type | Key Skill | Key Tools | Subagents |
|---|---|---|---|---|
| Watchlist Seeding | One-time agent | watchlist-relevance-scoring (shared) | IGDB, RAWG, Steam | — |
| Market & Player | Subagent | live-service-health-analysis | Steam (official APIs), IGDB, RAWG | — |
| Sentiment | Subagent | sentiment-analysis-methodology (VADER+LLM+ABSA) | Reddit adapter, YouTube Data API, Steam reviews | — |
| Patch Notes | Subagent | patch-cadence-analysis | Steam news API, web fetch | — |
| Studio Intelligence | Subagent | org-health-signal-analysis | Greenhouse/Lever/Ashby APIs, careers pages, SEC EDGAR | — |
| Financial Overlay | Subagent | equity-signal-mapping | yfinance, Alpaca data, SEC EDGAR | — |
| Discovery | Subagent | watchlist-relevance-scoring (shared) | Steam, IGDB, EDGAR, Reddit adapter | — |
| Synthesis | Orchestrator | investment-synthesis-framework (owns divergence) | DB read/write | deep-dive-researcher |
| Portfolio Manager | Orchestrator | position-sizing-and-risk | Alpaca (alpaca-py → MCP), DB | execution-agent |

---

## What This Buys You (resume + engineering)

This internal structure is what separates "I called some APIs and an LLM" from "I designed a production agentic system." Specifically it demonstrates:
- **Correct primitive selection** (tools vs. skills vs. subagents) — the single clearest signal of agentic-systems maturity
- **Context engineering** via isolation and progressive disclosure
- **Encoded domain methodology** (real KPI frameworks, the VADER+LLM+ABSA hybrid, materiality weighting) rather than generic prompting
- **Architecturally honest data flow** — the divergence check lives where the data actually exists (synthesis), not where it sounds nicest (the sentiment worker)
- **Safety-by-architecture** (the execution subagent's tool restriction + the in-tool pre-trade guard that survives framework migration)
- **Resilient integrations** (swappable source adapters with caching and graceful degradation, governed by a risk register, so a provider policy change or IP block doesn't break the pipeline)
- **Cost discipline** (model tiering, locked configs, tracing, and a stated monthly budget)
- **Scope discipline** (an explicit cut line, risk-tiered sources, and deferred-not-fudged paid integrations like X)

These are exactly the competencies an AI Solutions Architect is hired to bring.
