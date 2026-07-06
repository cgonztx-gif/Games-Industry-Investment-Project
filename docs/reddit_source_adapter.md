# RedditSource Adapter — Design

Isolates Reddit data access behind a single swappable interface so the Sentiment
Subagent depends on an **abstraction**, not on Reddit's `.json` endpoints, a proxy,
or any one access method. When Reddit changes its rules (as it did in Nov 2025) or
blocks an egress IP, the blast radius is this one file — nothing downstream changes.

## Why this exists

- The official Data API is **not an option** here (self-service key creation ended
  with the Nov 2025 Responsible Builder Policy; new OAuth access requires manual
  approval we're not pursuing).
- The unauthenticated **public `.json` endpoints** remain available and are
  **read-only**, which is exactly what sentiment analysis needs.
- Two hard constraints shape the design: unauthenticated access is throttled to
  roughly **10 requests/minute, tracked per IP**, and **data-center IPs** (which is
  what GitHub Actions runners are) are a prime target for blocking. Reddit has also
  been steadily tightening unauthenticated access since 2023, so the design assumes
  this path can degrade or close without notice. The adapter is therefore built to
  (a) stay well under the rate ceiling, (b) cache aggressively, and
  (c) **degrade gracefully** rather than crash the weekly run when blocked.
- This is the **reference implementation of the Tier-2 source pattern** in the
  project's *Data Source Risk Register* — every unofficial source (yfinance, the
  Steam `appreviews` endpoint) gets the same interface + rate-limit + cache +
  degrade treatment.

## Architecture at a glance

```
Sentiment Subagent
      │  depends only on the RedditSource interface
      ▼
FirstAvailableRedditSource          ← tries sources in order, falls through on block
      ├── CachedRedditSource(JsonRedditSource)     ← primary: free .json + cache
      └── CachedRedditSource(AltEgressRedditSource) ← optional fallback: proxy / paid scraper
              (each wraps a RateLimiter + retry/backoff + RedditBlocked signaling)
```

The composition is the point: every layer implements the same `RedditSource`
interface, so caching, fallback, and egress strategy are all just decorators you
stack. Swapping the free path for a paid scraping API later is a one-line change in
the factory, not a refactor of the agent.

---

## 1. Domain types

Return typed records, never raw JSON dicts. This is what decouples downstream code
from Reddit's response shape.

```python
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class RedditPost:
    id: str
    subreddit: str
    title: str
    selftext: str
    author: str
    score: int
    num_comments: int
    created_utc: float
    permalink: str
    url: str

@dataclass(frozen=True)
class RedditComment:
    id: str
    post_id: str
    body: str
    author: str
    score: int
    created_utc: float
```

## 2. The interface

Everything in the chain conforms to this. The agent is typed against `RedditSource`
and never imports a concrete class.

```python
from typing import Protocol

class RedditSource(Protocol):
    def fetch_posts(
        self, subreddit: str, sort: str = "top",
        timeframe: str = "week", limit: int = 100,
    ) -> list[RedditPost]: ...

    def fetch_comments(
        self, post_id: str, subreddit: str, limit: int = 200,
    ) -> list[RedditComment]: ...


class RedditBlocked(Exception):
    """Reddit throttled or blocked us (429/403/451, or retries exhausted).
    Signals callers to degrade gracefully instead of failing the run."""
```

## 3. Rate limiter

A minimum-interval limiter with jitter — deliberately *not* a burst token bucket.
Sustained, evenly-spaced, slightly-randomized requests stay under the ~10/min ceiling
and look less robotic than bursts. Default ~6–7 req/min effective.

```python
import random, time

class RateLimiter:
    def __init__(self, min_interval: float = 8.0, jitter: float = 2.0):
        self.min_interval = min_interval
        self.jitter = jitter
        self._last = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last
        delay = self.min_interval + random.uniform(0, self.jitter)
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last = time.monotonic()
```

## 4. The `.json` source (primary)

