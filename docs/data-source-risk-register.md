# Data Source Risk Register
### Every external source, classified before it's built

The Reddit adapter exists because Reddit's access rules changed under the project's feet (Nov 2025 Responsible Builder Policy) and the design had to absorb that instead of breaking. This register applies the same lens to **every** source *before* it's integrated, so no other source gets to surprise the pipeline the way Reddit tried to. The main brief's Data & APIs table is the summary view; this document is the reasoning behind it.

**The rule:** any new source enters the system through this register first. No exceptions — "it's just one endpoint" is how unowned risk accumulates.

---

## The four-question lens

Applied to every candidate source:

1. **Is there an official API?** Official + free + keyed beats everything else. Official + paid beats unofficial + free more often than it looks.
2. **What is the ToS posture of the access path we'd actually use?** Not "is the data public" — *is the method of access sanctioned, tolerated, or prohibited?*
3. **What is the realistic block/change risk?** Per-IP throttling, data-center IP hostility, policy-change velocity, anti-bot tooling.
4. **What is the fallback when (not if) it degrades?** Cache-and-serve-stale, alternate egress, alternate source, or graceful feature absence.

---

## Tier definitions

| Tier | Meaning | Required treatment |
|---|---|---|
| **1 — Official & free** | Documented API, sanctioned access, free at project volume | Build directly. Respect published rate limits; a plain retry/backoff is enough. |
| **2 — Public but unofficial** | Publicly reachable, but the access path is informal, undocumented, or merely tolerated | **Mandatory adapter treatment**: swappable interface + rate limiter + Supabase cache + graceful degradation (serve last-known-good on block). The RedditSource adapter is the reference implementation. |
| **3 — Deferred (paid or gated)** | Legitimate access exists but costs money or approval the MVP doesn't justify | Not built. Documented cost math + explicit revisit criteria, so the decision is a decision and not a fudge. |
| **4 — Excluded** | The only practical access paths violate the provider's terms, or the risk/effort dwarfs the signal | Not built, not "later." An official-path alternative is named for the same signal wherever one exists. |

---

## The register

