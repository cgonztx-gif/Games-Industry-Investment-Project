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
    def __init__(
        self,
        inner: GoogleNewsSource,
        cache: ApiCache,
        ttl_hours: float = 12.0,
    ) -> None:
        self.inner = inner
        self.cache = cache
        self.ttl_hours = ttl_hours

    def search(self, query: str) -> list[dict]:
        key = f"query:{query}"
        fresh = self.cache.get(key, max_age_hours=self.ttl_hours)
        if fresh is not None:
            return fresh
        try:
            articles = self.inner.search(query)
            self.cache.set(key, articles)
            return articles
        except Exception:
            stale = self.cache.get(key)  # no TTL: stale is better than empty
            if stale is not None:
                logger.warning("fetch failed for query %r; serving stale cache", query)
                return stale
            raise
