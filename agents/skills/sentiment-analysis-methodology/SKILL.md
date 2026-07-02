---
trigger: >
  Use this skill when building or reviewing the sentiment worker, interpreting
  sentiment_snapshots rows, extracting aspect-based player sentiment from
  Reddit, Steam reviews, or YouTube comments, evaluating vocal-minority risk,
  or handling lagged preliminary divergence notes.
---

# Sentiment Analysis Methodology

## Purpose

Convert noisy community text into structured sentiment inputs that the
Synthesis Agent can compare with same-week quantitative signals. This skill
defines the worker-side method only: VADER baseline scoring, Claude Haiku
aspect-based sentiment analysis, top-theme extraction, vocal-minority guarding,
and a clearly labeled lagged preliminary divergence hint.

The Sentiment Subagent does not compute the authoritative same-week divergence
check. Synthesis owns that check because it is the first layer that sees
same-week sentiment, player metrics, review velocity, patch cadence, studio
signals, and equity context together.

## Inputs

- Text items from allowed sentiment sources:
  - Reddit public read-only `.json` endpoints through `RedditSource`.
  - Steam `appreviews` text through the Tier-2 cached adapter.
  - YouTube comments through the official YouTube Data API.
- Per-item metadata where available: `text`, engagement score, author/account
  identifier, created time, source, URL, and helpful/upvote counts.
- Game metadata: `game_id`, title, genre, release date, live-service flag,
  Steam app id, and watchlist sentiment tier.
- Last stored `player_metrics` row only for an optional lagged preliminary flag.

## Methodology

### 1. Normalize Coverage

Score each source independently. Do not merge Reddit, Steam, and YouTube into a
single raw corpus before writing rows; source disagreement is itself useful.

Use the watchlist sentiment tier to control Reddit cost:

- `tier_a`: collect subreddit listing posts plus comments from the top posts.
- `listing_only`: collect subreddit listing posts only.

If a source is unavailable, skip that source and return a coverage note. Do not
fill missing Reddit, YouTube, or Steam text with another source.

### 2. VADER Baseline

Run VADER over every usable text item before any LLM step. VADER is the
deterministic baseline because it handles short social text, capitalization,
degree modifiers, punctuation emphasis, slang, and emoji better than a generic
lexicon.

Use engagement weighting:

```text
weight = max(1, engagement_score)
weighted_avg = sum(vader_compound_i * weight_i) / sum(weight_i)
sentiment_score = ((weighted_avg + 1) / 2 * 9) + 1
```

Round to one decimal place and return 5.5 when there are no usable texts.

Interpretation bands:

| Score | Label | Interpretation |
| --- | --- | --- |
| 1.0-3.5 | bearish | Broadly negative community text |
| 3.6-6.4 | neutral | No strong directional text signal |
| 6.5-10.0 | bullish | Broadly positive community text |

### 3. Claude Haiku ABSA

Run Claude Haiku aspect-based sentiment analysis when at least 5 usable texts
exist for the game/source pair. Use the most-engaging texts first, cap the batch
at 50 texts, and cap each text at about 600 characters.

Extract aspect-sentiment pairs rather than a whole-post label. Normalize aspect
names to lowercase snake_case. Preferred aspects:

- `core_gameplay`
- `monetization`
- `server_stability`
- `content_updates`
- `matchmaking`
- `progression_system`
- `ui_ux`
- `graphics`
- `performance`
- `community_trust`
- `anti_cheat`
- `roadmap`

Return only aspects mentioned by at least 2 texts. ABSA is non-fatal: if the LLM
call fails, persist the VADER score with an empty `top_themes` array.

### 4. Top Themes

Cluster similar ABSA aspects into the top 3 themes by volume and intensity.
Intensity should combine polarity and engagement-weighted text volume; a small
number of highly engaged posts can qualify only if the vocal-minority guard does
not mark the theme as concentrated.

Theme interpretation examples:

| Theme | Negative meaning | Positive meaning |
| --- | --- | --- |
| `monetization` | Battle pass, store, pricing, or pay-to-win backlash | Fair value or accepted cosmetics |
| `server_stability` | Launch or patch reliability risk | Infrastructure recovery |
| `content_updates` | Stale live-service loop | Content cadence is landing |
| `core_gameplay` | The game loop is rejected | Durable player-product fit |
| `community_trust` | Trust break, rollback demands, review bombing risk | Goodwill or successful apology |

### 5. Vocal-Minority Guard

Flag concentrated sentiment instead of treating it as broad community mood.

Use all available signals:

- Engagement-weighted VADER reduces low-engagement outliers.
- Distinct author counts reduce repeated-account dominance.
- Top-author concentration above roughly 30% of weighted engagement is a
  concentration warning.
- Repeated phrasing, identical links, or identical complaint framing across many
  accounts is a coordination warning.
- Source disagreement matters: Reddit-only outrage with stable Steam reviews is
  weaker than agreement across Reddit, Steam, and YouTube.

When only engagement score is available, say the guard is partial rather than
claiming account-level confidence.

### 6. Lagged Preliminary Divergence Only

The sentiment worker may compare this week's sentiment against the latest stored
player metric row, but any flag must be labeled "Preliminary lagged flag".

Valid preliminary cases:

- Bearish sentiment (`<= 3.5`) while the latest stored review base is material
  (`review_count >= 100`).
- Bullish sentiment (`>= 6.5`) while the latest stored review base is thin
  (`review_count < 500`).

Never present this as the same-week text-vs-quant divergence check. The
Synthesis Agent supersedes it with current-week player metrics, review velocity,
patch cadence, and other worker outputs.

## Output Contract

Persist one row per `(game_id, date, source)` into `sentiment_snapshots`:

```json
{
  "game_id": "uuid",
  "date": "YYYY-MM-DD",
  "source": "reddit | steam | youtube",
  "sentiment_score": 1.0,
  "top_themes": [
    {
      "aspect": "monetization",
      "polarity": "negative",
      "mention_count": 12,
      "intensity": "high"
    }
  ],
  "divergence_flag": false,
  "vocal_minority_note": "string or null"
}
```

Worker return summaries may include counts, skipped sources, and errors, but raw
post bodies and full comment corpora must not cross back to the orchestrator.

## Hard Constraints / Source-Risk Notes

- Do not use X/Twitter in the MVP; it is Tier 3 deferred.
- Do not scrape LinkedIn, Discord, or Steam community forums for sentiment.
- Reddit `.json` and Steam `appreviews` are Tier 2 and must stay behind cached,
  rate-limited adapters with stale fallback.
- YouTube collection must use the official Data API. Avoid quota-expensive video
  discovery patterns unless explicitly justified.
- Do not let ABSA failures block VADER persistence.
