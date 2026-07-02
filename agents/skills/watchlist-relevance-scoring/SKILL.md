---
trigger: >
  Use this skill when seeding the initial watchlist, scoring discovery
  candidates, assigning watchlist sentiment tiers, generating
  watchlist_proposals rationale, or learning from rejected discovery proposals.
---

# Watchlist Relevance Scoring

## Purpose

Keep seed-time and discovery-time watchlist decisions aligned. This skill
defines the shared rubric for deciding whether a game or studio is
investment-relevant enough to track, how to assign sentiment coverage tiers, and
how to learn from rejected proposals without drifting into noisy source chasing.

## Inputs

- Candidate game metadata from Steam, IGDB, and RAWG: title, app id, release
  date, genres, platforms, hype/follow signals, review counts, and current CCU
  where available.
- Studio and parent metadata: studio name, publisher, public parent, ticker,
  acquisition or IPO signals, and known portfolio relationships.
- Discovery trigger data: Steam top-CCU movement, review velocity, upcoming
  release calendar, EDGAR acquisition/IPO filings, and Reddit mention spikes.
- Existing watchlist rows and rejected `watchlist_proposals` with reviewer
  rationale when available.
- Sentiment coverage hints: known subreddit, Steam review availability, YouTube
  channel coverage, and live-service status.

## Methodology

### 1. Apply Required Gates

Active watchlist entries need an investable public parent. Do not guess a ticker.

Required gates:

- Public parent or credible public-market event exists.
- Parent ticker can be resolved through the canonical studio-to-ticker mapping
  rules in `equity-signal-mapping`.
- Game has enough observable data to monitor at least one product-health signal.
- Candidate is not already represented by an existing active watchlist row.

Private studios can be logged as excluded or proposed only when the trigger is a
specific public-market event such as acquisition, IPO filing, spinoff, or public
parent transfer.

### 2. Score Investment Relevance

Use a 100-point rubric after required gates pass:

| Dimension | Points | Guidance |
| --- | ---: | --- |
| Public-parent confidence | 25 | Direct owned studio or disclosed publishing relationship scores highest |
| Materiality to parent | 25 | Franchise scale, revenue disclosure, CCU, review base, or portfolio scarcity |
| Live-service or repeat revenue | 20 | Ongoing content, battle pass, subscriptions, DLC, seasons, or recurring spend |
| Observable signal coverage | 15 | Steam metrics, reviews, Reddit/YouTube coverage, patch feed, public filings |
| Timeliness | 10 | Recent launch, upcoming 60-day release, major patch, earnings proximity |
| Uniqueness | 5 | Adds new exposure not already covered by the same parent/franchise |

Default thresholds:

- `>= 70`: add at seed time or submit a high-confidence discovery proposal.
- `50-69`: submit a watch proposal only if the trigger is timely and material.
- `< 50`: reject or ignore unless a public-market event changes the thesis.

### 3. Materiality Criteria

Materiality asks whether the game can plausibly matter to the parent ticker.

High materiality:

- Flagship franchise, major live-service title, or disclosed key release.
- Top-50 Steam CCU, strong review velocity, or clear cross-platform scale.
- Smaller public parent where one title can move revenue or sentiment.

Medium materiality:

- Recognized franchise or relevant upcoming title, but parent has many larger
  revenue streams.
- Meaningful PC signal but likely cross-platform revenue is unknown.

Low materiality:

- Small catalog title inside a mega-cap parent with no evidence of outsized
  revenue, strategic importance, or community scale.

### 4. Live-Service Criteria

Treat a title as live-service when at least two signals are present:

- Recurring seasons, events, or roadmap.
- Battle pass, cosmetics store, expansion cadence, subscription, gacha, or
  recurring DLC model.
- Multiplayer or shared-world design where retention matters.
- Patch cadence intended to sustain engagement after launch.
- Current CCU/review activity suggests an active player base.

Single-player premium titles can still enter the watchlist if release timing,
parent materiality, or earnings proximity makes them investable.

### 5. Sentiment Tier Assignment

Assign `sentiment_tier` at seed or approval time so weekly collection cost is
predictable.

Use `tier_a` when at least one is true:

- Public ticker is mapped and the title is material to the parent.
- Live-service title with active player base or recent major update.
- Top-CCU or high-review-velocity game.
- Launch or major expansion occurred recently, or release is within 60 days.
- Known active subreddit where comments are likely to add signal.

Use `listing_only` when:

- The title is long-tail, low materiality, or lacks strong community coverage.
- The subreddit is small or sparse.
- The game is tracked mainly for catalog completeness or parent exposure.

### 6. Discovery Trigger Thresholds

Escalate untracked candidates when one trigger is strong or two weaker triggers
agree:

- Steam top-50 CCU entry or large week-over-week CCU rank movement.
- Review velocity spike for a public-parent title.
- IGDB upcoming release with high hype/follows in the next 60 days.
- EDGAR or press-release evidence of acquisition, IPO, spinoff, or parent change.
- Reddit mention spike for a mapped public-parent game, using the Tier-2 adapter.

Noise-only triggers should not create proposals without a public-parent path.

### 7. False-Positive Learning

Read rejected proposals before scoring new discovery candidates.

Use reviewer rejection reasons as soft constraints:

- `private_or_unmappable`: raise the public-parent evidence requirement for
  similar candidates.
- `immaterial_to_parent`: raise materiality threshold for the same parent.
- `duplicate_exposure`: check franchise and parent coverage earlier.
- `low_signal_coverage`: require at least two observable sources next time.
- `bad_source_noise`: downweight that trigger type for the same genre/community.

Do not permanently blacklist a game from one rejection. Reconsider when a new
public-market event, launch, acquisition, or major player-base change appears.

## Output Contract

For seed-time active entries, produce:

```json
{
  "game_id": "uuid",
  "studio_id": "uuid",
  "ticker": "TTWO",
  "active": true,
  "added_by": "seed",
  "sentiment_tier": "tier_a",
  "rationale": "Public parent, material live-service signal, Steam coverage."
}
```

For discovery proposals, write or return:

```json
{
  "game_id": "uuid",
  "studio_id": "uuid",
  "trigger_signal": "steam_top_ccu_entry",
  "score": 82,
  "claude_rationale": "Why this candidate is investment-relevant.",
  "status": "pending",
  "recommended_sentiment_tier": "tier_a",
  "rejection_learning_applied": ["duplicate_exposure_checked"]
}
```

## Hard Constraints / Source-Risk Notes

- Do not add private or unmappable studios to active tracking as investable
  equities.
- Do not use Tier 4 sources for discovery or scoring.
- Reddit mention spikes must use the existing cached Reddit adapter.
- Public-parent mapping belongs to `equity-signal-mapping`; this skill consumes
  that mapping rather than inventing tickers.