| Source | Provides | Access path | Tier | Risk & posture | Mitigation / fallback |
|---|---|---|---|---|---|
| Steam Web API (`ISteamUserStats`, `ISteamNews`) | Concurrent players; per-title patch notes & announcements | Official Steamworks Web API, free key | **1** | Low. Stable, documented. CCU endpoint returns *current* value only — no history. | Weekly snapshots build our own history; standard backoff. |
| IGDB | Catalog, release calendar, hype/follows | Official API (free via Twitch developer OAuth) | **1** | Low. Rate limits generous at our volume. | Backoff; RAWG overlaps much of the catalog. |
| RAWG | Supplementary catalog/ratings | Official API, free key for personal projects | **1** | Low–medium (smaller provider; free-key terms require attribution). | IGDB covers most of the same ground if RAWG changes terms. |
| SEC EDGAR | Filings (10-K/10-Q/8-K), full-text search | Official, free; declared User-Agent required; ~10 req/s ceiling | **1** | Very low. Government service. | Trivial pacing. |
| YouTube Data API v3 | Video comments on patch/review/dev-update videos | Official; free 10,000 units/day quota | **1** | Low, **but quota-shaped**: `commentThreads.list` = 1 unit per ~100 comments (cheap); `search.list` = 100 units *and* sits in its own tightly capped bucket (per the 2026 quota changes) — so discovery must not rely on search. Comments are disabled on a meaningful share of videos. No self-service quota increase (manual audit only). | Discover videos via tracked channels' upload playlists (`playlistItems.list`, 1 unit per 50 videos); treat `commentsDisabled` as a normal miss; cache comment pulls in `api_cache`. |
| Greenhouse / Lever / Ashby job boards | Studio job postings (hiring-signal source) | **Official public job-board APIs** — documented, no auth for reads (`boards-api.greenhouse.io`, `api.lever.co/v0/postings`, Ashby posting API) | **1** | Low. These endpoints exist precisely to be read by third parties. | Cover studios not on a hosted ATS via their own careers pages (below). |
| Alpaca (paper trading + market data) | Order execution (paper), positions, prices, SPY benchmark bars | Official API + official MCP server; paper trading free; free data plan (~200 req/min, IEX feed) | **1** | Low. Developer-first brokerage; paper/live differ only by endpoint + key. Some full-market real-time data needs a paid subscription — irrelevant at weekly cadence. | None needed beyond backoff. |
| Bluesky | Social-text sentiment (developer accounts, game hashtags/feeds) | Official, open AT Protocol API — free account gives authenticated `searchPosts`; public read endpoints exist; generous per-IP rate limits; no approval process | **1 — optional add** | Low access risk (the protocol is *designed* to be read). The real limit is coverage: the games conversation on Bluesky is smaller than X's, so signal density per tracked title varies. | Designated **free alternative to X**: if sentiment coverage needs a social-text source beyond Reddit + YouTube, add Bluesky behind the shared adapter interface *before* paying X's per-read pricing. |
| Steam `appreviews` endpoint | Review text + helpfulness (sentiment input) | Public JSON endpoint; informally supported (powers Steam's own pages) but not part of the documented Web API | **2** | Medium-low. Long-stable, but undocumented endpoints can change shape or gain throttling without notice. | Adapter treatment: same interface + cache + stale-fallback pattern as Reddit; review *scores* also arrive via Tier-1 paths. |
| Reddit `.json` endpoints | Community sentiment posts/comments | Public read-only `.json`; official Data API closed to self-service since Nov 2025 | **2** | **The reference case.** ~10 req/min per-IP unauthenticated ceiling; data-center (GitHub Actions) IPs throttled first; Reddit has tightened unauthenticated access repeatedly since 2023 and its terms govern automated access regardless of endpoint. | Full adapter: pacing + jitter, Supabase cache with fresh/stale semantics, `RedditBlocked` → serve last-known-good, fallback-chain slot for an alternate egress. See the *RedditSource Adapter* doc. |
| yfinance (Yahoo Finance) | P/E, earnings dates, analyst ratings, short interest | Unofficial Python wrapper around Yahoo's private endpoints | **2** | Medium. Yahoo throttles and reshapes these endpoints periodically; the library plays cat-and-mouse. It is *not* more official than Reddit's `.json` route — treat it with the same respect. | Adapter treatment + cache. Prices and the SPY benchmark come from Alpaca's official data API anyway; on a Yahoo block, only the fundamentals-adjacent fields degrade to last-known-good. |
| Studio careers pages | Postings for studios not on a hosted ATS | Playwright, low-volume, robots-respecting | **2** | Medium. HTML changes break parsers; volume must stay polite. | Per-studio parsers behind the shared adapter interface; a broken parser degrades that one studio, not the run. |
| X / Twitter | Developer/journalist posts, hashtag sentiment | Official API only | **3 — deferred** | As of **Feb 2026**: the free tier is discontinued and Basic/Pro are closed to new signups; new developers get **pay-per-use** (~$0.005 per post read, 24h dedup, 2M reads/mo cap). A useful read volume for this project (~1–2K posts/week) is **~$20–40/month** — modest, but not free, and X's developer terms have repriced repeatedly. Scraping is ToS-prohibited and aggressively defended; **Nitter is not a fallback** (public instances died with guest accounts in early 2024). | **Revisit criteria:** after Phase 4, if per-title sentiment coverage from Reddit + YouTube + Steam reviews is demonstrably too thin for Tier-A titles — try Bluesky (free, above) first; if the coverage gap persists, add X via pay-per-use with a hard monthly spend cap, behind the same adapter interface. Until then, absent — not faked. |
| LinkedIn | Job postings, org-chart signals | No public read API for this use; scraping prohibited by ToS, defended by aggressive anti-bot tooling and litigation history | **4 — excluded** | High legal/ToS + high breakage. | The hosted-ATS APIs (Tier 1) plus careers pages cover the same hiring signal at zero risk. |
| Discord announcement channels | Patch/dev announcements | Bot access requires per-server admin installation; automating a user account ("self-bot") is explicitly prohibited | **4 — excluded** | ToS-prohibited for the access we'd need. | `ISteamNews` (Tier 1) mirrors the same announcements for nearly every tracked title; dev blogs via web fetch cover the rest. |
| Steam community discussion forums | Forum threads (sentiment) | No API; scraping-only | **4 — excluded (for now)** | Scraping-only path for marginal signal already covered by reviews + Reddit. | Steam `appreviews` + Reddit carry the community-mood signal; revisit only if a specific tracked title's community lives primarily on Steam forums. |

---

## Standing rules

1. **Tier 2 means the adapter pattern, always.** Interface + rate limiter + `api_cache` + graceful degradation — the RedditSource design is the template, and `SupabaseRedditCache` is deliberately source-agnostic so each new adapter reuses it (`source="yfinance"`, `source="steam_reviews"`, …).
2. **Tier 3 entries carry cost math and revisit criteria**, so "deferred" stays a scoping decision rather than quiet scope creep or, worse, a pretended integration.
3. **Tier 4 entries name their official-path substitute.** Exclusion is only credible when the signal is still covered.
4. **Re-check this register when a phase completes or a provider announces policy changes.** Reddit (2023, 2025) and X (2023, 2024, Feb 2026) both demonstrate that access terms move faster than project timelines; the register is where that motion gets absorbed.
5. **A sustained block on any Tier-2 source is a monitored health signal**, not a silent failure — surfaced in observability the same way the adapter doc specifies for `RedditBlocked`.
