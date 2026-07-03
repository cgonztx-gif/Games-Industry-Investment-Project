"""
Unit tests for agents/workers/patch_notes/blog_client.py.

No live HTTP and no live Supabase: feed/HTML parsing is tested against fixture
strings, and the cache-wrapper behavior is tested with InMemoryApiCache
(database/api_cache.py) standing in for SupabaseApiCache.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agents.workers.patch_notes import blog_client
from database.api_cache import InMemoryApiCache

RSS_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example Studio Patch Notes</title>
    <item>
      <title>Hotfix 1.2.3 - Weapon Balance</title>
      <link>https://example.com/news/hotfix-1-2-3</link>
      <description>&lt;p&gt;Nerfed the shotgun and fixed a crash on startup.&lt;/p&gt;</description>
      <pubDate>Wed, 02 Oct 2024 15:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Store Update - New Battle Pass</title>
      <link>https://example.com/news/store-refresh</link>
      <description>New battle pass bundle available in the shop this update.</description>
      <pubDate>Wed, 09 Oct 2024 15:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Community Spotlight</title>
      <link>https://example.com/news/spotlight</link>
      <description>Check out this week's fan art.</description>
      <pubDate>Wed, 09 Oct 2024 16:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""

ATOM_FIXTURE = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example Studio Dev Blog</title>
  <entry>
    <title>Update 4.0 - New Season and Balance Pass</title>
    <link rel="alternate" href="https://example.com/blog/update-4-0"/>
    <updated>2024-10-05T12:00:00Z</updated>
    <content type="html">&lt;p&gt;New season, new map, and balance changes.&lt;/p&gt;</content>
  </entry>
</feed>
"""

HTML_FIXTURE = """<!DOCTYPE html>
<html>
<head><title>Patch Notes - Example Game</title></head>
<body>
  <h1>Patch 1.5 Update</h1>
  <p>This hotfix addresses balance issues and adds new content.</p>
</body>
</html>
"""

NON_FEED_TEXT = "Not XML at all, just plain text."


# ---------------------------------------------------------------------------
# load_game_patch_pages
# ---------------------------------------------------------------------------

def test_load_game_patch_pages_missing_env(monkeypatch):
    monkeypatch.delenv("GAME_PATCH_PAGES", raising=False)
    assert blog_client.load_game_patch_pages() == {}


def test_load_game_patch_pages_invalid_json(monkeypatch):
    monkeypatch.setenv("GAME_PATCH_PAGES", "{not valid json")
    assert blog_client.load_game_patch_pages() == {}


def test_load_game_patch_pages_non_dict_json(monkeypatch):
    monkeypatch.setenv("GAME_PATCH_PAGES", "[1, 2, 3]")
    assert blog_client.load_game_patch_pages() == {}


def test_load_game_patch_pages_valid(monkeypatch):
    monkeypatch.setenv(
        "GAME_PATCH_PAGES",
        '{"Fortnite": ["https://www.fortnite.com/news/rss"], '
        '"Apex Legends": "https://example.com/apex/rss", '
        '"Bad Entry": 123}',
    )
    result = blog_client.load_game_patch_pages()
    assert result["Fortnite"] == ["https://www.fortnite.com/news/rss"]
    assert result["Apex Legends"] == ["https://example.com/apex/rss"]
    # A value that's neither a string nor a list (e.g. a stray int) is dropped
    # entirely rather than mapped to an empty list.
    assert "Bad Entry" not in result


# ---------------------------------------------------------------------------
# Feed parsing
# ---------------------------------------------------------------------------

def test_parse_feed_rss():
    entries = blog_client._parse_feed(RSS_FIXTURE)
    assert len(entries) == 3
    first = entries[0]
    assert first["title"] == "Hotfix 1.2.3 - Weapon Balance"
    assert "Nerfed the shotgun" in first["contents"]
    assert "<p>" not in first["contents"]
    assert first["url"] == "https://example.com/news/hotfix-1-2-3"
    assert first["date"] is not None
    assert first["date"].startswith("2024-10-02")


def test_parse_feed_atom():
    entries = blog_client._parse_feed(ATOM_FIXTURE)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["title"] == "Update 4.0 - New Season and Balance Pass"
    assert "New season, new map" in entry["contents"]
    assert entry["url"] == "https://example.com/blog/update-4-0"
    assert entry["date"].startswith("2024-10-05")


def test_parse_feed_non_xml_returns_empty():
    assert blog_client._parse_feed(NON_FEED_TEXT) == []


# ---------------------------------------------------------------------------
# HTML fallback
# ---------------------------------------------------------------------------

def test_parse_html_page():
    entries = blog_client._parse_html_page(HTML_FIXTURE, "https://example.com/patch-notes")
    assert len(entries) == 1
    entry = entries[0]
    assert entry["title"] == "Patch Notes - Example Game"
    assert "Patch 1.5 Update" in entry["contents"]
    assert "hotfix" in entry["contents"].lower()
    assert entry["url"] == "https://example.com/patch-notes"
    assert entry["date"] is not None


def test_looks_like_feed_detection():
    assert blog_client._looks_like_feed("application/rss+xml", "") is True
    assert blog_client._looks_like_feed("text/xml; charset=utf-8", "") is True
    assert blog_client._looks_like_feed("text/html", RSS_FIXTURE) is True
    assert blog_client._looks_like_feed("text/html", HTML_FIXTURE) is False


