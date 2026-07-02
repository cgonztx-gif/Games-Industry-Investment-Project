---
trigger: >
  Use this skill when classifying Steam news or developer update posts,
  interpreting patch_events rows, evaluating patch cadence, detecting slowing
  or absent live-service support, flagging monetization-without-content, or
  tracking roadmap adherence.
---

# Patch Cadence Analysis

## Purpose

Infer developer investment and live-service commitment from official update
behavior. Patch cadence is a product-health and management-quality signal: it
shows whether a studio is maintaining a live title, responding to player pain,
and shipping against its stated roadmap.

## Inputs

- Steam News API items from `ISteamNews/GetNewsForApp`.
- Official developer blogs, patch pages, and publisher announcements.
- Game metadata: genre, release date, live-service flag, Steam app id, and parent
  ticker.
- Historical `patch_events`: date, patch type, scope summary, cadence delta, and
  source URL.
- Optional roadmap items from official posts: promised feature, target window,
  shipped date, and status.

## Methodology

### 1. Patch Taxonomy

Classify every update into one primary type:

| Type | Definition | Typical signal |
| --- | --- | --- |
| `hotfix` | Bug, crash, exploit, or server fix with narrow scope | Responsiveness |
| `balance` | Tuning weapons, heroes, economy, matchmaking, or ranked rules | Live operations |
| `content_drop` | New maps, modes, characters, quests, expansions, seasons, or events | Investment |
| `monetization` | Store, battle pass, cosmetics, pricing, currency, bundles, or paid DLC | Revenue push |
| `engine` | Performance, platform, rendering, tools, anti-cheat, infrastructure | Technical health |
| `other` | Community post or announcement without clear product change | Low product signal |

Prefer the concrete shipped change over the marketing title. If an update both
adds content and monetization, classify by the dominant player-facing payload
and add a note for the secondary signal.

### 2. Cadence Baselines

Resolve a baseline in days, then compare `cadence_delta` to that baseline.

Default baselines:

| Game / genre context | Baseline |
| --- | ---: |
| First 30 days after launch for live-service title | 7-14 days |
| Battle royale | 10 days |
| Competitive shooter or MOBA | 14 days |
| MMO or shared-world RPG | 21 days |
| Sports seasonal title | 30 days |
| Other live-service title | 21 days |
| Premium non-live title | 45-90 days, depending on launch age |

Flags:

- `on_pace`: cadence delta is within 1.5x baseline.
- `slowing`: cadence delta is greater than 1.5x baseline.
- `absent`: cadence delta is greater than 3.0x baseline for a live-service title.
- `unknown`: insufficient history or no reliable update source yet.

Treat launch-window silence more seriously than mature-title silence when the
game was sold as live-service.

### 3. Slowing or Absent Patch Flags

Raise a medium warning when:

- A live-service title has no meaningful update for more than 1.5x its baseline.
- Only `other` posts appear while player issues remain unresolved.
- Hotfixes repeat for the same issue without a durable fix.

Raise a high warning when:

- No content or balance update appears for more than 3x baseline.
- Silence follows a severe sentiment or player-count deterioration.
- The studio misses an official roadmap window without explanation.

### 4. Monetization Without Content

Flag monetization-without-content when an update adds or promotes paid items,
currency, battle pass, bundles, or store changes without a commensurate content
drop, balance improvement, or technical fix.

Interpretation:

- One isolated store update can be normal.
- Repeated monetization-only updates during declining engagement suggest the
  studio is extracting from a weakening title.
- Monetization-heavy updates after a trust-break event increase churn risk.

### 5. Roadmap Adherence

Track only official roadmap commitments. Do not treat rumors, Discord comments,
or influencer expectations as promises.

For each roadmap item, record:

- `promised_item`
- `promised_window`
- `actual_ship_date`
- `status`: `on_time`, `late`, `missed`, `cancelled`, or `unclear`
- `explanation`: official reason if provided

Leadership signal:

- On-time or clearly explained delays support execution confidence.
- Repeated missed windows without specifics reduce leadership reliability.
- Cancelled content after monetization updates is a high-trust-risk pattern.

## Output Contract

Persist factual events to `patch_events` and return an interpretive summary:

```json
{
  "game_id": "uuid",
  "date": "YYYY-MM-DD",
  "patch_type": "content_drop",
  "scope_summary": "Short factual summary with source title.",
  "cadence_delta": 18,
  "source_url": "https://...",
  "analysis": {
    "cadence_baseline_days": 14,
    "cadence_status": "slowing",
    "monetization_without_content": false,
    "roadmap_status": "on_time",
    "severity": "medium"
  }
}
```

If the current database schema lacks optional analysis columns, include those
fields in the worker return object or a JSON metadata field after a future
migration. Do not silently drop the reasoning.

## Hard Constraints / Source-Risk Notes

- Use Steam News API and official developer/publisher pages.
- Do not scrape Discord; it is Tier 4 excluded for this project.
- Do not use Steam community forums as a patch source.
- Keep per-game failures isolated so one missing feed does not fail the run.
