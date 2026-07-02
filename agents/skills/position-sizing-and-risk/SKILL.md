---
trigger: >
  Use this skill when translating weekly briefing outputs into a trade plan,
  sizing paper-portfolio positions, applying concentration limits, enforcing
  cash buffers, defining stop-loss or thesis-invalidation rules, or framing
  returns against the S&P 500 benchmark.
---

# Position Sizing And Risk

## Purpose

Turn synthesis outputs into disciplined paper-trading plans without arbitrary
position sizes. This skill defines conviction tiers, maximum exposures,
concentration limits, cash buffers, exit discipline, and benchmark-relative
reporting. It does not execute trades.

## Inputs

- Current weekly briefing and structured opportunities/risk flags.
- `equity_signals` rows with ticker, health score, recommendation, earnings date,
  and materiality context.
- Current Alpaca paper portfolio positions, cash, buying power, and open orders.
- Historical trade plans, fills, and portfolio snapshots.
- SPY or S&P 500 benchmark return series.
- User-approved risk settings if provided; otherwise use the defaults below.

## Methodology

### 1. Conviction-Tier Sizing

Assign each proposed position a conviction tier before sizing.

| Tier | Default target | Criteria |
| --- | ---: | --- |
| No trade | 0% | Signal is weak, low confidence, immaterial, or already priced |
| Watch / paper note | 0% | Interesting but insufficient confirmation |
| Starter | 1-2% | Medium confidence, limited materiality, or first signal week |
| Core | 3-5% | Multi-layer convergence, material ticker, manageable risk |
| High conviction | 5-8% | Strong convergence, high materiality, catalyst timing, clear invalidation |

Scale down when:

- Parent materiality is low.
- Confidence is below medium.
- Earnings or event risk is high and unhedged.
- Position would breach concentration limits.
- Data coverage includes stale Tier-2 fallback.

### 2. Maximum Position and Concentration Limits

Default limits for the paper portfolio:

- Max single ticker: 8% target, 10% hard cap.
- Max single publisher/parent family: 15%.
- Max games industry exposure: 40%.
- Max one theme, such as live-service rebound or monetization backlash: 20%.
- Max new capital deployed in one weekly plan: 15%.

If a proposed trade breaches a limit, reduce size or mark it `watch`.

### 3. Cash Buffer

Maintain a default 15% cash buffer.

Allow a temporary 10% floor only when:

- There are multiple high-confidence independent signals.
- No near-term liquidity need exists.
- The plan explains why reducing cash is justified.

Do not deploy cash just because no current positions exist.

### 4. Entry Discipline

Use staged entries unless conviction and timing are unusually strong:

- Starter entry for first-week signals.
- Add only after confirmation, such as sustained metrics, management commentary,
  or post-earnings validation.
- Avoid adding after a one-week spike unless the post-spike baseline improves.
- Avoid opening new positions solely from a sentiment note without product or
  equity confirmation.

### 5. Stop-Loss and Thesis Invalidation

Use both price risk and thesis risk.

Default price risk rules:

- Review at -8% from entry.
- Reduce or close at -12% to -15% unless thesis has strengthened and risk budget
  allows holding.
- Use tighter risk near earnings when the thesis is explicitly pre-earnings.

Thesis invalidation rules override price anchoring:

- Signal that justified entry reverses for two consecutive weeks.
- Authoritative divergence resolves against the position.
- Player metrics deteriorate and sentiment/pacing confirm the decline.
- Parent earnings commentary refutes game materiality.
- Acquisition, delay, cancellation, or leadership event changes the mapping.
- Position breaches concentration limits after price movement.

For shorts or inverse exposure, mirror the logic and define the invalidating
positive signal explicitly. If shorting is unavailable in the paper setup, express
bearish views as avoid/reduce/close rather than forced short trades.

### 6. Benchmark-Relative Framing

Always report performance against the S&P 500, preferably SPY:

- Weekly portfolio return vs. SPY weekly return.
- Cumulative portfolio return vs. SPY since inception.
- Per-position contribution and whether it beat SPY over the holding period.

Absolute gains are not sufficient. A trade that rises 2% while SPY rises 5%
underperformed.

## Output Contract

Produce a trade plan for human approval:

```json
{
  "week_of": "YYYY-MM-DD",
  "portfolio_risk_posture": "balanced | defensive | opportunistic",
  "cash_buffer_target_pct": 15,
  "benchmark": "SPY",
  "orders": [
    {
      "ticker": "TTWO",
      "action": "buy | sell | hold",
      "target_weight_pct": 3.0,
      "size_usd": 3000.0,
      "conviction_tier": "core",
      "rationale": "Multi-layer convergence with material title exposure.",
      "thesis_invalidation": "Close if player decline persists for 2 weeks and sentiment remains bearish.",
      "risk_checks": {
        "single_ticker_limit_ok": true,
        "sector_limit_ok": true,
        "cash_buffer_ok": true
      }
    }
  ],
  "rejected_or_watch": [
    {
      "ticker": "MSFT",
      "reason": "Game signal immaterial to mega-cap parent."
    }
  ]
}
```

Rows written to `trade_plans` and `trade_orders` must remain `pending` until a
human approves them.

## Hard Constraints / Source-Risk Notes

- Do not execute orders from this skill.
- All orders require human approval and the in-tool `status = approved` guard.
- Alpaca paper trading is the execution path; no broker UI automation.
- Do not size positions without materiality, confidence, and concentration checks.
- Benchmark-relative reporting against the S&P 500 is required.
