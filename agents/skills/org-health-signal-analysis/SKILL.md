---
trigger: >
  Use this skill when interpreting studio_signals rows, analyzing job postings,
  mapping role-type spikes to strategic intent, scoring layoffs or executive
  departures, detecting consolidation, acquisition, or IPO events, or assessing
  leadership stability and studio distress.
---

# Org Health Signal Analysis

## Purpose

Interpret public organizational signals as evidence about studio strategy,
capacity, leadership stability, and distress. The goal is not to infer private
headcount precisely; it is to convert observable signals into cautious,
structured `studio_signals` that synthesis can combine with product and equity
data.

## Inputs

- Official public job-board APIs: Greenhouse, Lever, and Ashby.
- Low-volume studio careers pages for studios without hosted ATS boards.
- SEC EDGAR filings, especially 8-K, 10-Q, and 10-K events.
- Press releases, investor-relations pages, official blogs, and publisher news.
- Existing `studio_signals` history for trend and leadership-stability scoring.

## Methodology

### 1. Hiring-Signal Taxonomy

Map role clusters to likely strategic intent:

| Role cluster | Intent signal |
| --- | --- |
| Live operations, online services, backend, SRE | Operating or scaling live-service title |
| Economy, monetization, product management, growth | Revenue optimization or F2P/live pivot |
| Engine, tools, rendering, platform | New technology investment or long-cycle build |
| QA, localization, release management | Imminent launch, port, expansion, or major patch |
| Content design, narrative, level design, art | Content pipeline expansion |
| Data science, analytics, experimentation | Live ops maturity or monetization optimization |
| Recruiting, HR, studio operations | Scaling organization or new project ramp |

Hiring spikes require context. A few replacement roles do not equal strategic
expansion.

### 2. Role-Type Intent Mapping

Use both volume and mix:

- `hiring_surge`: broad increase across multiple functions.
- `live_service_push`: online, backend, economy, data, or live-ops roles dominate.
- `launch_readiness`: QA, localization, release, platform certification, and
  community roles rise together.
- `new_project_investment`: engine, gameplay, art, narrative, and production roles
  appear together after a quiet period.
- `monetization_pivot`: economy, store, product, pricing, and growth roles rise
  without matching content roles.

Score as higher confidence when postings mention a specific franchise, live
title, engine, platform, or product area.

### 3. Distress Indicators

Classify negative org signals:

| Signal | Severity guidance |
| --- | --- |
| Layoffs under 5% or role-limited | Low to medium |
| Layoffs 5-15%, project cancellation, or post-launch cuts | Medium to high |
| Layoffs above 15%, studio closure, or repeated rounds | High |
| Executive departure with named successor | Low to medium |
| Executive departure without successor during product trouble | Medium to high |
| Consolidation, merger integration, or office closure | Medium unless tied to layoffs |
| Going-concern, impairment, or covenant language | High |

Avoid double-counting syndicated articles. Prefer primary company statements,
SEC filings, and official posts.

### 4. Acquisition, IPO, and Ownership Detection

Detect ownership changes from:

- SEC filings and exhibits.
- Press releases from buyer, seller, or parent company.
- Investor-relations transaction pages.
- IPO registration filings, ticker changes, or spinoff filings.

When ownership changes, emit a signal that synthesis and
`equity-signal-mapping` can use to re-resolve the studio-to-ticker mapping.

### 5. Leadership Stability and Distress Scoring

Maintain a 1-10 org-health score for reasoning, even if only severity is
persisted today:

- 8-10: stable leadership, selective hiring aligned with shipped strategy.
- 6-7: normal churn or mixed signals.
- 4-5: leadership churn, slowed hiring, or mild consolidation.
- 1-3: layoffs, closure risk, unexplained executive exits, or repeated distress.

Leadership-stability index:

- Count C-suite, studio head, GM, creative director, and franchise lead changes
  over the last 12 months.
- Weight departures during launch, major patch, or acquisition periods more
  heavily.
- Reduce severity when succession is immediate and strategic rationale is clear.

## Output Contract

Persist one row per material signal to `studio_signals`:

```json
{
  "studio_id": "uuid",
  "date": "YYYY-MM-DD",
  "signal_type": "hiring_surge | layoffs | exec_departure | acquisition | ipo | press_release",
  "description": "Concise factual signal plus interpretation.",
  "severity": "low | medium | high",
  "source_url": "https://..."
}
```

Worker summaries may additionally return:

```json
{
  "studio_id": "uuid",
  "org_health_score": 6.5,
  "leadership_stability": "stable | watch | unstable",
  "intent_tags": ["live_service_push"],
  "distress_tags": []
}
```

## Hard Constraints / Source-Risk Notes

- Do not scrape LinkedIn; it is Tier 4 excluded.
- Prefer official hosted ATS APIs. Use Playwright careers-page fallback only at
  low volume for studios without Greenhouse, Lever, or Ashby.
- SEC EDGAR requires a declared user agent and pacing.
- Rumors can trigger a deep dive, but do not persist as confirmed org signals
  without an official or primary source.
