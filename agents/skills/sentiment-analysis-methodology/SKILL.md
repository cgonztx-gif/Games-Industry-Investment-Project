---
trigger: >
  Use this skill when interpreting sentiment scores, deciding whether to act on
  a bearish or bullish sentiment signal, understanding top_themes from a
  sentiment_snapshot row, or explaining why a divergence_flag was set.
---

# Sentiment Analysis Methodology

## Pipeline Overview

The sentiment worker runs a two-pass pipeline per game per source (Steam, Reddit).
Both passes write to the same `sentiment_snapshots` row; ABSA is non-fatal — if
it fails, the VADER score still lands.

```
Raw texts (Steam reviews / Reddit posts)
        │
        ▼
  [Pass 1] VADER baseline   ──→  sentiment_score (1–10 float)
        │
        ▼
  [Pass 2] Claude Haiku ABSA  ──→  top_themes [{aspect, polarity}]  (≥5 texts only)
        │
        ▼
  Preliminary lagged flag  ──→  divergence_flag (bool), vocal_minority_note (str|null)
```

---

## Pass 1 — VADER Baseline Score

**What it does:** Runs VADER's compound scorer over every text, then computes an
engagement-weighted average scaled to 1–10.

**Engagement weight:** `weight = max(1, item["score"])` where `score` is upvotes
(Reddit) or thumbs-up count (Steam). Low-engagement posts count once; high-engagement
posts are amplified proportionally. This is the primary vocal-minority guard.

**Scaling formula:**
```
compound ∈ [-1, 1]
weighted_avg = Σ(compound_i × weight_i) / Σ(weight_i)
scaled = (weighted_avg + 1) / 2 × 9 + 1   → [1.0, 10.0]
```

**Neutral baseline:** 5.5 (returned when no texts are available).

**Score interpretation:**
| Range | Signal |
|-------|--------|
| 1.0 – 3.5 | Bearish — community is net negative |
| 3.5 – 6.5 | Neutral — no strong directional signal |
| 6.5 – 10.0 | Bullish — community is net positive |

---

## Pass 2 — Claude Haiku ABSA

**When it runs:** Only when ≥ 5 texts are available for the game+source pair.
Skipped silently otherwise; `top_themes` will be `[]`.

**Model:** `claude-haiku-4-5-20251001` (classification tier — cheap and fast).

**Input:** Up to 50 texts (most-engaging first), each capped at 600 characters.

**Output:** Top 3 aspects by mention count, returned as:
```json
[{"aspect": "monetization", "polarity": "negative"},
 {"aspect": "core_gameplay", "polarity": "positive"},
 {"aspect": "server_stability", "polarity": "mixed"}]
```

**Aspect taxonomy (snake_case):**
`core_gameplay`, `monetization`, `server_stability`, `content_updates`,
`matchmaking`, `progression_system`, `ui_ux`, `graphics`, `performance`

**Inclusion threshold:** An aspect must appear in ≥ 2 reviews to be returned.
Single-mention complaints are filtered out to reduce noise.

**Investment relevance of aspects:**
- `monetization → negative` — leading indicator of player backlash; often precedes
  CCU decline by 2–4 weeks
- `server_stability → negative` — launch-period risk; watch for persistence past
  week 2 (suggests infrastructure under-investment)
- `content_updates → negative` — live-service health signal; bearish for long-hold
  thesis if sustained
- `core_gameplay → positive` + `monetization → negative` — common "good game, bad
  monetization" split; community pressure may force studio to walk back MTX

---

## Divergence Inputs And Preliminary Lagged Flag

The sentiment worker does not own the authoritative same-week divergence check.
It emits clean sentiment inputs (score, top themes, source, and note fields) for
the Synthesis Agent. Synthesis is the first layer that can compare same-week
text sentiment against same-week player metrics, review velocity, and patch
cadence.

The worker may still set `divergence_flag` as a preliminary lagged hint by
comparing this week's sentiment against the latest stored player metric row.
Any such note must explicitly say "Preliminary lagged flag".

**Preconditions for a flag:**
- `player_metrics` row exists for the game
- `review_count ≥ 100` (sparse games are excluded — signal is unreliable below
  this threshold)
- Sentiment is clearly directional (≤ 3.5 bearish OR ≥ 6.5 bullish)

**Flag cases:**

| Condition | Flag | Interpretation |
|-----------|------|----------------|
| Bearish score + review_count ≥ 100 | `divergence_flag = True` | Negative text sentiment despite a well-reviewed game; recent posts may overrepresent a vocal minority upset about a patch or event |
| Bullish score + review_count < 500 | `divergence_flag = True` | Thin sample inflated by early adopters or fans; insufficient base for a reliable buy signal |

**Action rule:** When `divergence_flag` is set by the sentiment worker, do not
act on it as the system's alpha signal. The Synthesis Agent must supersede it
with the same-week divergence check before any portfolio view is formed.

---

## Source Comparison

| Dimension | Steam reviews | Reddit posts |
|-----------|--------------|--------------|
| Signal type | Verified purchasers only | Open community (includes non-owners) |
| Recency | Configurable (default: recent) | Last 7 days, hot sort |
| Upvote weighting | Helpful votes | Reddit score (upvotes − downvotes) |
| Bias risk | Refund-window negative bias at launch | Hype cycles, meme sentiment |
| Best use | Sustained sentiment trend | Rapid event detection (patches, bans, drama) |

When both sources are available, treat them as independent signals. Agreement
between Steam and Reddit strengthens conviction; divergence warrants a closer look.

---

## Limitations and When Not to Trust the Score

1. **VADER is lexical** — it does not understand sarcasm, in-game jargon, or
   community in-jokes. Scores for niche/hardcore communities may be systematically
   biased.

2. **Reddit coverage gaps** — if `resolve_subreddit()` cannot find a matching
   subreddit, the Reddit row is skipped entirely. Check for missing Reddit rows
   before concluding "no community concern."

3. **Steam review bombing** — coordinated negative campaigns produce a low VADER
   score that is not organic. Look for a sudden spike in `review_count` alongside
   a sharp score drop; if confirmed, treat the score as noise for that week.

4. **No longitudinal context in a single score** — always compare week-over-week
   scores from `sentiment_snapshots` rather than reading a single row in isolation.

5. **ABSA latency** — aspects reflect what the sampled 50 texts discuss, not the
   full review corpus. A major issue affecting a minority of players may not appear
   in `top_themes` even if it matters for the investment thesis.
