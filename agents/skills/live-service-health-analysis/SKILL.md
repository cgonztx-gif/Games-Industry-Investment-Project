---
trigger: >
  Use this skill when interpreting player_metrics rows, analyzing CCU or PCU
  trends, estimating live-service health, applying DAU/MAU or retention
  frameworks, computing rolling averages or week-over-week deltas, or producing
  product-health signals for synthesis.
---

# Live-Service Health Analysis

## Purpose

Interpret player engagement metrics as product-health signals for games
industry investing. The current pipeline mainly observes Steam current CCU,
Steam review score/count, and review velocity, but the methodology must remain
compatible with richer DAU, MAU, retention, and PCU data when those become
available.

## Inputs

- `player_metrics`: `game_id`, date, concurrent players, peak players if
  available, review score, review count, and review velocity.
- Game metadata: title, genre, release date, live-service flag, Steam app id,
  platform coverage, and parent ticker.
- Historical `player_metrics` rows for rolling averages and trend checks.
- Optional richer metrics when available: DAU, MAU, D1/D7/D30 retention, session
  count, average session length, churn, and paid conversion.
- Patch and sentiment outputs for context only; do not use them to overwrite the
  product metric facts.

## Methodology

### 1. CCU and PCU Interpretation

Steam `GetNumberOfCurrentPlayers` exposes current CCU, not historical PCU. The
pipeline's weekly snapshots build the time series. Treat same-time weekly CCU
comparisons as the cleanest available Steam proxy.

Use PCU only when a trusted source provides it. If `peak_players_24h` is null,
do not invent it from current CCU.

Interpretation rules:

- Sustained decline matters more than one-week noise.
- A content-drop spike is bullish only if the post-spike floor remains above the
  prior baseline.
- Launch decay is normal; compare the slope to genre and game age.
- Flat CCU with rising negative sentiment can be an early churn warning, but
  synthesis owns that cross-signal divergence check.

### 2. DAU/MAU and Retention Framework

When DAU and MAU are available:

```text
stickiness = DAU / MAU
```

Interpretation:

- `>= 0.20`: strong habit or event-driven daily loop.
- `0.10-0.19`: workable live-service engagement.
- `< 0.10`: weak daily habit unless the genre is intentionally low-frequency.

Retention benchmarks are genre-sensitive, but use these starting points:

- D1 around 35-40% is healthy for many F2P and multiplayer launches.
- D7 above 15-20% suggests the core loop is holding.
- D30 around 5-10% can be healthy for many genres; higher is expected for
  competitive or social live-service games.

If DAU, MAU, or retention are unavailable, mark them `unavailable`. Do not
translate Steam CCU directly into DAU/MAU or retention.

### 3. Rolling Average and Week-over-Week Rules

Use deterministic calculations for deltas and flags. The bundled helper can be
used for this:

```powershell
python agents/skills/live-service-health-analysis/scripts/calculate_health_deltas.py metrics.json
```

Default rules:

- Compare the latest row to the prior weekly row for week-over-week change.
- Use a 4-point trailing rolling average for weekly snapshots.
- Flag `growth` when CCU rises by at least 10% week over week.
- Flag `spike` when CCU rises by at least 25% week over week.
- Flag `decline` when CCU falls by at least 15% week over week.
- Flag `severe_decline` when CCU falls by at least 30% week over week.
- Treat fewer than 2 observations as `insufficient_history`.

Review velocity is a momentum proxy:

- Rising review count with stable or rising score supports product health.
- Rising review velocity with falling score can indicate backlash.
- Flat review velocity on a live-service title can signal fading attention.

### 4. Genre-Relative Interpretation

Use genre and game age before calling a trend bullish or bearish.

| Genre / model | Healthy cadence expectation | Metric interpretation |
| --- | --- | --- |
| Competitive shooter / MOBA | Frequent balance and active daily loop | CCU declines are high-signal |
| Battle royale | Event and season spikes | Watch post-event floor, not only spike |
| MMO / shared-world RPG | Lower frequency but durable sessions | D30 and expansion cycles matter |
| Sports annualized | Seasonal cycles | Compare to sports calendar and yearly release timing |
| Cozy / sim / management | Lower daily cadence | CCU can be smaller but long-tail stable |
| Premium single-player | Launch-heavy | Decline is normal unless DLC/live model is promised |

### 5. Health Scoring

Use a 1-10 product-health score only after considering trend, scale, and genre:

- 8-10: growing or stable above baseline, positive review momentum, healthy
  retention proxies.
- 6-7: stable with no severe warning signs.
- 4-5: mixed or noisy; monitor.
- 1-3: sustained decline, weak review velocity, or low retention proxies.

Do not compare a low-scale indie and a mega-franchise by raw CCU alone; compare
against the title's parent materiality and genre expectations.

## Output Contract

Return a product-health object for synthesis:

```json
{
  "game_id": "uuid",
  "date": "YYYY-MM-DD",
  "health_score": 6.8,
  "trend_status": "stable | growth | spike | decline | severe_decline | insufficient_history",
  "ccu": 12500,
  "ccu_wow_pct": -8.4,
  "ccu_rolling_avg": 13220.5,
  "review_velocity": 318,
  "retention": {
    "dau_mau": null,
    "d1": null,
    "d7": null,
    "d30": null,
    "availability": "unavailable"
  },
  "genre_context": "competitive shooter baseline",
  "notes": ["Steam current CCU only; no DAU/MAU source available."]
}
```

Current workers persist the factual metrics to `player_metrics`; richer
interpretive fields can be returned to synthesis or added through a future
migration.

## Hard Constraints / Source-Risk Notes

- Do not infer DAU, MAU, or retention from Steam CCU.
- Steam review text and review counts from `appreviews` are Tier 2 and must stay
  cached and rate-limited.
- Prefer official Steam, IGDB, and RAWG APIs for current pipeline inputs.
- Do not add unofficial player-count scraping sources without first updating the
  data-source risk register.