```python
import logging, requests
logger = logging.getLogger("reddit_source")

# Reddit throttles generic User-Agents (e.g. python-requests) hard.
# Use their required descriptive format: <platform>:<app-id>:<version> (by /u/<user>)
USER_AGENT = "github-actions:games-intel-platform:v0.1 (by /u/your_username)"

class JsonRedditSource:
    BASE = "https://www.reddit.com"

    def __init__(self, user_agent: str = USER_AGENT,
                 limiter: RateLimiter | None = None,
                 max_retries: int = 3,
                 session: requests.Session | None = None):
        self.limiter = limiter or RateLimiter()
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent})

    def _get(self, path: str, params: dict | None = None):
        url = f"{self.BASE}{path}"
        for attempt in range(1, self.max_retries + 1):
            self.limiter.wait()
            resp = self.session.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (429, 503):
                backoff = float(resp.headers.get("Retry-After", 2 ** attempt))
                logger.warning("throttled %s; backoff %ss (attempt %d)",
                               resp.status_code, backoff, attempt)
                time.sleep(backoff)
                continue
            if resp.status_code in (403, 451):
                raise RedditBlocked(f"{resp.status_code} for {url}")
            resp.raise_for_status()
        raise RedditBlocked(f"retries exhausted for {url}")

    def fetch_posts(self, subreddit, sort="top", timeframe="week", limit=100):
        data = self._get(f"/r/{subreddit}/{sort}.json",
                         params={"t": timeframe, "limit": min(limit, 100)})
        out = []
        for child in data.get("data", {}).get("children", []):
            d = child.get("data", {})
            if d.get("stickied"):       # skip mod-pinned megathreads (tune as needed)
                continue
            out.append(RedditPost(
                id=d["id"], subreddit=subreddit,
                title=d.get("title", ""), selftext=d.get("selftext", ""),
                author=d.get("author", "[deleted]"),
                score=d.get("score", 0), num_comments=d.get("num_comments", 0),
                created_utc=d.get("created_utc", 0.0),
                permalink=d.get("permalink", ""), url=d.get("url", ""),
            ))
        return out

    def fetch_comments(self, post_id, subreddit, limit=200):
        # comments endpoint returns [post_listing, comments_listing]
        data = self._get(f"/r/{subreddit}/comments/{post_id}.json",
                         params={"limit": limit})
        if not isinstance(data, list) or len(data) < 2:
            return []
        out: list[RedditComment] = []
        self._walk(data[1].get("data", {}).get("children", []), post_id, out)
        return out

    def _walk(self, children, post_id, out):
        for c in children:
            if c.get("kind") != "t1":   # t1 = comment; "more" nodes are skipped (see notes)
                continue
            d = c.get("data", {})
            out.append(RedditComment(
                id=d.get("id", ""), post_id=post_id,
                body=d.get("body", ""), author=d.get("author", "[deleted]"),
                score=d.get("score", 0), created_utc=d.get("created_utc", 0.0),
            ))
            replies = d.get("replies")
            if isinstance(replies, dict):
                self._walk(replies.get("data", {}).get("children", []), post_id, out)
```

## 5. Caching + graceful degradation

Wraps any source. On a normal run it prevents re-fetching; on a block it serves
last-known-good rather than returning empty. This is where the resilience lives.

**Serialization boundary (canonical — matches the SupabaseRedditCache doc §4):**
the cache stores JSON-native `list[dict]` payloads and knows nothing about the
dataclasses; *this wrapper* owns the dataclass ↔ dict conversion on both sides.
That keeps the cache generic (reusable for yfinance, Steam reviews, or any other
adapter) and keeps everything downstream of this wrapper typed.

