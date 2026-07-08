# Decision Memo: Adding a News-Article Stream to the Sentiment Pipeline

**Scope:** Enrich the existing Sentiment worker with a `source='news'` type, alongside Reddit/Steam/YouTube. Not an event detector (acquisitions/layoffs/lawsuits stay with Studio Intel). Free-tier only. This memo is a plan, not code.

**Facts current as of mid-2026; free-tier terms drift, so re-verify GDELT rate-limit behavior and NewsAPI/GNews ToS at build time.**

---

## 0. TL;DR recommendation

1. **Ingest via two Tier-1 free sources, not a news API vendor:**
   - **GDELT DOC 2.0 API** (free, keyless) for broad, keyword-searchable, per-entity discovery *with a built-in tone metric*.
   - **Curated games-press RSS feeds** (GamesIndustry.biz, Eurogamer, VGC, PC Gamer, RPS, VG247, Game Developer) — a near-drop-in for your existing dev-blog feed client.
   - **Google News RSS** as a Tier-2 per-game fallback when GDELT coverage is thin.
2. **Exclude NewsAPI.org and GNews free tiers (Tier 4 for you):** both are development/localhost-only and forbid production/commercial use. Your Actions cron is a production environment; using them would violate ToS. Named substitute: GDELT + RSS.
3. **Do not reuse the community-sentiment scoring wholesale.** VADER is miscalibrated for measured journalistic prose; the vocal-minority guard is meaningless for a handful of professional articles; and the player-complaint ABSA vocabulary is the wrong lens. Replace with a **media stance/frame** classification. See §3 — this is the most important section.
4. **Structurally, build news as its own ingestion module feeding a shared `news_items` table, and make the Sentiment worker a *consumer* of it** — not a news-fetcher itself. This honors your "feed sentiment scoring" scope now while leaving Studio Intel and a future Discovery agent able to reuse the same substrate. See §5.

---

## 1. Source recommendations (risk-register format)

Drop-in rows for the data-source-risk-register. Format: source / provides / access path / tier / risk & posture / mitigation-fallback.

### 1a. GDELT DOC 2.0 API — **RECOMMENDED PRIMARY**

- **Provides:** Global news-article metadata matching a keyword/domain/theme query over a rolling ~3-month window. Per-article fields: `url`, `title`, `seendate`, `domain`, `language`, `sourcecountry`, `socialimage`. Also aggregate modes: `TimelineTone` (avg tone of matching coverage over time) and `TimelineVol` (coverage volume as % of global monitored coverage). **No article body text.**
- **Access path:** `https://api.gdeltproject.org/api/v2/doc/doc?query=...&mode=artlist&format=json&maxrecords=250&timespan=1w`. No API key. Requires a browser-like `User-Agent` header or it may 429. Reference Python client exists (`alex9smith/gdelt-doc-api`) — useful as a *structural* reference, but you'll likely wrap your own to fit adapter conventions.
- **Tier:** **Tier 1 by ToS** (official, free, open API). **Operationally treat as Tier 2** — apply the full adapter pattern (rate limiter + cache-with-TTL-and-stale-fallback + graceful degradation) because it is rate-limited with no SLA and has occasional format/UA quirks. Flag this tension explicitly in the register: "official API, Tier-2 resilience posture."
- **Risk & posture:** Rate limits are burst/QPS protection, not a daily quota — fine for weekly serialized queries, but a tight loop across a large watchlist can trip 429s. No SLA; occasional query-syntax fragility (phrase quoting, boolean operators). Tone is GDELT's own metric, not yours — a dependency on their NLP.
- **Mitigation-fallback:** Serialize per-entity queries with a small delay + retry-with-backoff on 429 (honor any `Retry-After`); cache every response in `api_cache` under namespace `gdelt` keyed by the query string; on block, fall through to RSS/Google-News for that entity and return partial. For at-scale/native-language needs later, GDELT's Web NGrams 3.0 downloadable dataset is the documented escape hatch.

### 1b. Curated games-press RSS feeds — **RECOMMENDED SECONDARY**

