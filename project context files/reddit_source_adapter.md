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
  what GitHub Actions runners are) are a prime target for blocking. So the adapter
  is built to (a) stay well under the rate ceiling, (b) cache aggressively, and
  (c) **degrade gracefully** rather than crash the weekly run when blocked.

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

```python
from typing import Protocol

class RedditCache(Protocol):
    # back this with a Supabase table: key TEXT PK, payload JSONB, fetched_at TIMESTAMPTZ
    def get(self, key: str, max_age_hours: float | None = None) -> list | None: ...
    def set(self, key: str, value: list) -> None: ...

class CachedRedditSource:
    def __init__(self, inner: "RedditSource", cache: RedditCache, ttl_hours: int = 24):
        self.inner, self.cache, self.ttl_hours = inner, cache, ttl_hours

    def fetch_posts(self, subreddit, sort="top", timeframe="week", limit=100):
        key = f"posts:{subreddit}:{sort}:{timeframe}"
        fresh = self.cache.get(key, max_age_hours=self.ttl_hours)
        if fresh is not None:
            return fresh
        try:
            posts = self.inner.fetch_posts(subreddit, sort, timeframe, limit)
            self.cache.set(key, posts)
            return posts
        except RedditBlocked:
            stale = self.cache.get(key)            # ignore TTL: stale > empty
            if stale is not None:
                logger.warning("blocked; serving stale cache for r/%s", subreddit)
                return stale
            raise

    def fetch_comments(self, post_id, subreddit, limit=200):
        key = f"comments:{post_id}"
        fresh = self.cache.get(key, max_age_hours=self.ttl_hours)
        if fresh is not None:
            return fresh
        try:
            cs = self.inner.fetch_comments(post_id, subreddit, limit)
            self.cache.set(key, cs)
            return cs
        except RedditBlocked:
            stale = self.cache.get(key)
            if stale is not None:
                return stale
            raise
```

## 6. Fallback chain + factory

The "fallback" — given the official API is off the table — is an **alternate egress**,
not a different API. Anything that can produce `RedditPost`/`RedditComment` objects
(a residential-proxy `requests.Session`, or a managed scraping-API client) implements
the same interface and slots in behind the free path.

```python
class FirstAvailableRedditSource:
    """Try each source in order; fall through to the next on RedditBlocked."""
    def __init__(self, sources: list["RedditSource"]):
        self.sources = sources

    def fetch_posts(self, *a, **k):
        last = None
        for src in self.sources:
            try:
                return src.fetch_posts(*a, **k)
            except RedditBlocked as e:
                last = e
        raise last or RedditBlocked("no source available")

    def fetch_comments(self, *a, **k):
        last = None
        for src in self.sources:
            try:
                return src.fetch_comments(*a, **k)
            except RedditBlocked as e:
                last = e
        raise last or RedditBlocked("no source available")


def build_reddit_source(cache: RedditCache) -> "RedditSource":
    primary = CachedRedditSource(JsonRedditSource(), cache)
    # MVP: free path only. Add a proxied/paid source here later — zero downstream change:
    #   alt = CachedRedditSource(AltEgressRedditSource(session=proxied_session), cache)
    #   return FirstAvailableRedditSource([primary, alt])
    return primary
```

An `AltEgressRedditSource` can often subclass `JsonRedditSource` and only override the
`session` (e.g. one routed through a residential proxy) — the parsing logic is shared.

## 7. Integration with the Sentiment Subagent (CrewAI)

Expose the adapter as a thin tool; the agent never sees Reddit specifics.

```python
from crewai.tools import tool

_source = build_reddit_source(cache=SupabaseRedditCache())

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

---

## Operational notes

**Request budget.** At ~8s/request + jitter you get ~6–7 req/min, safely under the
~10/min ceiling. If you map games to ~60 active subreddits and pull 1 listing call +
comments for the top ~10 posts each (~11 calls/sub), that's ~660 calls ≈ ~90 minutes.
Fine for a weekly GitHub Actions job (well within job time limits and free-tier
minutes). Tune `top_n_posts` and which subs get comment fetches to control cost.

**The data-center IP risk is the real one.** GitHub Actions runners are data-center IPs,
exactly what Reddit throttles first. Mitigations, in order of effort: (1) keep volume
low and well-paced (done above); (2) lean on the cache so a partial block still yields
a usable run; (3) if blocks become routine, move *only the Reddit collection step* to an
egress with a cleaner IP (a small always-on box, or a managed scraping API as the
`AltEgressRedditSource`) — the rest of the pipeline stays on Actions. Treat a sustained
`RedditBlocked` rate as a monitored health signal, not a silent failure.

**Comment "more" nodes.** Deep/collapsed threads return `kind: "more"` placeholders
that the code skips. For sentiment, top-level + first-level replies are almost always
enough signal; only add the `morechildren` expansion if you find you're missing volume.

**Deleted content.** `[deleted]`/`[removed]` bodies and authors appear normally; filter
them before scoring so they don't drag the sentiment baseline.

**Testing.** Record real `.json` responses to fixtures once, then replay them in unit
tests (`responses` or a stubbed session). CI then never touches Reddit, tests are
deterministic, and you can assert parsing against known payloads including edge cases
(deleted comments, empty subs, a 429).

**Honest caveat.** The `.json` route is unofficial, and Reddit's Data API terms govern
automated access regardless of endpoint; their wiki is explicit that non-OAuth traffic
may be throttled or blocked at will. For a non-commercial personal project this is low
stakes, but keep volume modest and respectful, and don't present it as a sanctioned
integration.