```python
from dataclasses import asdict
from typing import Protocol

class RedditCache(Protocol):
    # backed by the generic api_cache table — see the SupabaseRedditCache design doc
    def get(self, key: str, max_age_hours: float | None = None) -> list | None: ...
    def set(self, key: str, value: list) -> None: ...

class CachedRedditSource:
    def __init__(self, inner: "RedditSource", cache: RedditCache, ttl_hours: int = 24):
        self.inner, self.cache, self.ttl_hours = inner, cache, ttl_hours

    def fetch_posts(self, subreddit, sort="top", timeframe="week", limit=100):
        key = f"posts:{subreddit}:{sort}:{timeframe}"
        fresh = self.cache.get(key, max_age_hours=self.ttl_hours)
        if fresh is not None:
            return [RedditPost(**d) for d in fresh]            # dict -> dataclass
        try:
            posts = self.inner.fetch_posts(subreddit, sort, timeframe, limit)
            self.cache.set(key, [asdict(p) for p in posts])    # dataclass -> dict
            return posts
        except RedditBlocked:
            stale = self.cache.get(key)            # no TTL: stale > empty
            if stale is not None:
                logger.warning("blocked; serving stale cache for r/%s", subreddit)
                return [RedditPost(**d) for d in stale]
            raise

    def fetch_comments(self, post_id, subreddit, limit=200):
        key = f"comments:{post_id}"
        fresh = self.cache.get(key, max_age_hours=self.ttl_hours)
        if fresh is not None:
            return [RedditComment(**d) for d in fresh]
        try:
            cs = self.inner.fetch_comments(post_id, subreddit, limit)
            self.cache.set(key, [asdict(c) for c in cs])
            return cs
        except RedditBlocked:
            stale = self.cache.get(key)
            if stale is not None:
                logger.warning("blocked; serving stale comments for %s", post_id)
                return [RedditComment(**d) for d in stale]
            raise
```

`RedditPost(**d)` works because the dataclasses are flat and the dict keys match
the field names — keep them flat for exactly this reason. If a nested field is
ever added, switch to a small `from_dict` classmethod.

## 6. Fallback chain + factory (as actually built, 2026-07-06)

The "fallback" — given the official API is off the table — is an **alternate egress**,
not a different API. This is no longer hypothetical: `agents/workers/sentiment/reddit_source.py`
implements it as an env-var-gated `FirstAvailableRedditSource` chain with two real
alternate-egress leaves, `ProxiedJsonRedditSource` and `OAuthRedditSource`, both of
which reuse `JsonRedditSource`'s parsing logic via shared module-level functions
(`_parse_post_listing`, `_walk_comments`, `_parse_comment_listing`, `_parse_subreddit_search`)
rather than duplicating it.

