"""
Unit tests for agents/workers/news/google_news_client.py.

No live network calls: GoogleNewsSource is given a _FakeSession, following
the pattern in tests/test_gdelt_client.py. CachedGoogleNewsSource is tested
with InMemoryApiCache and a plain stub/boom inner.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from agents.workers.news.google_news_client import CachedGoogleNewsSource, GoogleNewsSource
from agents.workers.news.rss_client import RateLimiter
from database.api_cache import InMemoryApiCache

# Minimal RSS 2.0 feed that _parse_feed will parse into one article.
_RSS_BODY = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Google News</title>
    <item>
      <title>Big Game Update Released</title>
      <link>https://example.com/story</link>
      <description>A great patch.</description>
      <pubDate>Wed, 01 Jul 2026 12:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""


class _FakeResponse:
    def __init__(self, status_code=200, text=_RSS_BODY):
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


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


class _BoomInner:
    def search(self, *args, **kwargs):
        raise AssertionError("inner must not be called on a fresh cache hit")


# ---------------------------------------------------------------------------
# GoogleNewsSource
# ---------------------------------------------------------------------------

def test_query_params_formed():
    session = _FakeSession([_FakeResponse()])
    source = GoogleNewsSource(limiter=_fast_limiter(), session=session)

    source.search("Elden Ring")

    assert session.calls
    _, kwargs = session.calls[0]
    params = kwargs.get("params", {})
    assert params.get("q") == "Elden Ring"
    assert params.get("hl") == "en-US"
    assert params.get("gl") == "US"
    assert params.get("ceid") == "US:en"


def test_search_parses_rss_feed():
    session = _FakeSession([_FakeResponse()])
    source = GoogleNewsSource(limiter=_fast_limiter(), session=session)

    articles = source.search("Elden Ring")

    assert len(articles) == 1
    assert articles[0]["title"] == "Big Game Update Released"
    assert articles[0]["url"] == "https://example.com/story"


def test_search_raises_on_http_error():
    session = _FakeSession([_FakeResponse(status_code=429)])
    source = GoogleNewsSource(limiter=_fast_limiter(), session=session)

    try:
        source.search("query")
        assert False, "expected HTTPError"
    except requests.HTTPError:
        pass


# ---------------------------------------------------------------------------
# CachedGoogleNewsSource
# ---------------------------------------------------------------------------

def test_cache_hit_bypasses_network():
    cache = InMemoryApiCache()
    cache.set("query:Elden Ring", [{"url": "https://cached.com/a"}])
    source = CachedGoogleNewsSource(_BoomInner(), cache=cache, ttl_hours=12.0)

    articles = source.search("Elden Ring")

    assert articles == [{"url": "https://cached.com/a"}]


def test_cache_miss_fetches_and_stores():
    class _StubInner:
        def search(self, query):
            return [{"url": "https://fresh.com/a"}]

    cache = InMemoryApiCache()
    source = CachedGoogleNewsSource(_StubInner(), cache=cache, ttl_hours=12.0)

    articles = source.search("query")

    assert articles == [{"url": "https://fresh.com/a"}]
    assert cache.get("query:query") == [{"url": "https://fresh.com/a"}]


def test_stale_cache_on_fetch_failure():
    class _FailInner:
        def search(self, query):
            raise requests.ConnectionError("unreachable")

    cache = InMemoryApiCache()
    # Plant stale entry (timestamp 0 = old)
    cache._store["query:Elden Ring"] = ([{"url": "https://stale.com/a"}], 0.0)
    source = CachedGoogleNewsSource(_FailInner(), cache=cache, ttl_hours=12.0)

    articles = source.search("Elden Ring")

    assert articles == [{"url": "https://stale.com/a"}]


def test_raises_when_blocked_and_no_stale():
    class _FailInner:
        def search(self, query):
            raise requests.ConnectionError("unreachable")

    source = CachedGoogleNewsSource(_FailInner(), cache=InMemoryApiCache(), ttl_hours=12.0)

    try:
        source.search("query")
        assert False, "expected exception"
    except Exception as exc:
        assert not isinstance(exc, AssertionError)
