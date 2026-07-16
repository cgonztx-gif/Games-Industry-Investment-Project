"""
GDELT DOC 2.0 adapter for the news worker.

See docs/news-source-decision-memo.md §1a: GDELT is an official, free, keyless
API (Tier 1 by ToS), but has no SLA and is rate-limited/format-fragile enough
to warrant the same resilience posture as a Tier-2 source (see
docs/data-source-risk-register.md) -- rate limiter + api_cache-backed fetch +
graceful degradation, mirroring the shape of
agents/workers/sentiment/reddit_source.py. This module has no LLM in the loop.

GDELT's `artlist` mode returns article metadata only (url/title/seendate/
domain) -- no per-article tone or body text. Tone/stance for this pipeline
comes from agents/workers/sentiment/news_stance_client.py's Claude call, not
from GDELT, so no tone field is parsed here.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone

import requests

from database.api_cache import ApiCache

logger = logging.getLogger("gdelt_client")

_USER_AGENT = "Mozilla/5.0 (compatible; games-intel-platform/0.1; contact: cgonztx@gmail.com)"
_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"


class GdeltBlocked(Exception):
    """GDELT throttled us or returned an unparseable response after retries.
    Signals callers to degrade gracefully instead of failing the run."""


# ---------------------------------------------------------------------------
# Rate limiter (local re-implementation of reddit_source.RateLimiter -- kept
# local rather than shared since worker packages don't cross-import)
# ---------------------------------------------------------------------------

class RateLimiter:
    def __init__(self, min_interval: float = 1.0, jitter: float = 0.5) -> None:
        self.min_interval = min_interval
        self.jitter = jitter
        self._last: float = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last
        delay = self.min_interval + random.uniform(0, self.jitter)
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last = time.monotonic()


def _parse_seendate(value: str) -> str | None:
    """GDELT seendate is YYYYMMDDHHMMSS UTC. Returns an ISO-8601 string, or
    None if unparseable."""
    try:
        dt = datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except (TypeError, ValueError):
        return None


def _parse_articles(data: dict) -> list[dict]:
    out: list[dict] = []
    for article in data.get("articles", []) or []:
        url = article.get("url")
        if not url:
            continue
        out.append(
            {
                "url": url,
                "title": article.get("title") or "",
                "published_at": _parse_seendate(article.get("seendate", "")),
                "domain": article.get("domain") or "",
                "snippet": "",
            }
        )
    return out


# ---------------------------------------------------------------------------
# Primary source
# ---------------------------------------------------------------------------

class GdeltSource:
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

    def search(
        self,
        query: str,
        timespan: str = "1w",
        max_records: int = 250,
    ) -> list[dict]:
        params = {
            "query": query,
            "mode": "artlist",
            "format": "json",
            "maxrecords": min(max_records, 250),
            "timespan": timespan,
        }
        for attempt in range(1, self.max_retries + 1):
            self.limiter.wait()
            try:
                resp = self.session.get(_DOC_URL, params=params, timeout=15)
            except requests.exceptions.RequestException as exc:
                logger.warning(
                    "gdelt request error: %s (attempt %d/%d)", exc, attempt, self.max_retries,
                )
                continue
            if resp.status_code == 429:
                backoff = float(resp.headers.get("Retry-After", 2 ** attempt))
                logger.warning(
                    "throttled 429; backoff %.0fs (attempt %d/%d)",
                    backoff, attempt, self.max_retries,
                )
                time.sleep(backoff)
                continue
            if resp.status_code != 200:
                logger.warning("gdelt non-200: %s", resp.status_code)
                raise GdeltBlocked(f"{resp.status_code} for query {query!r}")
            try:
                data = resp.json()
            except ValueError:
                # GDELT sometimes returns an HTML/plaintext error body instead
                # of JSON on malformed queries or transient failures.
                logger.warning("gdelt returned non-JSON body for query %r", query)
                raise GdeltBlocked(f"non-JSON response for query {query!r}")
            return _parse_articles(data)
        logger.warning("retries exhausted for query %r", query)
        raise GdeltBlocked(f"retries exhausted for query {query!r}")


# ---------------------------------------------------------------------------
# Caching wrapper (mirrors CachedRedditSource / CachedBlogSource: serve stale
# on failure instead of raising -- one entity's query failing must not fail
# the whole news run)
# ---------------------------------------------------------------------------

class CachedGdeltSource:
    """Besides fresh-cache/serve-stale semantics, this wrapper owns the
    sustained-outage circuit breaker: after `max_consecutive_live_failures`
    live calls fail in a row, it stops calling GDELT entirely for the rest of
    the run (stale cache is still served where it exists; otherwise
    GdeltBlocked raises immediately). The breaker must live HERE, not only in
    the worker's per-entity loop: a stale-cache rescue makes the worker see a
    success, so a worker-level consecutive-failure counter resets on every
    cached entity even while every live call is failing and paying the full
    retry/backoff cost -- confirmed live in CI run 29438324984 (2026-07-15),
    which ground through 317 exhausted-retry cycles interleaved with 196
    stale serves for ~6h without the worker-level breaker ever tripping."""

    def __init__(
        self,
        inner: GdeltSource,
        cache: ApiCache,
        ttl_hours: float = 24.0,
        max_consecutive_live_failures: int = 10,
    ) -> None:
        self.inner = inner
        self.cache = cache
        self.ttl_hours = ttl_hours
        self.max_consecutive_live_failures = max_consecutive_live_failures
        self._consecutive_live_failures = 0

    def _live_circuit_open(self) -> bool:
        return self._consecutive_live_failures >= self.max_consecutive_live_failures

    def search(self, query: str, timespan: str = "1w", max_records: int = 250) -> list[dict]:
        key = f"query:{query}:{timespan}"
        fresh = self.cache.get(key, max_age_hours=self.ttl_hours)
        if fresh is not None:
            return fresh
        if self._live_circuit_open():
            stale = self.cache.get(key)
            if stale is not None:
                return stale
            raise GdeltBlocked(
                f"live calls circuit-broken after {self._consecutive_live_failures} "
                f"consecutive failures; no cache for query {query!r}"
            )
        try:
            articles = self.inner.search(query, timespan=timespan, max_records=max_records)
        except GdeltBlocked:
            self._consecutive_live_failures += 1
            if self._live_circuit_open():
                logger.warning(
                    "%d consecutive live failures; disabling live GDELT calls for the rest of the run",
                    self._consecutive_live_failures,
                )
            stale = self.cache.get(key)  # no TTL: stale is better than empty
            if stale is not None:
                logger.warning("blocked; serving stale cache for query %r", query)
                return stale
            raise
        self._consecutive_live_failures = 0
        self.cache.set(key, articles)
        return articles
