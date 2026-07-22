"""
Reddit adapter with env-var-gated alternate egress.

Default (zero new env vars set): unauthenticated public .json endpoints, exactly as
before — rate-limited to ~6-7 req/min to stay under Reddit's ~10/min unauthenticated
ceiling. Confirmed 2026-07-06: this path is now hit with a static 403 WAF block from
GitHub Actions' datacenter IPs on every endpoint, every run (an IP-reputation block,
not throttling). Two optional, opt-in alternate-egress paths exist and are tried
first when configured:

    1. OAuth (REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET + REDDIT_REFRESH_TOKEN, all
       three required together) -- OAuthRedditSource, plain `requests`, no `praw`.
    2. Standard HTTP/HTTPS proxy (REDDIT_PROXY_URL) -- ProxiedJsonRedditSource, a
       thin JsonRedditSource subclass that only swaps the session's proxies.
       REDDIT_PROXY_INSECURE_SSL (optional, same truthy-string convention as
       REDDIT_SOURCE_PAUSED below) disables TLS verification on this leaf's
       session -- needed only for a proxy that does TLS interception, confirmed
       required for ScrapeOps (live-tested 2026-07-13; also needs its proxy
       URL's username to carry ScrapeOps' own `residential=true` flag to avoid
       its datacenter pool, which gets stonewalled by Reddit the same way
       GitHub Actions' IPs are -- a config-value concern, not code).

Caching and graceful degradation are handled by CachedRedditSource + RedditCache,
one per-leaf cache namespace ("reddit" / "reddit_proxy" / "reddit_oauth") so a
working path's stale-serve can never mask another path's real health.

Architecture (default, no new env vars set -- identical object graph as before):
    worker.py
        └── build_reddit_source(cache_factory) -> CachedRedditSource(JsonRedditSource(), cache)
                └── on RedditBlocked: serve stale cache, or re-raise if cache empty
        └── build_subreddit_resolver() -> JsonRedditSource() (uncached; results are
                cached separately by cached_resolve_subreddit()/lookup_cache in worker.py)

Architecture (any of the 4 env vars set):
    build_reddit_source(cache_factory) -> FirstAvailableRedditSource([
        CachedRedditSource(OAuthRedditSource, cache_factory("reddit_oauth")),   # if configured
        CachedRedditSource(ProxiedJsonRedditSource, cache_factory("reddit_proxy")),  # if configured
        CachedRedditSource(JsonRedditSource(), cache_factory("reddit")),        # always last
    ])
    build_subreddit_resolver() -> same priority order, uncached leaves

Deliberate pause switch: REDDIT_SOURCE_PAUSED (any of "1"/"true"/"yes") makes
both factories return NullRedditSource() instead -- zero network calls, every
call raises RedditBlocked immediately. Added 2026-07-09 to stop paying for a
guaranteed-failed request on every game/candidate every run while no real
egress path (OAuth or proxy) is configured yet; unset it to resume once one is.

Added 2026-07-13, found live-testing the proxy path against ScrapeOps: (1) a
degraded proxy can return HTTP 200 with an in-band JSON error body instead of
an HTTP error status -- _get() now detects this shape (_looks_like_proxy_soft_failure)
and raises RedditBlocked so it hits the normal stale-cache/fallback path instead
of being silently parsed as "zero results found"; (2) a connection-level
exception (e.g. ProxyError from a rotating residential pool) raised by
session.get() itself, as opposed to a bad status code, previously had no
retry/backoff handling in either JsonRedditSource._get() or
OAuthRedditSource._get() and would crash the run -- both now retry with the
same backoff used for 429/503 before giving up.
"""

from __future__ import annotations

import difflib
import logging
import os
import random
import re
import time
from dataclasses import asdict, dataclass
from typing import Callable, Protocol

import requests

from agents.workers.sentiment.reddit_cache import RedditCache

logger = logging.getLogger("reddit_source")

_USER_AGENT = "github-actions:games-intel-platform:v0.1 (by /u/cgonztx)"
_BASE = "https://www.reddit.com"
_OAUTH_BASE = "https://oauth.reddit.com"
_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"


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


