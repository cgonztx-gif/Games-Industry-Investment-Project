"""
Unauthenticated Reddit adapter.

Hits public read-only .json endpoints — no OAuth credentials required.
Rate-limited to ~6-7 req/min to stay under Reddit's ~10/min unauthenticated ceiling.
Caching and graceful degradation are handled by CachedRedditSource + RedditCache.

Architecture:
    worker.py
        └── CachedRedditSource(JsonRedditSource(), post_cache)
                └── on RedditBlocked: serve stale cache, or re-raise if cache empty
        └── cached_resolve_subreddit(title, JsonRedditSource(), lookup_cache)
                └── caches (source, key) → [subreddit_name] for 30 days
"""

from __future__ import annotations

import difflib
import logging
import random
import re
import time
from dataclasses import asdict, dataclass
from typing import Protocol

import requests

from agents.workers.sentiment.reddit_cache import RedditCache

logger = logging.getLogger("reddit_source")

_USER_AGENT = "github-actions:games-intel-platform:v0.1 (by /u/cgonztx)"
_BASE = "https://www.reddit.com"


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------

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


class RedditBlocked(Exception):
    """Reddit throttled or blocked us (429/403/451, or retries exhausted).
    Signals callers to degrade gracefully instead of failing the run."""


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

class RedditSource(Protocol):
    def fetch_posts(
        self,
        subreddit: str,
        sort: str = "top",
        timeframe: str = "week",
        limit: int = 100,
    ) -> list[RedditPost]: ...

    def fetch_comments(
        self,
        post_id: str,
        subreddit: str,
        limit: int = 200,
    ) -> list[RedditComment]: ...


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

class RateLimiter:
    """Minimum-interval limiter with jitter. Evenly spaced, slightly randomized
    requests are less likely to trigger Reddit's per-IP throttle than bursts."""

    def __init__(self, min_interval: float = 8.0, jitter: float = 2.0) -> None:
        self.min_interval = min_interval
        self.jitter = jitter
        self._last: float = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last
        delay = self.min_interval + random.uniform(0, self.jitter)
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last = time.monotonic()


# ---------------------------------------------------------------------------
# Normalization helper (shared by fetch and resolve)
# ---------------------------------------------------------------------------

def _normalize(title: str) -> str:
    title = title.lower()
    title = re.sub(r"[^\w\s]", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    for article in ("the ", "a ", "an "):
        if title.startswith(article):
            title = title[len(article):]
    return title


# ---------------------------------------------------------------------------
# Primary source: unauthenticated .json endpoints
# ---------------------------------------------------------------------------

class JsonRedditSource:
    def __init__(
        self,
        user_agent: str = _USER_AGENT,
        limiter: RateLimiter | None = None,
        max_retries: int = 3,
        session: requests.Session | None = None,
    ) -> None:
        self.limiter = limiter or RateLimiter()
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent})

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{_BASE}{path}"
        for attempt in range(1, self.max_retries + 1):
            self.limiter.wait()
            resp = self.session.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (429, 503):
                backoff = float(resp.headers.get("Retry-After", 2 ** attempt))
                logger.warning(
                    "throttled %s; backoff %.0fs (attempt %d/%d)",
                    resp.status_code, backoff, attempt, self.max_retries,
                )
                time.sleep(backoff)
                continue
            if resp.status_code in (403, 451):
                raise RedditBlocked(f"{resp.status_code} on {url}")
            resp.raise_for_status()
        raise RedditBlocked(f"retries exhausted for {url}")

    def fetch_posts(
        self,
        subreddit: str,
        sort: str = "top",
        timeframe: str = "week",
        limit: int = 100,
    ) -> list[RedditPost]:
        data = self._get(
            f"/r/{subreddit}/{sort}.json",
            params={"t": timeframe, "limit": min(limit, 100)},
        )
        out: list[RedditPost] = []
        for child in data.get("data", {}).get("children", []):
            d = child.get("data", {})
            if d.get("stickied"):
                continue
            out.append(RedditPost(
                id=d.get("id", ""),
                subreddit=subreddit,
                title=d.get("title", ""),
                selftext=d.get("selftext", ""),
                author=d.get("author", "[deleted]"),
                score=d.get("score", 0),
                num_comments=d.get("num_comments", 0),
                created_utc=d.get("created_utc", 0.0),
                permalink=d.get("permalink", ""),
                url=d.get("url", ""),
            ))
        return out

    def fetch_comments(
        self,
        post_id: str,
        subreddit: str,
        limit: int = 200,
    ) -> list[RedditComment]:
        data = self._get(
            f"/r/{subreddit}/comments/{post_id}.json",
            params={"limit": limit},
        )
        if not isinstance(data, list) or len(data) < 2:
            return []
        out: list[RedditComment] = []
        self._walk(data[1].get("data", {}).get("children", []), post_id, out)
        return out

    def _walk(
        self,
        children: list,
        post_id: str,
        out: list[RedditComment],
    ) -> None:
        for c in children:
            if c.get("kind") != "t1":
                continue
            d = c.get("data", {})
            out.append(RedditComment(
                id=d.get("id", ""),
                post_id=post_id,
                body=d.get("body", ""),
                author=d.get("author", "[deleted]"),
                score=d.get("score", 0),
                created_utc=d.get("created_utc", 0.0),
            ))
            replies = d.get("replies")
            if isinstance(replies, dict):
                self._walk(
                    replies.get("data", {}).get("children", []),
                    post_id,
                    out,
                )

    def resolve_subreddit(self, game_title: str) -> str | None:
        """Search Reddit for the best-matching subreddit (similarity >= 0.70)."""
        try:
            data = self._get(
                "/subreddits/search.json",
                params={"q": game_title, "limit": 5},
            )
        except RedditBlocked:
            return None

        normalized = _normalize(game_title)
        best_name: str | None = None
        best_ratio = 0.0

        for child in data.get("data", {}).get("children", []):
            d = child.get("data", {})
            candidate = _normalize(d.get("display_name", ""))
            ratio = difflib.SequenceMatcher(None, normalized, candidate).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_name = d.get("display_name")

        return best_name if best_ratio >= 0.70 else None


