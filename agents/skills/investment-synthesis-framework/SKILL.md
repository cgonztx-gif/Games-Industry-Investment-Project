---
trigger: >
  Use this skill when producing the weekly briefing, combining worker outputs,
  computing authoritative same-week text-vs-quant divergence, scoring
  confidence, ranking opportunities or risk flags, or deciding whether to
  dispatch a deep-dive researcher.
---

# Investment Synthesis Framework

## Purpose

Combine structured worker outputs into an auditable weekly investment briefing.
Synthesis is the first layer that sees all same-week data together, so it owns
the authoritative divergence check and the final confidence framing.

## Inputs

- Same-week `player_metrics`, `sentiment_snapshots`, `patch_events`,
  `studio_signals`, and `equity_signals`.
- Historical rows for trends, rolling averages, prior briefings, and rejection or
  thesis outcomes.
- Optional preliminary lagged divergence notes from sentiment, treated only as
  hints.
- Parent materiality weights from `equity-signal-mapping`.
- Current portfolio context when available, but trade sizing belongs to
  `position-sizing-and-risk`.

## Methodology

### 1. Normalize Worker Outputs

Group signals by `game_id`, `studio_id`, and `ticker`.

For each game, build a compact weekly state:

- Product health: CCU, review velocity, review score, trend status.
- Sentiment: average score by source, top themes, vocal-minority note.
  **`source='news'` is media tone/stance, not community sentiment** (see
  `agents/workers/sentiment/news_stance_client.py`) — it lives in its own
  `news_score`/`news_themes`, never blended into `avg_score` with
  reddit/steam/youtube. `_sentiment_by_game()` in `agents/synthesis/agent.py`
  enforces this split; do not reintroduce a source-agnostic average.
- Patch cadence: patch types, cadence status, roadmap status, monetization flag.
- Org health: studio-level signals and severity.
- Equity context: ticker, materiality weight, earnings window, price context.

Do not pass raw Reddit posts, full review text, or full filings into the briefing
unless a deep dive explicitly needs them.

### 2. Convergence Rules

Raise conviction when independent layers agree.

High-conviction bearish setup:

- Declining player metrics or review velocity.
- Bearish sentiment with material negative themes.
- Slowing or absent patch cadence, missed roadmap, or monetization-only updates.
- Layoffs, executive churn, consolidation, or other distress signal.
- High parent materiality or pre-earnings timing.

High-conviction bullish setup:

- Stable or growing player metrics.
- Improving or positive sentiment across more than one source.
- Content cadence on pace or successful roadmap delivery.
- Hiring aligned with growth or launch readiness.
- Material title with earnings catalyst or market underreaction.

Single-layer signals usually become `watch` items, not strong recommendations.

### 3. Authoritative Same-Week Divergence

Compute divergence only here, using same-week outputs.

Primary cases:

- `bearish_text_stable_quant`: sentiment <= 3.5 while CCU/review velocity remain
  stable. Interpret as possible early churn warning or vocal-minority event.
- `bullish_text_weak_quant`: sentiment >= 6.5 while CCU/review velocity weaken.
  Interpret as possible recovery narrative not yet visible in behavior.
- `quant_decline_sentiment_neutral`: product metrics deteriorate while text stays
  neutral. Interpret as silent churn or coverage gap.
- `patch_cadence_bad_quant_stable`: patch cadence deteriorates before metrics do.
  Interpret as future retention risk.
- `news_community_divergence`: community sentiment (`avg_score`) and media
  tone (`news_score`) disagree by >= 2.5 points. This is source disagreement,
  not a text-vs-quant check — treat it as a prompt to ask which is leading
  (e.g. press covering a controversy the community hasn't reacted to yet, or
  vice versa), not as a bullish/bearish verdict on its own.

Any sentiment-side `Preliminary lagged flag` is superseded by this same-week
check. Preserve it in the reasoning log only if it explains why the game was
examined.

### 4. Divergence Opportunity Logic

Negative sentiment can be bullish or bearish depending on breadth:

- Vocal-minority negativity plus stable players, healthy reviews, and no cadence
  failure can be a contrarian setup.
- Broad multi-source negativity plus stable quant is an early warning.
- Positive sentiment with weak quant can be a low-confidence recovery watch.
- Strong quant with missing sentiment coverage is not a sentiment divergence; it
  is a coverage gap.

### 5. Confidence Scoring

Score confidence as `very_low`, `low`, `medium`, or `high`.

Increase confidence for:

- Four or more data layers present.
- Agreement across independent sources.
- High sample sizes and strong source coverage.
- High materiality to parent ticker.
- Recency inside the current week.
- Repeated pattern across multiple weeks.

Decrease confidence for:

- Missing source coverage.
- Thin review or comment samples.
- Tier-2 stale fallback data.
- Vocal-minority concentration.
- Conflicting signals without a clear timing explanation.
- Low materiality to a diversified parent.

Never express false precision. Use numeric scores only when backed by explicit
component logic.

### 6. Deep-Dive Researcher Triggers

Dispatch a deep-dive researcher only for bounded questions that can change the
briefing:

- High-materiality ticker with conflicting signals.
- Pre-earnings divergence or severe risk flag.
- Sudden sentiment collapse with stable quant.
- Acquisition, IPO, executive departure, or layoff event needing confirmation.
- Patch controversy where the theme is unclear from structured outputs.
- Source coverage gap for a Tier A title.

The deep dive should return a short findings summary and citations or source
URLs. It should not pollute the main synthesis context with raw corpora.

## Weekly Briefing Template

Use this structure:

```text
Weekly Briefing - YYYY-MM-DD

1. Portfolio Update
   - Confidence:
   - Exposure context:
   - Benchmark-relative note:

2. Top Opportunities
   - Ticker / game:
   - Signal:
   - Evidence:
   - Materiality:
   - Confidence:

3. Risk Flags
   - Ticker / game / studio:
   - Severity:
   - Evidence:
   - Required follow-up:

4. Notable Events
   - Product:
   - Sentiment:
   - Patch cadence:
   - Org / filings:
   - Equity:

5. Reasoning Log
   - Data coverage:
   - Divergence checks:
   - Conflicts and uncertainty:
```

## Output Contract

Persist one row to `weekly_briefings`:

```json
{
  "week_of": "YYYY-MM-DD",
  "briefing_text": "Human-readable weekly briefing.",
  "portfolio_update": {
    "confidence": "medium",
    "equity_signals_count": 12,
    "divergence_count": 3,
    "risk_count": 2
  },
  "top_opportunities": [],
  "risk_flags": [],
  "notable_events": {},
  "reasoning_log": "Auditable explanation of data coverage and decisions."
}
```

## Hard Constraints / Source-Risk Notes

- Synthesis owns same-week divergence. Do not defer it to sentiment.
- Do not generate trade orders here; portfolio sizing and approval are separate.
- Treat stale Tier-2 fallback data as lower confidence and state that in the log.
- Do not introduce Tier 4 sources during deep dives.
- Never average `source='news'` sentiment_score into community `avg_score`
  (or vice versa). They measure different things — media narrative framing
  vs. player-experience sentiment — and blending them silently corrupts every
  downstream divergence/risk check that reads `avg_score`.