- **Provides:** Recent articles from named trade/enthusiast outlets. Feed items give title, link, published date, summary/description (sometimes full content). Deeper games-specific coverage than a general news index; higher signal-to-noise for our domain than GDELT's global net.
- **Access path (verified as standard RSS/Atom):**
  - GamesIndustry.biz — `https://www.gamesindustry.biz/feed` (also `/rss/gamesindustry_news_feed.rss`)
  - Eurogamer — `https://www.eurogamer.net/feed`
  - VGC — `https://www.videogameschronicle.com/feed/`
  - PC Gamer — `https://www.pcgamer.com/rss/`
  - Rock Paper Shotgun — `https://www.rockpapershotgun.com/feed`
  - VG247 — `https://www.vg247.com/feed`
  - Game Developer — `https://www.gamedeveloper.com/rss.xml`
  - (Verify each URL at build; outlets occasionally move feed paths. Confirm the ToS/robots of each permits automated feed consumption — RSS is published for exactly this, so this is low-risk, but note it in the register.)
- **Tier:** **Tier 1.** Official feeds, stdlib XML parsing, no key. This is the closest existing precedent in your codebase — reuse the dev-blog feed client structure almost verbatim (same stdlib-XML-with-HTML-scrape-fallback shape), just pointed at a *curated set of general games-journalism feeds* rather than a per-game dev-blog mapping.
- **Risk & posture:** Feeds are outlet-wide, not game-specific → the relevance-matching burden (§2) lands here hardest. Feed path changes; occasional malformed XML.
- **Mitigation-fallback:** HTML-scrape fallback already exists in the blog-feed client; cache each feed pull in `api_cache` namespace `news_rss`; a dead feed degrades to "skip this outlet this run," never fatal.

### 1c. Google News RSS — **RECOMMENDED FALLBACK (per-game targeting)**

- **Provides:** Per-query search results as an RSS feed — `https://news.google.com/rss?q=<url-encoded query>`. Lets you query *per watchlist entity directly*, which sidesteps some of the broad-net relevance problem.
- **Access path:** Keyless RSS. Parse like any feed.
- **Tier:** **Tier 2** (public-but-unofficial). Mandatory adapter pattern.
- **Risk & posture:** Unofficial, undocumented rate limits, no SLA; Google can change format or block traffic. Returns Google-redirect URLs (need unwrapping) and can be noisy.
- **Mitigation-fallback:** Cache under namespace `gnews_rss` keyed by query; back off on empty/blocked; use only to fill gaps where GDELT + curated RSS under-cover a specific entity.

### 1d. Excluded sources (name the substitute, per Tier-4 convention)

- **NewsAPI.org free (Developer plan) — EXCLUDED (Tier 4 for our use).** Free tier is development/localhost-only (CORS restricted to localhost), articles delayed 24h, 100 req/day, commercial/production use forbidden in ToS. A scheduled Actions cron is a production environment → prohibited path. Only viable tier is paid (~$449/mo). **Substitute: GDELT + curated RSS.**
- **GNews free — EXCLUDED (Tier 4 for our use).** ~100 req/day, free tier also dev-only / no commercial use. **Substitute: Google News RSS (Tier 2) for the same "Google-News-sourced" coverage without the ToS problem.**
- **The Guardian Open Platform — OPTIONAL, note-only.** Genuinely free, commercial-OK, returns full body text, generous limits. But single-source and thin on games. Not worth wiring now; note as a possible future quality-layer if you ever want full article text for a specific high-value story.

---

## 2. Game-relevance matching strategy

**The problem:** GDELT keyword search, curated RSS, and Google News all return broad results; you must reliably bind an article to a specific watchlist game/studio. Your existing per-game curated-URL pattern (great for dev blogs) does **not** transfer, because news feeds are outlet-wide, not game-scoped.

**Recommended approach — curated alias dictionary + cheap deterministic filter, with an LLM disambiguation pass only for ambiguous/high-value hits:**

