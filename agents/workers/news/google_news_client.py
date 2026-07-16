"""
Google News RSS adapter for the news worker -- per-entity fallback, used only
to backfill watchlist entities with thin coverage from GDELT + curated RSS
(see docs/news-source-decision-memo.md §1c).

Tier 2 (public-but-unofficial, see docs/data-source-risk-register.md): no
key, no documented SLA, undocumented rate limits. Reuses rss_client's feed
parser directly -- this is a same-*package* import (both live in
agents/workers/news/), not the cross-*package* import this codebase avoids.

v1 stores the Google-redirect URL as-is (still a stable unique key for
news_items' url primary key / cache keys); unwrapping to the true publisher
URL is a follow-up, not blocking for relevance matching or stance scoring.
"""

from __future__ import annotations

import logging

import requests

from agents.http_retry import request_with_retry
from agents.workers.news.rss_client import RateLimiter, _parse_feed
from database.api_cache import ApiCache

logger = logging.getLogger("google_news_client")

_USER_AGENT = "games-intel-platform/0.1 (news worker; contact: cgonztx@gmail.com)"
_SEARCH_URL = "https://news.google.com/rss/search"


class GoogleNewsSource:
    def __init__(
        self,
        user_agent: str = _USER_AGENT,
        limiter: RateLimiter | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.limiter = limiter or RateLimiter()
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent})

    def search(self, query: str) -> list[dict]:
        self.limiter.wait()
        resp = request_with_retry(
            self.session.get,
            _SEARCH_URL,
            params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
            timeout=15,
        )
        resp.raise_for_status()
        return _parse_feed(resp.text)


class CachedGoogleNewsSource:
    """Same sustained-outage circuit breaker as CachedGdeltSource, for the
    same reason: a stale-cache rescue hides the live failure from the
    worker's per-entity loop, so the breaker must count live-call failures
    here, before the rescue -- see CachedGdeltSource's docstring."""

    def __init__(
        self,
        inner: GoogleNewsSource,
        cache: ApiCache,
        ttl_hours: float = 12.0,
        max_consecutive_live_failures: int = 10,
    ) -> None:
        self.inner = inner
        self.cache = cache
        self.ttl_hours = ttl_hours
        self.max_consecutive_live_failures = max_consecutive_live_failures
        self._consecutive_live_failures = 0

    def _live_circuit_open(self) -> bool:
        return self._consecutive_live_failures >= self.max_consecutive_live_failures

    def search(self, query: str) -> list[dict]:
        key = f"query:{query}"
        fresh = self.cache.get(key, max_age_hours=self.ttl_hours)
        if fresh is not None:
            return fresh
        if self._live_circuit_open():
            stale = self.cache.get(key)
            if stale is not None:
                return stale
            raise RuntimeError(
                f"live calls circuit-broken after {self._consecutive_live_failures} "
                f"consecutive failures; no cache for query {query!r}"
            )
        try:
            articles = self.inner.search(query)
        except Exception:
            self._consecutive_live_failures += 1
            if self._live_circuit_open():
                logger.warning(
                    "%d consecutive live failures; disabling live Google News calls for the rest of the run",
                    self._consecutive_live_failures,
                )
            stale = self.cache.get(key)  # no TTL: stale is better than empty
            if stale is not None:
                logger.warning("fetch failed for query %r; serving stale cache", query)
                return stale
            raise
        self._consecutive_live_failures = 0
        self.cache.set(key, articles)
        return articles
