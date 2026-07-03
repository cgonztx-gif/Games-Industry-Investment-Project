"""
Developer-blog / official patch-page adapter for the patch_notes worker.

Tier-2 style adapter (see docs/data-source-risk-register.md): rate limiter +
api_cache-backed fetch + graceful degradation, mirroring the shape of
agents/workers/sentiment/reddit_source.py. This module has no LLM in the
loop -- it is a plain requests-based HTTP client, same convention as
steam_news_client.py and studio_intel/ats_clients.py.

Blog/patch-page URLs are manually curated config (no discovery mechanism),
loaded from the GAME_PATCH_PAGES env var the same defensive way
ats_clients.load_ats_board_map() loads STUDIO_ATS_BOARDS.

Prefers parsing RSS/Atom feeds (stdlib xml.etree.ElementTree) since that
avoids fragile HTML scraping; falls back to basic HTML text extraction
(reusing steam_news_client._clean_html) for a plain patch-notes page.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

from agents.workers.patch_notes.steam_news_client import (
    _clean_html,
    classify_patch,
    looks_like_update,
)
from database.api_cache import ApiCache

logger = logging.getLogger("blog_client")

_USER_AGENT = "games-intel-platform/0.1 (patch-notes worker; contact: cgonztx@gmail.com)"
_MAX_CONTENTS_CHARS = 5000


# ---------------------------------------------------------------------------
# Config loading (same defensive convention as ats_clients.load_ats_board_map)
# ---------------------------------------------------------------------------

def load_game_patch_pages() -> dict[str, list[str]]:
    """
    Load optional manually-curated developer-blog / official patch-page URLs.

    Expected env shape:
      GAME_PATCH_PAGES='{"Fortnite": ["https://www.fortnite.com/news/rss"],
                          "Apex Legends": "https://www.ea.com/games/apex-legends/news/rss"}'

    Keyed by exact watchlist game title (games.title) -- that's what's already
    available in the patch_notes worker loop without an extra studio join.
    Values may be a single URL string or a list of URLs. Missing/invalid env
    value returns {} (most games won't have one configured).
    """
    raw = os.environ.get("GAME_PATCH_PAGES", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}

    result: dict[str, list[str]] = {}
    for title, urls in data.items():
        if isinstance(urls, str):
            result[title] = [urls] if urls.strip() else []
        elif isinstance(urls, list):
            result[title] = [u for u in urls if isinstance(u, str) and u.strip()]
    return result


# ---------------------------------------------------------------------------
# Rate limiter (minimal re-implementation of reddit_source.RateLimiter --
# kept local rather than shared since worker packages don't cross-import,
# and there are only two callers so far)
# ---------------------------------------------------------------------------

class RateLimiter:
    """Minimum-interval limiter with jitter, evenly spacing requests across
    configured blog/patch-page URLs."""

    def __init__(self, min_interval: float = 3.0, jitter: float = 1.0) -> None:
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
# Feed parsing (RSS + Atom, namespace-agnostic via localname matching)
# ---------------------------------------------------------------------------

def _local(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _child_text(el: ET.Element, name: str) -> str:
    for child in el:
        if _local(child.tag) == name:
            return (child.text or "").strip()
    return ""


def _atom_link(el: ET.Element) -> str:
    links = [child for child in el if _local(child.tag) == "link"]
    for link in links:
        if link.get("rel") in (None, "alternate"):
            return link.get("href", "")
    return links[0].get("href", "") if links else ""


def _parse_date(value: str) -> str | None:
    """Parse RFC-822 (RSS pubDate) or ISO-8601 (Atom updated/published) into
    a UTC ISO-8601 timestamp string. Returns None if unparseable/blank."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def _parse_feed(xml_text: str) -> list[dict]:
    """Parse an RSS or Atom feed into raw entries: title/contents/url/date.
    Returns [] if the text isn't parseable XML (caller falls back to HTML)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    entries: list[dict] = []
    for item in root.iter():
        tag = _local(item.tag)
        if tag == "item":  # RSS 2.0
            title = _child_text(item, "title")
            contents = _clean_html(_child_text(item, "encoded") or _child_text(item, "description"))
            url = _child_text(item, "link")
            entry_date = _parse_date(_child_text(item, "pubDate"))
            entries.append({"title": title, "contents": contents, "url": url, "date": entry_date})
        elif tag == "entry":  # Atom
            title = _child_text(item, "title")
            contents = _clean_html(_child_text(item, "content") or _child_text(item, "summary"))
            url = _atom_link(item)
            entry_date = _parse_date(_child_text(item, "updated") or _child_text(item, "published"))
            entries.append({"title": title, "contents": contents, "url": url, "date": entry_date})

    return entries


# ---------------------------------------------------------------------------
# HTML fallback (plain patch-notes page, not a feed)
# ---------------------------------------------------------------------------

_TITLE_TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _parse_html_page(html_text: str, url: str) -> list[dict]:
    """
    Fallback for a plain (non-feed) patch-notes page: treat the whole page as
    one snapshot entry dated "today" (static pages rarely expose per-post
    dates in a generically parseable way).

    Note: write_patch_event's (game_id, source_url) idempotency means a given
    static URL only ever records once until that exact URL changes. Studios
    that publish many distinct updates behind one evergreen URL are better
    served by adding an RSS/Atom feed URL to GAME_PATCH_PAGES instead of a
    single patch-page URL; this fallback is a best-effort snapshot, not a
    full scraper.
    """
    title_match = _TITLE_TAG_RE.search(html_text or "")
    title = _clean_html(title_match.group(1)) if title_match else ""
    body = _clean_html(html_text)[:_MAX_CONTENTS_CHARS]
    today = datetime.now(timezone.utc).date().isoformat()
    return [{"title": title, "contents": body, "url": url, "date": f"{today}T00:00:00+00:00"}]


def _looks_like_feed(content_type: str, text: str) -> bool:
    content_type = (content_type or "").lower()
    if any(marker in content_type for marker in ("xml", "rss", "atom")):
        return True
    head = (text or "").lstrip()[:300].lower()
    return head.startswith("<?xml") or "<rss" in head or "<feed" in head


def _fetch_raw_entries(
    url: str,
    limiter: RateLimiter,
    session: requests.Session,
) -> list[dict]:
    limiter.wait()
    resp = session.get(url, timeout=15, headers={"User-Agent": _USER_AGENT})
    resp.raise_for_status()
    text = resp.text
    if _looks_like_feed(resp.headers.get("Content-Type", ""), text):
        entries = _parse_feed(text)
        if entries:
            return entries
    return _parse_html_page(text, url)


# ---------------------------------------------------------------------------
# Caching wrapper (mirrors CachedRedditSource: cache raw entries, serve stale
# on failure instead of raising -- one studio's blog being unreachable must
# not fail the whole patch_notes run)
# ---------------------------------------------------------------------------

class CachedBlogSource:
    def __init__(
        self,
        cache: ApiCache,
        limiter: RateLimiter | None = None,
        session: requests.Session | None = None,
        ttl_hours: float = 12.0,
    ) -> None:
        self.cache = cache
        self.limiter = limiter or RateLimiter()
        self.session = session or requests.Session()
        self.ttl_hours = ttl_hours

    def fetch_raw_entries(self, url: str) -> list[dict]:
        key = f"entries:{url}"
        fresh = self.cache.get(key, max_age_hours=self.ttl_hours)
        if fresh is not None:
            return fresh
        try:
            entries = _fetch_raw_entries(url, self.limiter, self.session)
            self.cache.set(key, entries)
            return entries
        except Exception:
            stale = self.cache.get(key)  # no TTL: stale is better than empty
            if stale is not None:
                logger.warning("fetch failed for %s; serving stale cache", url)
                return stale
            raise


# ---------------------------------------------------------------------------
# Public entry point -- same output shape as steam_news_client.get_recent_news
# ---------------------------------------------------------------------------

def get_recent_entries(
    source: CachedBlogSource,
    url: str,
    days_back: int = 45,
) -> list[dict]:
    """
    Return recent patch-like entries from one blog/patch-page URL, in the
    same shape worker.py already expects from get_recent_news:
    {"date", "title", "contents", "url", "patch_type"}.
    """
    raw_entries = source.fetch_raw_entries(url)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    items: list[dict] = []
    for raw in raw_entries:
        date_str = raw.get("date")
        if not date_str:
            continue
        try:
            published = datetime.fromisoformat(date_str)
        except ValueError:
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        if published < cutoff:
            continue

        title = raw.get("title") or ""
        contents = raw.get("contents") or ""
        if not looks_like_update(title, contents):
            continue

        items.append(
            {
                "date": published.date().isoformat(),
                "title": title,
                "contents": contents,
                "url": raw.get("url") or url,
                "patch_type": classify_patch(title, contents),
            }
        )

    return sorted(items, key=lambda item: item["date"])