1. **Per-watchlist-entity alias record** (extends your existing per-game config philosophy — you're already comfortable curating per-game config): for each entity store `canonical_title`, `aliases` (abbreviations/nicknames — CS2, BG3, PoE2, CoD, WoW), `studio`, `publisher`, `franchise`. This is manual curation, but it's the same kind of curation your dev-blog mapping already does, and it's the highest-precision lever you have.
2. **Stage-1 deterministic match** against `title + description/snippet`: normalized substring/word-boundary match on canonical title + aliases, with studio/publisher as weaker secondary signals. Cheap, no API cost, runs on every candidate.
3. **Stage-2 LLM disambiguation (Haiku), only for ambiguous candidates:** when a match is on a common-word title/franchise or on studio-only, ask Haiku "Is this article primarily about `{entity}`? Answer yes/no + which entity." **Cache the verdict keyed by article URL** so a given article is judged once, ever, across the whole watchlist. This keeps cost bounded (§4).

**Why not pure NER / embeddings:** off-the-shelf NER won't reliably tag game titles (they're not standard named-entity classes and collide with common words); embedding similarity is fuzzy and adds infra without fixing the core false-positive classes below. A curated dictionary is more precise *for a bounded watchlist* and matches your existing curation muscle. Revisit NER/embeddings only if the watchlist grows past the point where curation scales.

**Expected failure modes (tell your coding assistant to build test cases for these):**

- **False positives:**
  - Common-word titles: *Control, Destiny, Prey, Rust, Halo, The Division, Among Us, Dead Space* → match unrelated news. Highest-risk class; these entities should be flagged in the dictionary to *force* Stage-2 disambiguation.
  - Studio/publisher names as common words, or an article about the publisher that isn't about your specific watchlisted game.
  - Passing mentions: listicles ("top 10 games of..."), roundups, ads embedded in unrelated coverage — article isn't *about* the game.
  - Franchise vs. installment: an article about *Call of Duty* generically vs. the specific title you watchlist.
- **False negatives:**
  - Nicknames/abbreviations not in the alias list; typos; localized titles; non-English coverage (GDELT indexes many languages — decide whether to include).
  - Articles about the studio that never name the game.
  - Oblique headlines that reference the game only in body text — and remember **GDELT gives you no body text**, so title+snippet is all you match on. This structurally raises GDELT's false-negative rate. RSS descriptions are usually richer.

**Net:** precision-first via the dictionary + forced disambiguation on common-word entities; accept some false negatives (a missed article rarely changes a weekly narrative signal, whereas a false-positive article corrupts a score).

---

## 3. Candid methodology take (the important one)

**You are right to be suspicious. Do not treat press coverage as "community sentiment," and do not reuse the community scoring stack as-is.** Three specific problems and what to do instead:

### 3a. VADER is the wrong instrument for journalism
VADER is a lexicon+rules model tuned for **social-media microblog text** — it keys on emoji, ALL-CAPS, "!!!", slang, and degree modifiers. Professional journalism is deliberately measured, hedges, and *attributes* opinion to sources rather than asserting it. So VADER will score most articles near-neutral and be noisy on the rest. Worse: your VADER step is **engagement-weighted**, and feed articles have no upvotes/likes — the weighting input doesn't exist. **Recommendation:** drop VADER for `source='news'`. Prefer either (a) **GDELT's built-in tone metric**, which is computed over the article and designed for news, as a free numeric baseline, and/or (b) a **Claude stance classification** (below). Keep VADER out of this source, or relegate it to a weak, untrusted secondary.

### 3b. The vocal-minority guard is meaningless here — skip it, don't reuse it
That guard (engagement weighting + distinct-author-count + top-author-concentration) exists to detect when a **many-author volume signal** is driven by a loud few. A handful of articles from professional outlets has no "vocal minority" — every author is a professional outlet. Distinct-author-count and concentration don't measure the same phenomenon. **Recommendation:** skip the vocal-minority guard for news entirely. Replace it with the news-appropriate analog: a **coverage-breadth descriptor** — how many *distinct outlets* covered this, and is the signal one outlet repeating vs. broad pickup. Store it in `vocal_minority_note` (repurposed) or a new field; it answers the same underlying question ("is this narrow or broad?") in the right units for news.