```python
class ProxiedJsonRedditSource(JsonRedditSource):
    """Same as JsonRedditSource, routed through a standard HTTP/HTTPS proxy.
    No SOCKS5 -- requests.Session supports http(s) proxies natively, zero new deps."""
    def __init__(self, proxy_url: str, user_agent=_USER_AGENT, limiter=None,
                 max_retries=3, session=None):
        session = session or requests.Session()
        session.proxies.update({"http": proxy_url, "https": proxy_url})
        super().__init__(user_agent=user_agent, limiter=limiter,
                          max_retries=max_retries, session=session)


class OAuthRedditSource:
    """Reddit's OAuth2 refresh-token flow via plain `requests` -- no `praw`, matching
    this repo's no-SDK convention (alpaca_trading_client.py, email_delivery.py).
    Requires REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET / REDDIT_REFRESH_TOKEN. Talks to
    oauth.reddit.com (note: no .json suffix -- OAuth responses are JSON natively) with
    a Bearer token cached in memory for its expires_in duration. Own RateLimiter(0.8, 0.2)
    -- OAuth's rate ceiling (~100 req/min) is far looser than unauthenticated (~10/min)."""
    # fetch_posts / fetch_comments / resolve_subreddit call the same module-level
    # parsers as JsonRedditSource -- see reddit_source.py for the full implementation.


def _build_leaf_sources() -> list["RedditSource"]:
    """Priority: OAuth (all 3 vars required together) -> Proxy -> unauthenticated
    JsonRedditSource (always last, unconditional -- the sole active path when no
    new env vars are set)."""
    leaves = []
    if os.environ.get("REDDIT_CLIENT_ID") and os.environ.get("REDDIT_CLIENT_SECRET") and os.environ.get("REDDIT_REFRESH_TOKEN"):
        leaves.append(OAuthRedditSource(...))
    if os.environ.get("REDDIT_PROXY_URL"):
        leaves.append(ProxiedJsonRedditSource(...))
    leaves.append(JsonRedditSource())
    return leaves


def build_reddit_source(cache_factory: "Callable[[str], RedditCache]") -> "RedditSource":
    """Each leaf gets its own api_cache namespace (reddit_oauth / reddit_proxy / reddit)
    so one path's stale-serve can never mask another path's real health. Returns a bare
    CachedRedditSource when only JsonRedditSource is active -- the exact pre-existing
    object graph, unchanged -- else a FirstAvailableRedditSource of per-leaf wraps."""
    leaves = _build_leaf_sources()
    wrapped = [CachedRedditSource(leaf, cache_factory(_CACHE_NAMESPACE_BY_TYPE[type(leaf)]))
               for leaf in leaves]
    return wrapped[0] if len(wrapped) == 1 else FirstAvailableRedditSource(wrapped)


def build_subreddit_resolver() -> "SubredditResolver":
    """Same priority chain, no cache wrap -- subreddit-name resolution results are
    already cached separately by cached_resolve_subreddit()/lookup_cache in worker.py.
    Fixes a real prior bug: worker.py used to instantiate a hardcoded JsonRedditSource()
    for resolution, bypassing whatever fallback chain was configured."""
    leaves = _build_leaf_sources()
    return leaves[0] if len(leaves) == 1 else FirstAvailableRedditSource(leaves)
```

All four new env vars (`REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_REFRESH_TOKEN`,
`REDDIT_PROXY_URL`) are optional and gate this behavior; with none set, `build_reddit_source`
returns a bare `CachedRedditSource(JsonRedditSource(), cache)` — bit-for-bit identical to
the object graph before this change. `resolve_subreddit` also now flows through
`build_subreddit_resolver()` rather than a hardcoded, ungated `JsonRedditSource()`.

## 7. Integration with the Sentiment Subagent (CrewAI at MVP)

Expose the adapter as a thin tool; the agent never sees Reddit specifics. Note the
cache is constructed with its Supabase credentials from the environment — it has
required arguments (see the SupabaseRedditCache doc §3), so `SupabaseRedditCache()`
with no args would fail.

```python
import json, os
from crewai.tools import tool

_cache = SupabaseRedditCache(
    url=os.environ["SUPABASE_URL"],
    service_key=os.environ["SUPABASE_SERVICE_KEY"],   # GitHub Actions secret
    source="reddit",
)
_source = build_reddit_source(cache=_cache)

@tool("fetch_reddit_discussion")
def fetch_reddit_discussion(subreddit: str, top_n_posts: int = 10) -> str:
    """Fetch recent top posts + comments from a subreddit for sentiment analysis."""
    posts = _source.fetch_posts(subreddit, sort="top", timeframe="week", limit=top_n_posts)
    blob = []
    for p in posts:
        comments = _source.fetch_comments(p.id, subreddit, limit=100)
        blob.append({"title": p.title, "body": p.selftext, "score": p.score,
                     "comments": [c.body for c in comments]})
    return json.dumps(blob)   # feeds the VADER baseline + Claude ABSA pass
```

After the Claude Agent SDK migration, the same function registers as an SDK custom
tool — the decorator changes; the adapter, cache, and everything above this section
do not. That containment is the point of the design.

---

## Operational notes