class NullRedditSource:
    """Returned by build_reddit_source()/build_subreddit_resolver() when
    REDDIT_SOURCE_PAUSED is set. Raises RedditBlocked immediately on every call,
    with zero network I/O -- callers already treat RedditBlocked as a normal
    degrade-gracefully signal, so pausing needs no changes in worker.py or
    discovery/worker.py. Deliberate scope pause (2026-07-09): the unauthenticated
    path is a confirmed-permanent WAF block and no proxy/OAuth egress is
    configured yet, so every attempt was a guaranteed-failed request; this stops
    paying that cost every run until a real egress path is set up."""

    def fetch_posts(self, *args, **kwargs) -> list[RedditPost]:
        raise RedditBlocked("Reddit source paused (REDDIT_SOURCE_PAUSED is set)")

    def fetch_comments(self, *args, **kwargs) -> list[RedditComment]:
        raise RedditBlocked("Reddit source paused (REDDIT_SOURCE_PAUSED is set)")

    def resolve_subreddit(self, game_title: str) -> str | None:
        raise RedditBlocked("Reddit source paused (REDDIT_SOURCE_PAUSED is set)")


def _reddit_source_paused() -> bool:
    return os.environ.get("REDDIT_SOURCE_PAUSED", "").strip().lower() in ("1", "true", "yes")


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


class SubredditResolver(Protocol):
    def resolve_subreddit(self, game_title: str) -> str | None: ...


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
# Shared response parsers (module-level so JsonRedditSource, ProxiedJsonRedditSource,
# and OAuthRedditSource all reuse the exact same parsing logic without duplication)
# ---------------------------------------------------------------------------

def _parse_post_listing(data: dict, subreddit: str) -> list[RedditPost]:
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


def _walk_comments(children: list, post_id: str, out: list[RedditComment]) -> None:
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
            _walk_comments(
                replies.get("data", {}).get("children", []),
                post_id,
                out,
            )


def _parse_comment_listing(data, post_id: str) -> list[RedditComment]:
    if not isinstance(data, list) or len(data) < 2:
        return []
    out: list[RedditComment] = []
    _walk_comments(data[1].get("data", {}).get("children", []), post_id, out)
    return out


def _parse_subreddit_search(data: dict, game_title: str) -> str | None:
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


def _looks_like_proxy_soft_failure(data) -> bool:
    """A real Reddit listing/search response is always a list (comments) or a
    dict containing "data" (posts/search). A proxy's in-band error body (e.g.
    ScrapeOps' {"status": "We couldn't retrieve a successful response..."})
    is a bare dict with a string "status" key and no "data" key -- a shape
    that never occurs in a genuine Reddit response. Confirmed live 2026-07-13:
    ScrapeOps returns HTTP 200 with this body when its proxy pool can't
    reach Reddit, which would otherwise be silently parsed as "zero results"
    instead of triggering the RedditBlocked fallback/stale-cache path."""
    return isinstance(data, dict) and "data" not in data and isinstance(data.get("status"), str)


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
            try:
                resp = self.session.get(url, params=params, timeout=15)
            except requests.exceptions.RequestException as exc:
                backoff = 2 ** attempt
                logger.warning(
                    "network error on %s (attempt %d/%d): %s; backoff %.0fs",
                    url, attempt, self.max_retries, exc, backoff,
                )
                time.sleep(backoff)
                continue
            if resp.status_code == 200:
                payload = resp.json()
                if _looks_like_proxy_soft_failure(payload):
                    logger.warning("proxy soft-failure body on %s: %r", url, payload)
                    raise RedditBlocked(f"proxy soft-failure on {url}")
                return payload
            if resp.status_code in (429, 503):
                backoff = float(resp.headers.get("Retry-After", 2 ** attempt))
                logger.warning(
                    "throttled %s; backoff %.0fs (attempt %d/%d)",
                    resp.status_code, backoff, attempt, self.max_retries,
                )
                time.sleep(backoff)
                continue
            if resp.status_code in (403, 451):
                logger.warning("blocked: %s on %s", resp.status_code, url)
                raise RedditBlocked(f"{resp.status_code} on {url}")
            resp.raise_for_status()
        logger.warning("retries exhausted for %s", url)
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
        return _parse_post_listing(data, subreddit)

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
        return _parse_comment_listing(data, post_id)

    def resolve_subreddit(self, game_title: str) -> str | None:
        """
        Search Reddit for the best-matching subreddit (similarity >= 0.70).
        Raises RedditBlocked on throttle/block instead of swallowing it, so
        callers (cached_resolve_subreddit) can tell a transient failure apart
        from a confirmed no-match and avoid caching the former as the latter.
        """
        data = self._get(
            "/subreddits/search.json",
            params={"q": game_title, "limit": 5},
        )
        return _parse_subreddit_search(data, game_title)


# ---------------------------------------------------------------------------
# Alternate egress #1: same unauthenticated .json path, routed through a proxy
# ---------------------------------------------------------------------------

