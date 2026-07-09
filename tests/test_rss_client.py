"""
Unit tests for agents/workers/news/rss_client.py.

No live network calls: feed parsing is tested directly against fixture
strings (pure functions), and fetch degradation is tested via a stubbed
_fetch_feed_entries substitution -- mirrors tests/test_blog_client.py's
convention for the sibling patch_notes adapter.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agents.workers.news.rss_client as rss_client
from agents.workers.news.rss_client import CachedRssSource, fetch_curated_feeds
from database.api_cache import InMemoryApiCache

RSS_FIXTURE = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Studio ships big patch</title>
    <description>&lt;p&gt;Lots of &lt;b&gt;changes&lt;/b&gt; this week.&lt;/p&gt;</description>
    <link>https://example.com/patch-notes</link>
    <pubDate>Wed, 01 Jul 2026 12:00:00 GMT</pubDate>
  </item>
</channel></rss>
"""

ATOM_FIXTURE = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Layoffs hit studio</title>
    <summary>A summary of the story.</summary>
    <link rel="alternate" href="https://example.com/layoffs"/>
    <updated>2026-07-02T09:00:00Z</updated>
  </entry>
</feed>
"""

NON_FEED_TEXT = "<html><body>not a feed</body></html>"


def test_parse_feed_rss_item():
    entries = rss_client._parse_feed(RSS_FIXTURE)

    assert len(entries) == 1
    assert entries[0]["title"] == "Studio ships big patch"
    assert entries[0]["url"] == "https://example.com/patch-notes"
    assert entries[0]["domain"] == "example.com"
    assert "changes" in entries[0]["snippet"]
    assert entries[0]["published_at"] == "2026-07-01T12:00:00+00:00"


def test_parse_feed_atom_entry():
    entries = rss_client._parse_feed(ATOM_FIXTURE)

    assert len(entries) == 1
    assert entries[0]["title"] == "Layoffs hit studio"
    assert entries[0]["url"] == "https://example.com/layoffs"
    assert entries[0]["published_at"] == "2026-07-02T09:00:00+00:00"


def test_parse_feed_returns_empty_for_non_xml():
    assert rss_client._parse_feed(NON_FEED_TEXT) == []


def test_parse_feed_skips_entries_without_url():
    xml = """<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <item><title>No link here</title><description>d</description></item>
    </channel></rss>
    """
    assert rss_client._parse_feed(xml) == []


# ---------------------------------------------------------------------------
# CachedRssSource
# ---------------------------------------------------------------------------

def test_cached_fetch_feed_serves_fresh_cache(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("must not fetch on a fresh cache hit")

    monkeypatch.setattr(rss_client, "_fetch_feed_entries", _boom)
    cache = InMemoryApiCache()
    cache.set("feed:https://example.com/feed", [{"url": "https://example.com/a"}])
    source = CachedRssSource(cache=cache)

    entries = source.fetch_feed("https://example.com/feed")

    assert entries == [{"url": "https://example.com/a"}]


def test_cached_fetch_feed_fetches_and_caches_on_miss(monkeypatch):
    monkeypatch.setattr(
        rss_client,
        "_fetch_feed_entries",
        lambda url, limiter, session: [{"url": "https://example.com/b"}],
    )
    cache = InMemoryApiCache()
    source = CachedRssSource(cache=cache)

    entries = source.fetch_feed("https://example.com/feed")

    assert entries == [{"url": "https://example.com/b"}]
    assert cache.get("feed:https://example.com/feed") == [{"url": "https://example.com/b"}]


def test_cached_fetch_feed_serves_stale_on_failure(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(rss_client, "_fetch_feed_entries", _boom)
    cache = InMemoryApiCache()
    cache._store["feed:https://example.com/feed"] = ([{"url": "stale"}], 0.0)
    source = CachedRssSource(cache=cache)

    entries = source.fetch_feed("https://example.com/feed")

    assert entries == [{"url": "stale"}]


def test_cached_fetch_feed_reraises_when_no_stale_cache(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(rss_client, "_fetch_feed_entries", _boom)
    source = CachedRssSource(cache=InMemoryApiCache())

    try:
        source.fetch_feed("https://example.com/feed")
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


# ---------------------------------------------------------------------------
# fetch_curated_feeds -- one bad feed must not kill the batch
# ---------------------------------------------------------------------------

def test_fetch_curated_feeds_degrades_per_feed():
    class _StubSource:
        def fetch_feed(self, url):
            if url == "https://bad.example.com/feed":
                raise RuntimeError("boom")
            return [{"url": url + "/article"}]

    articles = fetch_curated_feeds(_StubSource(), feeds=[
        "https://good.example.com/feed",
        "https://bad.example.com/feed",
        "https://good2.example.com/feed",
    ])

    assert len(articles) == 2
    assert {a["url"] for a in articles} == {
        "https://good.example.com/feed/article",
        "https://good2.example.com/feed/article",
    }


# ---------------------------------------------------------------------------
# _fetch_feed_entries: transient-error retry (agents.http_retry.request_with_retry)
# ---------------------------------------------------------------------------

class _FakeHttpResponse:
    def __init__(self, status_code=200, text=RSS_FIXTURE, headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise rss_client.requests.HTTPError(f"{self.status_code}")


class _FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)

    def get(self, *args, **kwargs):
        return self.responses.pop(0)


def test_fetch_feed_entries_retries_transient_503_then_succeeds(monkeypatch):
    monkeypatch.setattr("agents.http_retry.time.sleep", lambda _: None)
    session = _FakeSession([_FakeHttpResponse(status_code=503), _FakeHttpResponse()])
    limiter = rss_client.RateLimiter(min_interval=0.0, jitter=0.0)

    entries = rss_client._fetch_feed_entries("https://example.com/feed", limiter, session)

    assert len(entries) == 1
    assert entries[0]["title"] == "Studio ships big patch"