# ---------------------------------------------------------------------------
# get_recent_entries (filtering, classification, sorting)
# ---------------------------------------------------------------------------

class _FakeSource:
    """Stand-in for CachedBlogSource returning fixed raw entries."""

    def __init__(self, entries: list[dict]) -> None:
        self._entries = entries

    def fetch_raw_entries(self, url: str) -> list[dict]:
        return self._entries


def test_get_recent_entries_filters_and_classifies():
    raw_entries = blog_client._parse_feed(RSS_FIXTURE)
    source = _FakeSource(raw_entries)

    items = blog_client.get_recent_entries(source, "https://example.com/news/rss", days_back=3650)

    # "Community Spotlight" has no patch-update keywords -> filtered out.
    titles = [item["title"] for item in items]
    assert "Community Spotlight" not in titles
    assert "Hotfix 1.2.3 - Weapon Balance" in titles
    assert "Store Update - New Battle Pass" in titles

    # Sorted ascending by date.
    assert items == sorted(items, key=lambda i: i["date"])

    hotfix_item = next(i for i in items if i["title"] == "Hotfix 1.2.3 - Weapon Balance")
    assert hotfix_item["patch_type"] == "hotfix"
    assert hotfix_item["date"] == "2024-10-02"
    assert hotfix_item["url"] == "https://example.com/news/hotfix-1-2-3"

    store_item = next(i for i in items if i["title"] == "Store Update - New Battle Pass")
    assert store_item["patch_type"] == "monetization"


def test_get_recent_entries_respects_days_back_cutoff():
    raw_entries = blog_client._parse_feed(RSS_FIXTURE)
    source = _FakeSource(raw_entries)

    # All fixture dates are in Oct 2024; a 1-day cutoff from "now" excludes everything.
    items = blog_client.get_recent_entries(source, "https://example.com/news/rss", days_back=1)
    assert items == []


def test_get_recent_entries_skips_entries_without_date():
    source = _FakeSource([{"title": "Patch notes", "contents": "update details", "url": "u", "date": None}])
    items = blog_client.get_recent_entries(source, "https://example.com/x", days_back=3650)
    assert items == []


# ---------------------------------------------------------------------------
# CachedBlogSource: cache hit / miss / stale-on-failure
# ---------------------------------------------------------------------------

def test_cached_blog_source_cache_hit_skips_fetch(monkeypatch):
    cache = InMemoryApiCache()
    url = "https://example.com/feed"
    cache.set(f"entries:{url}", [{"title": "cached", "contents": "c", "url": url, "date": "2024-01-01T00:00:00+00:00"}])

    def _boom(*args, **kwargs):
        raise AssertionError("should not fetch on a cache hit")

    monkeypatch.setattr(blog_client, "_fetch_raw_entries", _boom)

    source = blog_client.CachedBlogSource(cache)
    result = source.fetch_raw_entries(url)
    assert result[0]["title"] == "cached"


def test_cached_blog_source_cache_miss_fetches_and_stores(monkeypatch):
    cache = InMemoryApiCache()
    url = "https://example.com/feed"
    fetched = [{"title": "fresh", "contents": "c", "url": url, "date": "2024-01-01T00:00:00+00:00"}]

    monkeypatch.setattr(blog_client, "_fetch_raw_entries", lambda *a, **k: fetched)

    source = blog_client.CachedBlogSource(cache)
    result = source.fetch_raw_entries(url)
    assert result == fetched
    # Now cached.
    assert cache.get(f"entries:{url}") == fetched


def test_cached_blog_source_serves_stale_on_fetch_failure(monkeypatch):
    cache = InMemoryApiCache()
    url = "https://example.com/feed"
    key = f"entries:{url}"
    stale = [{"title": "stale", "contents": "c", "url": url, "date": "2024-01-01T00:00:00+00:00"}]
    cache.set(key, stale)
    # Force the "fresh" lookup to miss deterministically by back-dating the stored
    # timestamp, rather than racing on wall-clock time with a 0-hour TTL.
    value, _ts = cache._store[key]
    cache._store[key] = (value, 0.0)

    source = blog_client.CachedBlogSource(cache)  # default ttl_hours=12.0

    def _boom(*args, **kwargs):
        raise blog_client.requests.exceptions.RequestException("network down")

    monkeypatch.setattr(blog_client, "_fetch_raw_entries", _boom)

    result = source.fetch_raw_entries(url)
    assert result == stale


def test_cached_blog_source_raises_when_no_stale_cache(monkeypatch):
    cache = InMemoryApiCache()
    url = "https://example.com/feed"
    source = blog_client.CachedBlogSource(cache)

    def _boom(*args, **kwargs):
        raise blog_client.requests.exceptions.RequestException("network down")

    monkeypatch.setattr(blog_client, "_fetch_raw_entries", _boom)

    with pytest.raises(blog_client.requests.exceptions.RequestException):
        source.fetch_raw_entries(url)


# ---------------------------------------------------------------------------
# RateLimiter sanity (no live sleeping in tests -- just verify interface)
# ---------------------------------------------------------------------------

def test_rate_limiter_first_call_does_not_block():
    limiter = blog_client.RateLimiter(min_interval=0.0, jitter=0.0)
    limiter.wait()  # should return immediately without raising