class ProxiedJsonRedditSource(JsonRedditSource):
    """Same as JsonRedditSource, routed through a standard HTTP/HTTPS proxy.
    No SOCKS5 -- requests.Session supports http(s) proxies natively, zero new deps.
    verify=False is only needed for a proxy that does TLS interception to
    inspect the tunneled request (confirmed required for ScrapeOps, live-tested
    2026-07-13 -- fails with CERTIFICATE_VERIFY_FAILED otherwise); defaults to
    True (secure) since a plain passthrough proxy needs no special handling."""

    def __init__(
        self,
        proxy_url: str,
        user_agent: str = _USER_AGENT,
        limiter: RateLimiter | None = None,
        max_retries: int = 3,
        session: requests.Session | None = None,
        verify: bool = True,
    ) -> None:
        session = session or requests.Session()
        session.proxies.update({"http": proxy_url, "https": proxy_url})
        session.verify = verify
        super().__init__(
            user_agent=user_agent,
            limiter=limiter,
            max_retries=max_retries,
            session=session,
        )


# ---------------------------------------------------------------------------
# Alternate egress #2: Reddit's OAuth2 refresh-token flow via plain requests
# ---------------------------------------------------------------------------

class OAuthRedditSource:
    """Reddit's OAuth2 refresh-token flow via plain `requests` -- no `praw`,
    matching alpaca_trading_client.py / email_delivery.py's no-SDK convention.
    Requires REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET / REDDIT_REFRESH_TOKEN.
    Caches the access token in memory for its expires_in duration (minus a 60s
    safety margin), not fetched per request."""

    def __init__(
        self,
        client_id,
        client_secret,
        refresh_token,
        user_agent=_USER_AGENT,
        limiter=None,
        max_retries=3,
        session=None,
    ):
        self.client_id, self.client_secret, self.refresh_token = client_id, client_secret, refresh_token
        self.user_agent = user_agent
        self.limiter = limiter or RateLimiter(min_interval=0.8, jitter=0.2)
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    def _ensure_token(self) -> str:
        if self._access_token is not None and time.monotonic() < self._token_expires_at:
            return self._access_token
        try:
            resp = self.session.post(
                _TOKEN_URL,
                auth=(self.client_id, self.client_secret),
                data={"grant_type": "refresh_token", "refresh_token": self.refresh_token},
                headers={"User-Agent": self.user_agent},
                timeout=15,
            )
        except requests.exceptions.RequestException as exc:
            logger.warning("oauth token fetch failed: %s", exc)
            raise RedditBlocked(f"oauth token fetch error: {exc}") from exc
        if resp.status_code != 200:
            logger.warning("oauth token fetch non-200: %s", resp.status_code)
            raise RedditBlocked(f"oauth token fetch returned {resp.status_code}")
        payload = resp.json()
        token = payload.get("access_token")
        if not token:
            raise RedditBlocked("oauth token response missing access_token")
        self._access_token = token
        self._token_expires_at = time.monotonic() + max(payload.get("expires_in", 3600) - 60, 30)
        return token

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{_OAUTH_BASE}{path}"
        for attempt in range(1, self.max_retries + 1):
            token = self._ensure_token()
            self.limiter.wait()
            try:
                resp = self.session.get(
                    url,
                    params=params,
                    timeout=15,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except requests.exceptions.RequestException as exc:
                backoff = 2 ** attempt
                logger.warning(
                    "oauth network error on %s (attempt %d/%d): %s; backoff %.0fs",
                    url, attempt, self.max_retries, exc, backoff,
                )
                time.sleep(backoff)
                continue
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 401:
                logger.warning(
                    "oauth 401 on %s; forcing token refresh (attempt %d/%d)",
                    url, attempt, self.max_retries,
                )
                self._access_token, self._token_expires_at = None, 0.0
                continue
            if resp.status_code in (429, 503):
                backoff = float(resp.headers.get("Retry-After", 2 ** attempt))
                logger.warning(
                    "oauth throttled %s; backoff %.0fs (attempt %d/%d)",
                    resp.status_code, backoff, attempt, self.max_retries,
                )
                time.sleep(backoff)
                continue
            if resp.status_code in (403, 451):
                logger.warning("oauth blocked: %s on %s", resp.status_code, url)
                raise RedditBlocked(f"{resp.status_code} on {url}")
            resp.raise_for_status()
        logger.warning("oauth retries exhausted for %s", url)
        raise RedditBlocked(f"retries exhausted for {url}")

    def fetch_posts(self, subreddit, sort="top", timeframe="week", limit=100):
        data = self._get(
            f"/r/{subreddit}/{sort}",
            params={"t": timeframe, "limit": min(limit, 100)},
        )
        return _parse_post_listing(data, subreddit)

    def fetch_comments(self, post_id, subreddit, limit=200):
        return _parse_comment_listing(
            self._get(f"/r/{subreddit}/comments/{post_id}", params={"limit": limit}),
            post_id,
        )

    def resolve_subreddit(self, game_title):
        return _parse_subreddit_search(
            self._get("/subreddits/search", params={"q": game_title, "limit": 5}),
            game_title,
        )


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

    def resolve_subreddit(self, game_title: str) -> str | None:
        last: RedditBlocked | None = None
        for src in self.sources:
            try:
                return src.resolve_subreddit(game_title)
            except RedditBlocked as e:
                last = e
        raise last or RedditBlocked("no source available")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def _build_leaf_sources() -> list[RedditSource]:
    """Priority: OAuth (all 3 vars required together, partial = treated as absent)
    -> Proxy -> unauthenticated JsonRedditSource (always last, unconditional --
    the sole active path when no new env vars are set)."""
    leaves: list[RedditSource] = []
    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    refresh_token = os.environ.get("REDDIT_REFRESH_TOKEN")
    if client_id and client_secret and refresh_token:
        leaves.append(OAuthRedditSource(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
        ))
    proxy_url = os.environ.get("REDDIT_PROXY_URL")
    if proxy_url:
        insecure = os.environ.get("REDDIT_PROXY_INSECURE_SSL", "").strip().lower() in ("1", "true", "yes")
        leaves.append(ProxiedJsonRedditSource(proxy_url=proxy_url, verify=not insecure))
    leaves.append(JsonRedditSource())
    return leaves


_CACHE_NAMESPACE_BY_TYPE = {
    JsonRedditSource: "reddit",
    ProxiedJsonRedditSource: "reddit_proxy",
    OAuthRedditSource: "reddit_oauth",
}


def build_reddit_source(cache_factory: Callable[[str], RedditCache]) -> RedditSource:
    """cache_factory: Callable[[str], RedditCache]. Returns a single CachedRedditSource
    when only JsonRedditSource is active (default -- identical object graph to before
    this change), else a FirstAvailableRedditSource of per-leaf CachedRedditSource wraps,
    each with its own cache namespace. Returns NullRedditSource() instead, with no
    leaves built at all, when REDDIT_SOURCE_PAUSED is set."""
    if _reddit_source_paused():
        return NullRedditSource()
    leaves = _build_leaf_sources()
    wrapped = [
        CachedRedditSource(leaf, cache_factory(_CACHE_NAMESPACE_BY_TYPE[type(leaf)]))
        for leaf in leaves
    ]
    return wrapped[0] if len(wrapped) == 1 else FirstAvailableRedditSource(wrapped)


def build_subreddit_resolver() -> SubredditResolver:
    """Uncached resolver chain mirroring build_reddit_source's env-var priority --
    resolution results are already cached separately by cached_resolve_subreddit().
    Returns NullRedditSource() when REDDIT_SOURCE_PAUSED is set, same as
    build_reddit_source()."""
    if _reddit_source_paused():
        return NullRedditSource()
    leaves = _build_leaf_sources()
    return leaves[0] if len(leaves) == 1 else FirstAvailableRedditSource(leaves)


# ---------------------------------------------------------------------------
# Subreddit resolution with 30-day cache
# ---------------------------------------------------------------------------

# Subreddit-resolution cache lifetime. Real matches also persist permanently
# in watchlist.subreddit (so they never re-resolve); this TTL only governs how
# often a *no-match* game is re-checked. A game with no subreddit almost never
# gains one, so re-resolving all ~3,400 no-match games monthly was mostly
# wasted ScrapeOps spend -- bumped 30d -> 90d (2026-07-22) to cut that
# re-resolution cost ~3x and keep monthly Reddit proxy usage under budget,
# while still eventually self-healing if a game does get a community.
_LOOKUP_TTL_HOURS = 24 * 90


def cached_resolve_subreddit(
    game_title: str,
    resolver: SubredditResolver,
    lookup_cache: RedditCache,
) -> str | None:
    """
    Resolve a game title to its best-matching subreddit name, caching the result
    for 30 days so we don't re-query ~3k games on every weekly run.

    Stores [display_name] on a hit, [] on a confirmed no-match — so both
    positive and negative results are cached and won't be re-queried. Raises
    RedditBlocked on throttle/block instead: that's a transient failure, not a
    confirmed no-match, so it must not be cached or persisted as one (callers
    should catch it and simply retry on a later run).
    """
    key = _normalize(game_title)
    cached = lookup_cache.get(key, max_age_hours=_LOOKUP_TTL_HOURS)
    if cached is not None:
        return cached[0] if cached else None

    result = resolver.resolve_subreddit(game_title)
    lookup_cache.set(key, [result] if result else [])
    return result
