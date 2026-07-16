"""
Unit tests for agents/workers/news/gdelt_client.py.

No live network calls: every requests.Session is a local _FakeSession that
pops pre-scripted _FakeResponse objects in call order. Mirrors
tests/test_reddit_source.py's house convention: plain recording fakes, no
mocking library, assertion-raising fakes to prove a path is never invoked.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from agents.workers.news.gdelt_client import (
    CachedGdeltSource,
    GdeltBlocked,
    GdeltSource,
    RateLimiter,
)
from database.api_cache import InMemoryApiCache


class _FakeResponse:
    def __init__(self, status_code, json_data=None, headers=None, json_error=False):
        self.status_code = status_code
        self._json_data = {} if json_data is None else json_data
        self.headers = headers or {}
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("not JSON")
        return self._json_data


class _FakeSession:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls: list = []
        self.headers: dict = {}

    def get(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _fast_limiter():
    return RateLimiter(min_interval=0.0, jitter=0.0)


def _articles_response(articles):
    return _FakeResponse(200, json_data={"articles": articles})


# ---------------------------------------------------------------------------
# GdeltSource
# ---------------------------------------------------------------------------

def test_search_parses_articles():
    session = _FakeSession([
        _articles_response([
            {"url": "https://example.com/a", "title": "Big Update", "seendate": "20260701120000", "domain": "example.com"},
            {"url": "", "title": "no url, skipped"},  # missing url must be dropped
        ])
    ])
    source = GdeltSource(limiter=_fast_limiter(), session=session)

    articles = source.search('"Baldur\'s Gate 3"')

    assert len(articles) == 1
    assert articles[0]["url"] == "https://example.com/a"
    assert articles[0]["title"] == "Big Update"
    assert articles[0]["domain"] == "example.com"
    assert articles[0]["published_at"] == "2026-07-01T12:00:00+00:00"


def test_search_retries_on_429_then_succeeds():
    session = _FakeSession([
        _FakeResponse(429, headers={"Retry-After": "0"}),
        _articles_response([]),
    ])
    source = GdeltSource(limiter=_fast_limiter(), session=session, max_retries=3)

    articles = source.search("query")

    assert articles == []
    assert len(session.calls) == 2


def test_search_raises_gdelt_blocked_after_retries_exhausted():
    session = _FakeSession([
        _FakeResponse(500),
        _FakeResponse(500),
        _FakeResponse(500),
    ])
    source = GdeltSource(limiter=_fast_limiter(), session=session, max_retries=3)

    try:
        source.search("query")
        assert False, "expected GdeltBlocked"
    except GdeltBlocked:
        pass


def test_search_retries_on_connection_error_then_succeeds():
    session = _FakeSession([
        requests.exceptions.ConnectionError("Remote end closed connection without response"),
        _articles_response([]),
    ])
    source = GdeltSource(limiter=_fast_limiter(), session=session, max_retries=3)

    articles = source.search("query")

    assert articles == []
    assert len(session.calls) == 2


def test_search_raises_gdelt_blocked_after_connection_errors_exhausted():
    session = _FakeSession([
        requests.exceptions.ConnectionError("boom"),
        requests.exceptions.ConnectionError("boom"),
        requests.exceptions.ConnectionError("boom"),
    ])
    source = GdeltSource(limiter=_fast_limiter(), session=session, max_retries=3)

    try:
        source.search("query")
        assert False, "expected GdeltBlocked"
    except GdeltBlocked:
        pass


def test_search_raises_gdelt_blocked_on_non_json_body():
    session = _FakeSession([_FakeResponse(200, json_error=True)])
    source = GdeltSource(limiter=_fast_limiter(), session=session)

    try:
        source.search("query")
        assert False, "expected GdeltBlocked"
    except GdeltBlocked:
        pass


# ---------------------------------------------------------------------------
# CachedGdeltSource
# ---------------------------------------------------------------------------

class _BoomInner:
    def search(self, *args, **kwargs):
        raise AssertionError("inner must not be called on a fresh cache hit")


def test_cached_search_serves_fresh_cache_without_calling_inner():
    cache = InMemoryApiCache()
    cache.set("query:q:1w", [{"url": "https://example.com/x"}])
    source = CachedGdeltSource(_BoomInner(), cache=cache)

    articles = source.search("q")

    assert articles == [{"url": "https://example.com/x"}]


def test_cached_search_fetches_and_caches_on_miss():
    class _StubInner:
        def search(self, query, timespan="1w", max_records=250):
            return [{"url": "https://example.com/y"}]

    cache = InMemoryApiCache()
    source = CachedGdeltSource(_StubInner(), cache=cache)

    articles = source.search("q")

    assert articles == [{"url": "https://example.com/y"}]
    assert cache.get("query:q:1w") == [{"url": "https://example.com/y"}]


def test_cached_search_serves_stale_on_block():
    class _BlockedInner:
        def search(self, *args, **kwargs):
            raise GdeltBlocked("429")

    cache = InMemoryApiCache()
    cache._store["query:q:1w"] = ([{"url": "https://example.com/stale"}], 0.0)
    source = CachedGdeltSource(_BlockedInner(), cache=cache)

    articles = source.search("q")

    assert articles == [{"url": "https://example.com/stale"}]


def test_cached_search_reraises_when_blocked_and_no_stale_cache():
    class _BlockedInner:
        def search(self, *args, **kwargs):
            raise GdeltBlocked("429")

    source = CachedGdeltSource(_BlockedInner(), cache=InMemoryApiCache())

    try:
        source.search("q")
        assert False, "expected GdeltBlocked"
    except GdeltBlocked:
        pass


# ---------------------------------------------------------------------------
# Live-call circuit breaker (CI run 29438324984, 2026-07-15: stale-cache
# rescues hid every live failure from the worker's loop-level breaker, so the
# run ground through ~6h of full-cost retry cycles without ever tripping it.
# The cached source itself must stop calling the network after a sustained
# consecutive live-failure streak, regardless of stale rescues.)
# ---------------------------------------------------------------------------

class _CountingBlockedInner:
    def __init__(self):
        self.calls = 0

    def search(self, *args, **kwargs):
        self.calls += 1
        raise GdeltBlocked("429")


def test_circuit_breaker_stops_live_calls_despite_stale_rescues():
    inner = _CountingBlockedInner()
    cache = InMemoryApiCache()
    # Every query has stale cache, so every failure is rescued and the
    # caller never sees an exception -- the exact pattern that defeated the
    # worker-level breaker in CI.
    for i in range(15):
        cache._store[f"query:q{i}:1w"] = ([{"url": f"https://stale.com/{i}"}], 0.0)
    source = CachedGdeltSource(inner, cache=cache, max_consecutive_live_failures=10)

    results = [source.search(f"q{i}") for i in range(15)]

    assert inner.calls == 10  # live calls stop at the limit
    assert all(r == [{"url": f"https://stale.com/{i}"}] for i, r in enumerate(results))


def test_circuit_breaker_open_raises_immediately_when_no_stale():
    inner = _CountingBlockedInner()
    cache = InMemoryApiCache()
    for i in range(10):
        cache._store[f"query:q{i}:1w"] = ([{"url": "https://stale.com/x"}], 0.0)
    source = CachedGdeltSource(inner, cache=cache, max_consecutive_live_failures=10)
    for i in range(10):
        source.search(f"q{i}")  # trip the breaker via stale-rescued failures

    try:
        source.search("uncached")
        assert False, "expected GdeltBlocked"
    except GdeltBlocked:
        pass
    assert inner.calls == 10  # the uncached query made no live call


def test_circuit_breaker_streak_resets_on_live_success():
    class _NinthTimeLucky:
        def __init__(self):
            self.calls = 0

        def search(self, *args, **kwargs):
            self.calls += 1
            if self.calls % 10 == 0:
                return [{"url": "https://live.com/ok"}]
            raise GdeltBlocked("429")

    inner = _NinthTimeLucky()
    cache = InMemoryApiCache()
    for i in range(30):
        cache._store[f"query:q{i}:1w"] = ([{"url": "https://stale.com/x"}], 0.0)
    source = CachedGdeltSource(inner, cache=cache, max_consecutive_live_failures=10)

    # 9 failures then a success, repeated: the streak never reaches 10, so
    # every distinct query gets a real live attempt.
    for i in range(30):
        source.search(f"q{i}")

    assert inner.calls == 30


def test_circuit_breaker_fresh_cache_hit_does_not_reset_streak():
    inner = _CountingBlockedInner()
    cache = InMemoryApiCache()
    for i in range(12):
        cache._store[f"query:q{i}:1w"] = ([{"url": "https://stale.com/x"}], 0.0)
    source = CachedGdeltSource(inner, cache=cache, max_consecutive_live_failures=10)

    for i in range(5):
        source.search(f"q{i}")  # 5 live failures
    cache.set("query:fresh:1w", [{"url": "https://fresh.com/a"}])
    source.search("fresh")  # fresh hit: no network touched, streak unchanged
    for i in range(5, 12):
        source.search(f"q{i}")

    assert inner.calls == 10  # streak continued through the fresh hit