### 3c. The player-complaint ABSA vocabulary is the wrong lens — measure stance/frame instead
Your aspect vocab (monetization, matchmaking, anti_cheat, progression_system, server_stability, ...) is **player-experience-gripe-shaped**. Journalists occasionally hit those (monetization is a beat; server meltdowns get covered), but press coverage mostly operates on a *different axis*: business performance, sales, layoffs-adjacent framing, review-score narratives, controversy, roadmap/strategy, competitive positioning. Running player-complaint ABSA on journalist prose will capture thin overlap (monetization, content_updates, roadmap) and miss the dimension that actually matters in press coverage.

**What news honestly measures is media *narrative/framing*, not player *sentiment*.** So replace the ABSA extraction for this source with a **stance + frame** classification (Haiku, or Sonnet for high-priority games):
- **Stance:** the article's posture toward the company/game — positive / neutral / negative framing (map to the same 1.0–10.0 scale so it slots into `sentiment_score`, **but document that for news rows this column means "media tone/stance," not community sentiment**).
- **Frame:** which axis the coverage is on — e.g. `financial_performance`, `product_quality`, `controversy`, `roadmap_future`, `competitive_position`, `monetization`. Store in `top_themes` jsonb (same column, news-appropriate vocabulary).

**Why this is still coherent with your architecture:** your design already scores each source **independently and never merges corpora**, treating source disagreement as signal. A news "tone/stance" score sitting beside community sentiment scores is fine — *as long as the consumer knows they measure different things.* The one thing that would be genuine misuse: the Synthesis agent naively averaging news tone with Reddit/Steam/YouTube sentiment into one number. Flag that explicitly (see §5).

### 3d. Aggregate per-game-per-week, not per-article
You want weekly narrative direction, not per-article scores. Batch a game's week of matched articles into **one** stance/frame call that returns an aggregate stance + dominant frames + coverage breadth. This is cheaper (§4), more robust to single-article noise, and matches the weekly-briefing cadence.

---

## 4. Volume / cost math

**Assumed scale:** watchlist of 20–50 games, weekly cadence. (Swap in your real number; the shape holds.)

**Fetch layer — $0:**
- GDELT: 1 keyword query per entity per week = 20–50 queries/week, serialized with backoff. No daily quota; well under burst limits. Cost: $0.
- Curated RSS: ~7 feeds pulled once (or a few times)/week. Trivial. Cost: $0.
- Google News RSS (fallback, only thin-coverage entities): ≤ ~20 queries/week. Cost: $0.

**Claude token layer — the only cost, and it's marginal:**
- **Stage-2 relevance disambiguation (Haiku)**, only on ambiguous candidates, cached by URL: ~250 in / ~15 out tokens each. Even a generous 300 judgments/week ≈ **~75K in / ~4.5K out per week**, and the cache means recurring weeks are far cheaper (repeat articles already judged).
- **Stance/frame classification (Haiku; Sonnet for priority games), aggregated per-game-per-week:** ~20–50 calls/week, each bundling the game's articles. Say ~600 in / ~80 out tokens per call → **~30K in / ~4K out per week**.
- **Weekly total ≈ ~100–150K tokens**, dominated by Haiku. At Haiku-tier pricing this is on the order of **cents per week**; even routing priority games' stance calls to Sonnet keeps it comfortably in low-single-digit dollars/week at most. It is marginal next to your existing per-game ABSA spend.
- *(Confirm current per-token Haiku/Sonnet rates at build — pricing changes. The durable takeaway is the token volume, which is small.)*

**Design levers that keep cost flat as the watchlist grows:** cache disambiguation verdicts by URL (dedupes the cross-entity re-judging problem); aggregate per-game-per-week instead of per-article; reserve Sonnet for high-priority games only.

---

## 5. Architectural recommendation: build it as its own thing, make Sentiment a consumer

