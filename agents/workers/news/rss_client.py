"""
Curated games-press RSS/Atom adapter for the news worker.

Tier-1 sources (official outlet feeds, see docs/data-source-risk-register.md)
but still wrapped with a rate limiter + api_cache-backed fetch + graceful
degradation, mirroring the shape of agents/workers/sentiment/reddit_source.py
and agents/workers/patch_notes/blog_client.py. The feed parser here is a
deliberately independent, minimal fork of blog_client.py's -- worker packages
don't cross-import (see blog_client.py's own docstring) -- trimmed to what
relevance matching needs (title/snippet/url/date/domain), not full article
bodies, per Key Architecture Rule 1 (no raw post bodies cross back to the
orchestrator).
"""

from __future__ import annotations

import logging
import random
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests

from agents.http_retry import request_with_retry
from database.api_cache import ApiCache

logger = logging.getLogger("rss_client")

_USER_AGENT = "games-intel-platform/0.1 (news worker; contact: cgonztx@gmail.com)"
_MAX_SNIPPET_CHARS = 500

# Curated general games-press outlets (not per-game -- see entity_matcher.py
# for how articles from these outlet-wide feeds get matched to a watchlist
# game). Verify these paths periodically; outlets occasionally move feeds.
CURATED_FEEDS = [
    "https://www.gamesindustry.biz/feed",
    "https://www.eurogamer.net/feed",
    "https://www.videogameschronicle.com/feed/",
    "https://www.pcgamer.com/rss/",
    "https://www.rockpapershotgun.com/feed",
    "https://www.vg247.com/feed",
    "https://www.gamedeveloper.com/rss.xml",
]


# ---------------------------------------------------------------------------
# Rate limiter (local re-implementation -- kept local per the no-cross-import
# convention; blog_client.py duplicates the same class for the same reason)
# ---------------------------------------------------------------------------

class RateLimiter:
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


def _clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


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


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc
    except ValueError:
        return ""


def _parse_feed(xml_text: str) -> list[dict]:
    """Parse an RSS or Atom feed into entries: url/title/snippet/published_at/
    domain. Returns [] if the text isn't parseable XML."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    entries: list[dict] = []
    for item in root.iter():
        tag = _local(item.tag)
        if tag == "item":  # RSS 2.0
            title = _child_text(item, "title")
            snippet = _clean_html(_child_text(item, "description"))[:_MAX_SNIPPET_CHARS]
            url = _child_text(item, "link")
            published_at = _parse_date(_child_text(item, "pubDate"))
        elif tag == "entry":  # Atom
            title = _child_text(item, "title")
            snippet = _clean_html(_child_text(item, "summary") or _child_text(item, "content"))[:_MAX_SNIPPET_CHARS]
            url = _atom_link(item)
            published_at = _parse_date(_child_text(item, "updated") or _child_text(item, "published"))
        else:
            continue
        if not url:
            continue
        entries.append(
            {
                "url": url,
                "title": title,
                "snippet": snippet,
                "published_at": published_at,
                "domain": _domain(url),
            }
        )

    return entries


def _fetch_feed_entries(url: str, limiter: RateLimiter, session: requests.Session) -> list[dict]:
    limiter.wait()
    resp = request_with_retry(session.get, url, timeout=15, headers={"User-Agent": _USER_AGENT})
    resp.raise_for_status()
    return _parse_feed(resp.text)


# ---------------------------------------------------------------------------
# Caching wrapper (serve stale on failure instead of raising -- one dead
# outlet feed must not fail the whole news run)
# ---------------------------------------------------------------------------

class CachedRssSource:
    def __init__(
        self,
        limiter: RateLimiter | None = None,
        session: requests.Session | None = None,
        cache: ApiCache | None = None,
        ttl_hours: float = 12.0,
    ) -> None:
        self.limiter = limiter or RateLimiter()
        self.session = session or requests.Session()
        self.cache = cache
        self.ttl_hours = ttl_hours

    def fetch_feed(self, url: str) -> list[dict]:
        key = f"feed:{url}"
        fresh = self.cache.get(key, max_age_hours=self.ttl_hours)
        if fresh is not None:
            return fresh
        try:
            entries = _fetch_feed_entries(url, self.limiter, self.session)
            self.cache.set(key, entries)
            return entries
        except Exception:
            stale = self.cache.get(key)  # no TTL: stale is better than empty
            if stale is not None:
                logger.warning("fetch failed for %s; serving stale cache", url)
                return stale
            raise


def fetch_curated_feeds(source: CachedRssSource, feeds: list[str] = CURATED_FEEDS) -> list[dict]:
    """Pull all curated outlet feeds. One outlet's failure only skips that
    outlet for this run -- never fatal for the whole news worker."""
    articles: list[dict] = []
    for url in feeds:
        try:
            articles.extend(source.fetch_feed(url))
        except Exception as exc:
            logger.warning("skipping feed %s this run: %s", url, exc)
    return articles