**Request budget — tiered, not flat.** At ~8s/request + jitter you get ~6–7 req/min,
safely under the ~10/min ceiling. The watchlist maps ~150–300 games to roughly ~60
active subreddits, but not every sub earns comment fetches. Each game carries a
**sentiment tier** assigned at seeding (see the brief): Tier A (~25 subs — highest
portfolio materiality and activity) gets 1 listing call + comments for the top ~10
posts (~11 calls/sub); the tail gets a listing call only. That's ~25×11 + ~35×1 ≈
**310 calls ≈ 45 minutes** per weekly run — with a worst case of ~660 calls ≈ ~90
minutes if every sub were promoted to Tier A. Either figure fits comfortably inside
GitHub Actions' 6-hour job limit; and because the repo is public (per the brief),
Actions minutes are unmetered — on a private repo this one step would consume
10–25% of the free plan's 2,000 monthly minutes. **r/gaming is deliberately
excluded**: it's a firehose of memes and cross-game noise whose volume would eat
the budget while diluting per-title signal; game-specific subs carry the alpha.
Tune `top_n_posts` and tier assignments to control cost.

**The data-center IP risk is the real one.** GitHub Actions runners are data-center IPs,
exactly what Reddit throttles first. Mitigations, in order of effort: (1) keep volume
low and well-paced (done above); (2) lean on the cache so a partial block still yields
a usable run; (3) if blocks become routine, move *only the Reddit collection step* to an
egress with a cleaner IP (a small always-on box, or a managed scraping API as the
`AltEgressRedditSource`) — the rest of the pipeline stays on Actions. Treat a sustained
`RedditBlocked` rate as a monitored health signal, not a silent failure.

> **Confirmed, not hypothetical — 2026-07-06.** A live diagnostic run showed a static
> WAF `403` ("You've been blocked by network security") on every endpoint tried
> (`/r/*/top.json`, `/subreddits/search.json`), on every run, since inception — zero
> successful Reddit rows have ever been written to `sentiment_snapshots`. This is the
> IP-reputation block this section already anticipated, now confirmed sustained and
> 100%-observed rather than intermittent throttling. Mitigation tier 3 (alternate
> egress) is now implemented — see §6 above — as `ProxiedJsonRedditSource` and
> `OAuthRedditSource`, both gated behind optional env vars and opt-in (not automatically
> active until the user supplies real credentials/a real proxy).

**Comment "more" nodes.** Deep/collapsed threads return `kind: "more"` placeholders
that the code skips. For sentiment, top-level + first-level replies are almost always
enough signal; only add the `morechildren` expansion if you find you're missing volume.

**Deleted content.** `[deleted]`/`[removed]` bodies and authors appear normally; filter
them before scoring so they don't drag the sentiment baseline.

**Testing.** `tests/test_reddit_source.py` now exists — hand-built fixture dicts plus a
local fake `requests.Session` (records calls, pops pre-scripted responses/exceptions in
order), not recorded cassettes as originally sketched here. Covers `JsonRedditSource`
(200/403/451/429-backoff/retries-exhausted/nested comments/malformed responses/subreddit
match threshold), `CachedRedditSource` (fresh hit, miss, blocked+stale, blocked+empty),
`FirstAvailableRedditSource`, `ProxiedJsonRedditSource`, `OAuthRedditSource` (token
lifecycle, 401/403/429 handling, no-`.json`-suffix + Bearer header), and the
`build_reddit_source`/`build_subreddit_resolver`/`_build_leaf_sources` factory —
including the load-bearing proof that with all four new env vars unset, the returned
object graph is unchanged. Zero live network calls anywhere in the suite. Note: OAuth
registration itself is a separate, manual, non-guaranteed Reddit approval process (no
SLA as of the Nov 2025 policy) — the adapter code is *ready* to use OAuth the moment
credentials exist, but is not *activated*, and this test suite cannot and does not
verify a real token exchange or a real proxied request reaching Reddit.

**Honest caveat.** The `.json` route is unofficial, and Reddit's Data API terms govern
automated access regardless of endpoint; their wiki is explicit that non-OAuth traffic
may be throttled or blocked at will. For a non-commercial personal project this is low
stakes, but keep volume modest and respectful, and don't present it as a sanctioned
integration.