**Your design docs disagree about where news lives (Sentiment vs. Studio Intel vs. a future Discovery agent). That disagreement is the tell: news is a shared concern, not a Sentiment-owned one.** A general news stream is a structurally poor fit for the Sentiment worker's per-source-independent-score model, for four reasons:

1. **Semantic mismatch.** The Sentiment worker's contract is "per game, per source, an independent *community-sentiment* score." News tone/stance is a *different measurement*. Writing it as just another `sentiment_snapshots` row makes the score column mean two different things depending on `source`, and any consumer that reads the table uniformly will silently mix apples and oranges.
2. **News is cross-cutting.** One article routinely spans multiple watchlist entities (an EA piece touches several EA games; an industry-trend piece touches many). A per-game worker fetching per game will redundantly fetch and re-judge the same article N times. A single ingestion pass that fans out to entities dedupes naturally (cache by URL).
3. **Multiple consumers want news.** Sentiment wants tone; Studio Intel wants market-moving events (explicitly out of scope here, but it's the same raw feed); the future Discovery agent wants press-surfaced new-watchlist candidates. If news is trapped inside Sentiment, the others can't reuse it without cross-worker coupling.
4. **You already feel the seam.** The doc disagreement is friction from trying to assign a shared substrate to one owner.

**But respect your own scope** ("I've scoped this to feed sentiment scoring"). The way to ship the sentiment enrichment now *without* painting into a corner:

- **Build a standalone news-ingestion + entity-matching module** (GDELT adapter + RSS adapter + Google-News fallback → §2 matching) that writes a normalized **`news_items` table**, keyed by URL: `url` (PK), `published_at`, `outlets`/`domain`, `matched_entities` (jsonb), `title`, `snippet`, `fetched_at`. This is the shared substrate; it does fetch + cache + relevance-match **once**.
- **Make the Sentiment worker a *consumer*:** for each game, read the week's `news_items` where the entity matched, run the §3 stance/frame classification, and write a `source='news'` row into `sentiment_snapshots` with the corrected semantics. This delivers your stated goal today, no schema migration (the `source` column is free text).
- **Later, Studio Intel and Discovery read the same `news_items`** for their own purposes with zero rework.

This gives you the "own thing feeding multiple consumers" shape while still, right now, feeding sentiment scoring — the scope you asked for.

**Single most important correctness note for the Synthesis agent:** whatever number lands in the news row's `sentiment_score`, Synthesis must treat "media tone/stance" as a **distinct axis** from community sentiment and must **not** average them into one figure. If anything, news-vs-community *divergence* is itself a signal (press bullish while community sours, or vice versa) — which fits your existing "source disagreement is signal" thesis nicely.

---

## 6. Suggested build sequence (hand this to the coding assistant)

1. **`news_items` table + migration** (or reuse the schema-less pattern; `source` needs none).
2. **GDELT adapter** — Tier-2 resilience posture: common source interface, rate limiter, `api_cache` (namespace `gdelt`), User-Agent header, 429 backoff, graceful partial-return. Structural template: the Reddit multi-strategy adapter.
3. **RSS adapter** — fork the existing dev-blog feed client; point at the curated games-press feed list; keep the HTML-scrape fallback; cache namespace `news_rss`.
4. **Google News RSS adapter** — Tier-2, namespace `gnews_rss`, used only to backfill thin-coverage entities.
5. **Alias dictionary + Stage-1 deterministic matcher**, with common-word entities flagged for forced Stage-2.
6. **Stage-2 Haiku disambiguation**, verdict cached by URL.
7. **Sentiment worker: news consumer** — read week's matched `news_items`, run per-game-per-week stance/frame classification (Haiku default, Sonnet for priority), write `source='news'` row (score = media tone/stance; `top_themes` = frames; coverage-breadth in place of vocal-minority note). **Skip** VADER, **skip** the vocal-minority guard, **skip** player-complaint ABSA for this source.
8. **Synthesis contract update:** document that `source='news'` measures media tone/stance, not community sentiment; do not average across; surface news-vs-community divergence as its own signal.
9. **Test fixtures** for the false-positive/negative classes in §2.

---

*End of memo.*