# ---------------------------------------------------------------------------
# Caching wrapper
# ---------------------------------------------------------------------------

class CachedRedditSource:
    """
    Wraps any RedditSource with a cache. On a block, serves stale data rather
    than propagating RedditBlocked (unless the cache is also empty).

    Owns the RedditPost <-> dict serialization boundary so the cache stays
    source-agnostic (stores plain JSON dicts, not dataclasses).
    """

    def __init__(
        self,
        inner: RedditSource,
        cache: RedditCache,
        ttl_hours: int = 24,
    ) -> None:
        self.inner = inner
        self.cache = cache
        self.ttl_hours = ttl_hours

    def fetch_posts(
        self,
        subreddit: str,
        sort: str = "top",
        timeframe: str = "week",
        limit: int = 100,
    ) -> list[RedditPost]:
        key = f"posts:{subreddit}:{sort}:{timeframe}"
        fresh = self.cache.get(key, max_age_hours=self.ttl_hours)
        if fresh is not None:
            return [RedditPost(**d) for d in fresh]
        try:
            posts = self.inner.fetch_posts(subreddit, sort, timeframe, limit)
            self.cache.set(key, [asdict(p) for p in posts])
            return posts
        except RedditBlocked:
            stale = self.cache.get(key)  # no TTL: stale is better than empty
            if stale is not None:
                logger.warning("blocked; serving stale cache for r/%s", subreddit)
                return [RedditPost(**d) for d in stale]
            raise

    def fetch_comments(
        self,
        post_id: str,
        subreddit: str,
        limit: int = 200,
    ) -> list[RedditComment]:
        key = f"comments:{post_id}"
        fresh = self.cache.get(key, max_age_hours=self.ttl_hours)
        if fresh is not None:
            return [RedditComment(**d) for d in fresh]
        try:
            comments = self.inner.fetch_comments(post_id, subreddit, limit)
            self.cache.set(key, [asdict(c) for c in comments])
            return comments
        except RedditBlocked:
            stale = self.cache.get(key)
            if stale is not None:
                logger.warning("blocked; serving stale cache for comments:%s", post_id)
                return [RedditComment(**d) for d in stale]
            raise


# ---------------------------------------------------------------------------
# Fallback chain (MVP: single source; alt-egress slot ready to activate)
# ---------------------------------------------------------------------------

class FirstAvailableRedditSource:
    """Tries each source in order, falling through to the next on RedditBlocked."""

    def __init__(self, sources: list[RedditSource]) -> None:
        self.sources = sources

    def fetch_posts(self, *args, **kwargs) -> list[RedditPost]:
        last: RedditBlocked | None = None
        for src in self.sources:
            try:
                return src.fetch_posts(*args, **kwargs)
            except RedditBlocked as e:
                last = e
        raise last or RedditBlocked("no source available")

    def fetch_comments(self, *args, **kwargs) -> list[RedditComment]:
        last: RedditBlocked | None = None
        for src in self.sources:
            try:
                return src.fetch_comments(*args, **kwargs)
            except RedditBlocked as e:
                last = e
        raise last or RedditBlocked("no source available")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_reddit_source(cache: RedditCache) -> CachedRedditSource:
    primary = CachedRedditSource(JsonRedditSource(), cache)
    # To add a proxied fallback later — zero downstream change:
    #   alt = CachedRedditSource(JsonRedditSource(session=proxied_session), alt_cache)
    #   return FirstAvailableRedditSource([primary, alt])
    return primary


# ---------------------------------------------------------------------------
# Subreddit resolution with 30-day cache
# ---------------------------------------------------------------------------

_LOOKUP_TTL_HOURS = 24 * 30


def cached_resolve_subreddit(
    game_title: str,
    json_source: JsonRedditSource,
    lookup_cache: RedditCache,
) -> str | None:
    """
    Resolve a game title to its best-matching subreddit name, caching the result
    for 30 days so we don't re-query ~3k games on every weekly run.

    Stores [display_name] on a hit, [] on a confirmed no-match — so both
    positive and negative results are cached and won't be re-queried.
    """
    key = _normalize(game_title)
    cached = lookup_cache.get(key, max_age_hours=_LOOKUP_TTL_HOURS)
    if cached is not None:
        return cached[0] if cached else None

    result = json_source.resolve_subreddit(game_title)
    lookup_cache.set(key, [result] if result else [])
    return result
