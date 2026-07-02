---
trigger: >
  Use this skill when resolving studios or games to public tickers, weighting
  game-level signals by parent-company materiality, interpreting equity_signals
  rows, flagging pre-earnings windows, or tracking correlation between game
  health and ticker performance.
---

# Equity Signal Mapping

## Purpose

Map game-level and studio-level signals to investable public equities without
overstating their financial importance. This skill defines ticker resolution,
materiality weighting, pre-earnings logic, and correlation tracking between game
health and market performance.

## Inputs

- `studios`, `games`, and `watchlist` rows with parent name and ticker.
- Canonical studio-to-parent mapping maintained by the project.
- Worker outputs: player metrics, sentiment snapshots, patch events, studio
  signals, and weekly equity snapshots.
- Financial data from Alpaca Market Data, yfinance behind its Tier-2 adapter, and
  SEC EDGAR filings.
- Benchmark data for SPY or another S&P 500 proxy from Alpaca where available.

## Methodology

### 1. Studio-to-Ticker Resolution

Resolve tickers conservatively:

- Prefer direct public parent disclosed in filings, investor materials, or
  official publisher/studio pages.
- Use acquisition close date when ownership changes; do not remap before close.
- For publishing-only relationships, map only when the economics are material
  and disclosed or strongly evidenced.
- If multiple public companies touch a title, assign the primary economic parent
  and record secondary exposure separately for synthesis.
- If no public parent is known, mark the candidate private or unmappable.

Never infer a ticker from name similarity.

### 2. Parent-Company Materiality Weighting

Weight each game signal by how much it can plausibly move the parent.

Suggested tiers:

| Tier | Weight | Criteria |
| --- | ---: | --- |
| High | 1.00 | Flagship franchise, disclosed key title, small parent, or major live-service revenue driver |
| Medium | 0.50 | Meaningful franchise or active title inside a diversified publisher |
| Low | 0.20 | Relevant but unlikely to move parent results alone |
| Immaterial | 0.05 | Long-tail catalog title inside a mega-cap or no evidence of strategic importance |

When revenue data is unavailable, use proxies:

- Parent size and segment disclosure.
- Franchise prominence in investor materials.
- Current CCU, review base, review velocity, and platform breadth.
- Number of other tracked titles under the same parent.
- Earnings-call or filing mentions.

### 3. Pre-Earnings Window Logic

Flag signals inside the 3-4 weeks before a parent earnings date.

Use a default 28-calendar-day window:

- `pre_earnings_watch`: signal is material and within 28 days.
- `pre_earnings_high_signal`: material signal plus text-vs-quant divergence,
  severe player decline, major patch failure, or high-severity org event.
- `post_earnings_reset`: after earnings, compare management commentary and stock
  reaction against the prior game-health thesis.

Pre-earnings signals are not automatic trades; they raise synthesis priority.

### 4. Correlation Tracking

Track whether the thesis works over time:

- Compute rolling correlation between materiality-weighted game-health scores and
  ticker returns.
- Prefer relative returns versus SPY for signal validation.
- Use at least 8-12 weekly observations before treating correlation as useful.
- Treat correlation breakdown as a thesis-health warning, not proof the game
  signal is useless.

Interpretation:

- Positive correlation: market is responding to game health.
- Low correlation with strong signals: possible alpha, immateriality, or timing
  mismatch.
- Negative correlation: parent-level factors may dominate game-level signals.

### 5. Equity Snapshot Interpretation

Use equity data as context, not as a standalone recommendation:

- Price and SPY-relative movement show market reaction.
- P/E, short interest, analyst ratings, and earnings date frame risk.
- SEC filings and management commentary can confirm or refute game-level thesis.
- Large diversified parents require stronger materiality evidence than pure-play
  game publishers.

## Output Contract

For each ticker, persist or return:

```json
{
  "ticker": "TTWO",
  "studio_id": "uuid",
  "date": "YYYY-MM-DD",
  "current_price": 165.25,
  "pe_ratio": 28.4,
  "earnings_date": "YYYY-MM-DD",
  "short_interest": 4.2,
  "health_score": 6.7,
  "current_signal": "Weighted health across 4 tracked titles; 1 high-materiality risk.",
  "recommendation": "watch | bullish | bearish | neutral"
}
```

Mapping decisions should also produce an auditable note:

```json
{
  "game_id": "uuid",
  "studio_id": "uuid",
  "ticker": "TTWO",
  "parent_name": "Take-Two Interactive",
  "materiality_weight": 0.5,
  "mapping_confidence": "high",
  "evidence": ["official parent relationship", "tracked franchise"]
}
```

## Hard Constraints / Source-Risk Notes

- Alpaca Market Data is preferred for price and SPY benchmark bars.
- yfinance is Tier 2; keep it behind cache and stale fallback.
- EDGAR is Tier 1 but must be paced and use a declared user agent.
- Do not create active watchlist exposure for unmappable private studios.
- Do not treat game-level signals as equity recommendations without materiality
  weighting.
