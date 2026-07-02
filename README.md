# Games Industry Investment Intelligence Platform — Design Docs

A multi-agent system that reads game-level signals (players, sentiment, patch cadence, studio hiring), maps them to public equities, and closes the loop through a human-approved paper-trading portfolio. These five documents are the complete design set. **Last revised: July 2026** (source facts — X API pricing, YouTube quotas, Supabase free-tier behavior, Alpaca MCP — verified as of this revision).

## Reading order

| # | Document (located in docs) | What it covers | Read it when |
|---|---|---|---|
| 1 | **games-investment-platform-brief.md** | System-level overview: components, tech stack, data model, orchestration flow, build phases (with an explicit cut line), resume framing | First — everything else hangs off this |
| 2 | **agent-components-plan.md** | Per-agent internals: tools, skills (with the analytical frameworks each encodes), subagents, cross-cutting infrastructure, and the SDK↔CrewAI mapping table | Before building any agent |
| 3 | **data-source-risk-register.md** | Every external source tiered by access path, ToS posture, and block risk — with mandated mitigations, cost math for deferred sources, and named substitutes for excluded ones | Before integrating (or proposing) any data source |
| 4 | **reddit_source_adapter.md** | The reference Tier-2 source implementation: swappable `RedditSource` interface, rate limiting, retry/backoff, fallback chain, CrewAI tool integration, operational budget | When building the sentiment layer, or any new Tier-2 adapter |
| 5 | **supabase_reddit_cache.md** | The source-agnostic `api_cache` implementation behind every adapter: fresh/stale TTL semantics, fail-open behavior, serialization boundary, test fake, free-tier operations | Alongside #4 |

## Conventions that span the set

- **Framework vocabulary:** specs are written in Claude Agent SDK terms (skills, subagents, hooks) because that's the target; the MVP ships on CrewAI. The mapping table in doc #2 defines every equivalence, and nothing in the design depends on a primitive that lacks one. Safety-critical checks (the pre-trade guard) live *in tool code*, so they hold under either framework.
- **Divergence check ownership:** the text-vs-quant divergence comparison — the system's alpha — is computed by the **Synthesis Agent**, because workers run parallel and isolated and only synthesis sees all same-week outputs. The Sentiment Subagent produces the inputs (and at most a clearly-labeled lagged preliminary flag).
- **Source discipline:** any new external source enters through the risk register (doc #3) first. Tier 2 sources always get the adapter + cache + graceful-degradation treatment that docs #4–5 implement.
- **Honest scoping:** paid or ToS-risky integrations (X, LinkedIn, Discord) are deferred or excluded *explicitly*, with cost math, revisit criteria, and named substitutes — never silently assumed free.

## Setup

> Detailed setup instructions will be added as each phase completes.

```bash
# Clone the repo
git clone <repo-url>
cd games-industry-investment-platform

# Install Python dependencies
pip install -r requirements.txt

# Copy and fill in environment variables
cp .env.example .env

# Run the watchlist seeding agent (Phase 1 — one-time)
python agents/orchestrator/seed_watchlist.py
```
