"""
Unit tests for agents/workers/sentiment/reddit_source.py.

No live network calls anywhere in this file: every requests.Session is a local
_FakeSession that pops pre-scripted _FakeResponse objects (or raises a scripted
exception) in call order. Mirrors tests/test_returns_tracker.py /
tests/test_execution_agent.py's house convention: plain recording fakes, no
mocking library, assertion-raising fakes to prove a path is never invoked.

The factory tests (_build_leaf_sources / build_reddit_source / build_subreddit_resolver)
are the most important in this file: they prove that with all four new env vars
unset, the object graph returned is bit-for-bit identical to the pre-existing
behavior (a bare CachedRedditSource wrapping a bare JsonRedditSource) -- i.e. this
change is a zero-behavior-change no-op until the user opts in.
"""

from __future__ import annotations

import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from agents.workers.sentiment.reddit_cache import InMemoryRedditCache
from agents.workers.sentiment.reddit_source import (
    CachedRedditSource,
    FirstAvailableRedditSource,
    JsonRedditSource,
    NullRedditSource,
    OAuthRedditSource,
    ProxiedJsonRedditSource,
    RateLimiter,
    RedditBlocked,
    RedditPost,
    _POSTS_TTL_HOURS,
    _build_leaf_sources,
    build_reddit_source,
    build_subreddit_resolver,
)

_ENV_VARS = (
    "REDDIT_CLIENT_ID",
    "REDDIT_CLIENT_SECRET",
    "REDDIT_REFRESH_TOKEN",
    "REDDIT_PROXY_URL",
    "REDDIT_PROXY_INSECURE_SSL",
    "REDDIT_SOURCE_PAUSED",
)


def _clear_env(monkeypatch):
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code, json_data=None, headers=None):
        self.status_code = status_code
        self._json_data = {} if json_data is None else json_data
        self.headers = headers or {}

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


class _FakeSession:
    """Records .get/.post calls in order; returns (or raises) pre-scripted
    responses popped in call order. `proxies` / `headers` are plain dicts so
    ProxiedJsonRedditSource's session.proxies.update(...) works unmodified."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls: list[tuple[str, tuple, dict]] = []
        self.proxies: dict = {}
        self.headers: dict = {}

    def _pop(self):
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def get(self, *args, **kwargs):
        self.calls.append(("get", args, kwargs))
        return self._pop()

    def post(self, *args, **kwargs):
        self.calls.append(("post", args, kwargs))
        return self._pop()


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _post_child(id_="p1", title="Great game", selftext="loved it", stickied=False, score=10):
    return {
        "kind": "t3",
        "data": {
            "id": id_,
            "title": title,
            "selftext": selftext,
            "author": "alice",
            "score": score,
            "num_comments": 5,
            "created_utc": 1700000000.0,
            "permalink": f"/r/test/comments/{id_}/",
            "url": f"https://reddit.com/r/test/comments/{id_}/",
            "stickied": stickied,
        },
    }


def _post_listing(children):
    return {"data": {"children": children}}


def _comment_node(id_="c1", body="nice", replies=None, kind="t1"):
    d = {"id": id_, "body": body, "author": "bob", "score": 3, "created_utc": 1700000001.0}
    if replies is not None:
        d["replies"] = replies
    return {"kind": kind, "data": d}


def _comments_listing(children):
    return [{"data": {"children": []}}, {"data": {"children": children}}]


def _subreddit_search(names):
    return {"data": {"children": [{"data": {"display_name": n}} for n in names]}}


def _sample_post(id_="p1"):
    return RedditPost(
        id=id_, subreddit="testsub", title="t", selftext="s", author="a",
        score=1, num_comments=0, created_utc=0.0, permalink="", url="",
    )


def _fast_limiter():
    return RateLimiter(min_interval=0.0, jitter=0.0)


def _make_json_source(responses, cls=JsonRedditSource, **kwargs):
    session = _FakeSession(responses)
    source = cls(limiter=_fast_limiter(), session=session, **kwargs)
    return source, session


# ---------------------------------------------------------------------------
# JsonRedditSource (post-refactor -- must show zero regression)
# ---------------------------------------------------------------------------

def test_json_source_fetch_posts_parses_and_skips_stickied():
    children = [_post_child(id_="p1", stickied=False), _post_child(id_="p2", stickied=True)]
    source, _ = _make_json_source([_FakeResponse(200, _post_listing(children))])
    posts = source.fetch_posts("testsub")
    assert [p.id for p in posts] == ["p1"]
    assert isinstance(posts[0], RedditPost)


def test_json_source_403_raises_reddit_blocked():
    source, _ = _make_json_source([_FakeResponse(403, {})])
    try:
        source.fetch_posts("testsub")
        assert False, "expected RedditBlocked"
    except RedditBlocked:
        pass


def test_json_source_451_raises_reddit_blocked():
    source, _ = _make_json_source([_FakeResponse(451, {})])
    try:
        source.fetch_posts("testsub")
        assert False, "expected RedditBlocked"
    except RedditBlocked:
        pass


def test_json_source_429_then_200_retries_and_respects_retry_after(monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    responses = [
        _FakeResponse(429, {}, headers={"Retry-After": "5"}),
        _FakeResponse(200, _post_listing([_post_child()])),
    ]
    source, _ = _make_json_source(responses)
    posts = source.fetch_posts("testsub")
    assert len(posts) == 1
    assert sleeps == [5.0]


def test_json_source_retries_exhausted_all_503_raises_reddit_blocked(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    responses = [_FakeResponse(503, {}) for _ in range(3)]
    source, _ = _make_json_source(responses)
    try:
        source.fetch_posts("testsub")
        assert False, "expected RedditBlocked"
    except RedditBlocked:
        pass


def test_json_source_comment_walk_handles_nested_replies_and_skips_other_kinds():
    nested = _comment_node(id_="c2", body="reply")
    top = _comment_node(id_="c1", body="top", replies={"data": {"children": [nested]}})
    more_node = {"kind": "more", "data": {}}
    listing = _comments_listing([top, more_node])
    source, _ = _make_json_source([_FakeResponse(200, listing)])
    comments = source.fetch_comments("p1", "testsub")
    assert [c.id for c in comments] == ["c1", "c2"]


def test_json_source_malformed_comment_response_returns_empty_list():
    source, _ = _make_json_source([_FakeResponse(200, {"not": "a list"})])
    comments = source.fetch_comments("p1", "testsub")
    assert comments == []


def test_json_source_resolve_subreddit_returns_match_at_or_above_threshold():
    source, _ = _make_json_source([_FakeResponse(200, _subreddit_search(["Fortnite"]))])
    assert source.resolve_subreddit("Fortnite") == "Fortnite"


def test_json_source_resolve_subreddit_returns_none_below_threshold():
    source, _ = _make_json_source([_FakeResponse(200, _subreddit_search(["zzz_totally_unrelated_zzz"]))])
    assert source.resolve_subreddit("Fortnite") is None


def _proxy_soft_failure_body():
    """Shape ScrapeOps returns with HTTP 200 when its proxy pool can't reach
    Reddit -- a bare dict with a string "status" key and no "data" key."""
    return {"status": "We couldn't retrieve a successful response for this request."}


def test_json_source_200_with_proxy_soft_failure_body_raises_reddit_blocked():
    source, _ = _make_json_source([_FakeResponse(200, _proxy_soft_failure_body())])
    try:
        source.fetch_posts("testsub")
        assert False, "expected RedditBlocked"
    except RedditBlocked:
        pass


def test_json_source_200_empty_listing_does_not_false_positive_as_soft_failure():
    # Regression guard: a real empty listing ({"data": {"children": []}}) has
    # a "data" key, so it must still parse as zero posts, not RedditBlocked.
    source, _ = _make_json_source([_FakeResponse(200, _post_listing([]))])
    posts = source.fetch_posts("testsub")
    assert posts == []


def test_json_source_connection_error_then_200_retries_and_succeeds(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    responses = [
        requests.exceptions.ConnectionError("reset"),
        _FakeResponse(200, _post_listing([_post_child()])),
    ]
    source, _ = _make_json_source(responses)
    posts = source.fetch_posts("testsub")
    assert len(posts) == 1


def test_json_source_proxy_error_retries_exhausted_raises_reddit_blocked(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    responses = [requests.exceptions.ProxyError("no route") for _ in range(3)]
    source, _ = _make_json_source(responses)
    try:
        source.fetch_posts("testsub")
        assert False, "expected RedditBlocked"
    except RedditBlocked:
        pass


# ---------------------------------------------------------------------------
# CachedRedditSource
# ---------------------------------------------------------------------------

class _BoomInner:
    def fetch_posts(self, *a, **k):
        raise AssertionError("inner must not be called on a fresh cache hit")

    def fetch_comments(self, *a, **k):
        raise AssertionError("inner must not be called on a fresh cache hit")


class _RecordingInner:
    def __init__(self, posts=None, comments=None):
        self._posts = posts or []
        self._comments = comments or []
        self.fetch_posts_calls = 0
        self.fetch_comments_calls = 0

    def fetch_posts(self, *a, **k):
        self.fetch_posts_calls += 1
        return self._posts

    def fetch_comments(self, *a, **k):
        self.fetch_comments_calls += 1
        return self._comments


class _BlockedInner:
    def fetch_posts(self, *a, **k):
        raise RedditBlocked("blocked")

    def fetch_comments(self, *a, **k):
        raise RedditBlocked("blocked")


def test_cached_source_fresh_hit_never_calls_inner():
    cache = InMemoryRedditCache()
    cache.set("posts:testsub:top:week", [asdict(_sample_post("cached1"))])
    source = CachedRedditSource(_BoomInner(), cache)
    posts = source.fetch_posts("testsub")
    assert [p.id for p in posts] == ["cached1"]


def test_cached_source_miss_fetches_and_writes_to_cache():
    cache = InMemoryRedditCache()
    inner = _RecordingInner(posts=[_sample_post("fresh1")])
    source = CachedRedditSource(inner, cache)
    posts = source.fetch_posts("testsub")
    assert [p.id for p in posts] == ["fresh1"]
    assert inner.fetch_posts_calls == 1
    assert cache.get("posts:testsub:top:week") is not None


def test_cached_source_blocked_with_stale_cache_serves_stale():
    cache = InMemoryRedditCache()
    cache.set("posts:testsub:top:week", [asdict(_sample_post("stale1"))])
    # ttl_hours=0 forces the fresh-check to always miss (any elapsed time > 0
    # hours), so the block path is exercised and stale-serve is proven.
    source = CachedRedditSource(_BlockedInner(), cache, ttl_hours=0)
    posts = source.fetch_posts("testsub")
    assert [p.id for p in posts] == ["stale1"]


def test_cached_source_blocked_with_no_stale_cache_reraises():
    cache = InMemoryRedditCache()
    source = CachedRedditSource(_BlockedInner(), cache)
    try:
        source.fetch_posts("testsub")
        assert False, "expected RedditBlocked"
    except RedditBlocked:
        pass


# ---------------------------------------------------------------------------
# FirstAvailableRedditSource
# ---------------------------------------------------------------------------

class _AlwaysBlocked:
    def fetch_posts(self, *a, **k):
        raise RedditBlocked("blocked")

    def fetch_comments(self, *a, **k):
        raise RedditBlocked("blocked")

    def resolve_subreddit(self, *a, **k):
        raise RedditBlocked("blocked")


class _AlwaysWorks:
    def __init__(self, posts=None, subreddit=None):
        self._posts = posts or []
        self._subreddit = subreddit

    def fetch_posts(self, *a, **k):
        return self._posts

    def fetch_comments(self, *a, **k):
        return []

    def resolve_subreddit(self, *a, **k):
        return self._subreddit


def test_first_available_falls_through_to_second_source_on_blocked():
    chain = FirstAvailableRedditSource([_AlwaysBlocked(), _AlwaysWorks(posts=[_sample_post("p2")])])
    posts = chain.fetch_posts("testsub")
    assert [p.id for p in posts] == ["p2"]


def test_first_available_all_blocked_raises_last_exception():
    chain = FirstAvailableRedditSource([_AlwaysBlocked(), _AlwaysBlocked()])
    try:
        chain.fetch_posts("testsub")
        assert False, "expected RedditBlocked"
    except RedditBlocked:
        pass


def test_first_available_resolve_subreddit_falls_through_identically():
    chain = FirstAvailableRedditSource([_AlwaysBlocked(), _AlwaysWorks(subreddit="TestGame")])
    assert chain.resolve_subreddit("Test Game") == "TestGame"


# ---------------------------------------------------------------------------
# ProxiedJsonRedditSource
# ---------------------------------------------------------------------------

def test_proxied_source_applies_proxy_url_to_session():
    session = _FakeSession([_FakeResponse(200, _post_listing([_post_child()]))])
    source = ProxiedJsonRedditSource(
        proxy_url="http://proxy.example:8080", limiter=_fast_limiter(), session=session
    )
    assert session.proxies == {
        "http": "http://proxy.example:8080",
        "https": "http://proxy.example:8080",
    }
    posts = source.fetch_posts("testsub")
    assert len(posts) == 1


def test_proxied_source_inherits_403_and_429_handling_unchanged(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    session = _FakeSession([_FakeResponse(403, {})])
    source = ProxiedJsonRedditSource(
        proxy_url="http://proxy.example:8080", limiter=_fast_limiter(), session=session
    )
    try:
        source.fetch_posts("testsub")
        assert False, "expected RedditBlocked"
    except RedditBlocked:
        pass


def test_proxied_source_defaults_to_verify_true():
    session = _FakeSession([_FakeResponse(200, _post_listing([_post_child()]))])
    ProxiedJsonRedditSource(
        proxy_url="http://proxy.example:8080", limiter=_fast_limiter(), session=session
    )
    assert session.verify is True


def test_proxied_source_verify_false_sets_session_verify_false():
    session = _FakeSession([_FakeResponse(200, _post_listing([_post_child()]))])
    ProxiedJsonRedditSource(
        proxy_url="http://proxy.example:8080", limiter=_fast_limiter(), session=session, verify=False
    )
    assert session.verify is False


def test_proxied_source_inherits_soft_failure_detection_unchanged():
    session = _FakeSession([_FakeResponse(200, _proxy_soft_failure_body())])
    source = ProxiedJsonRedditSource(
        proxy_url="http://proxy.example:8080", limiter=_fast_limiter(), session=session
    )
    try:
        source.fetch_posts("testsub")
        assert False, "expected RedditBlocked"
    except RedditBlocked:
        pass


def test_proxied_source_inherits_connection_error_retry_unchanged(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    session = _FakeSession([
        requests.exceptions.ProxyError("reset"),
        _FakeResponse(200, _post_listing([_post_child()])),
    ])
    source = ProxiedJsonRedditSource(
        proxy_url="http://proxy.example:8080", limiter=_fast_limiter(), session=session
    )
    posts = source.fetch_posts("testsub")
    assert len(posts) == 1


# ---------------------------------------------------------------------------
# OAuthRedditSource
# ---------------------------------------------------------------------------

def _oauth_source(session, limiter=None):
    return OAuthRedditSource(
        client_id="cid",
        client_secret="csecret",
        refresh_token="rtoken",
        limiter=limiter or _fast_limiter(),
        session=session,
    )


def _post_calls(session):
    return [c for c in session.calls if c[0] == "post"]


def _get_calls(session):
    return [c for c in session.calls if c[0] == "get"]


def test_oauth_token_fetched_once_and_reused_across_calls(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    session = _FakeSession([
        _FakeResponse(200, {"access_token": "tok1", "expires_in": 3600}),
        _FakeResponse(200, _post_listing([_post_child()])),
        _FakeResponse(200, _post_listing([_post_child()])),
    ])
    source = _oauth_source(session)
    source.fetch_posts("testsub")
    source.fetch_posts("testsub")
    assert len(_post_calls(session)) == 1


def test_oauth_token_refetched_after_simulated_expiry(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    session = _FakeSession([
        _FakeResponse(200, {"access_token": "tok1", "expires_in": 3600}),
        _FakeResponse(200, _post_listing([_post_child()])),
        _FakeResponse(200, {"access_token": "tok2", "expires_in": 3600}),
        _FakeResponse(200, _post_listing([_post_child()])),
    ])
    source = _oauth_source(session)
    source.fetch_posts("testsub")
    future = time.monotonic() + 100000
    monkeypatch.setattr(time, "monotonic", lambda: future)
    source.fetch_posts("testsub")
    assert len(_post_calls(session)) == 2


def test_oauth_token_endpoint_non_200_raises_reddit_blocked():
    session = _FakeSession([_FakeResponse(500, {})])
    source = _oauth_source(session)
    try:
        source.fetch_posts("testsub")
        assert False, "expected RedditBlocked"
    except RedditBlocked:
        pass


def test_oauth_token_endpoint_network_exception_raises_reddit_blocked():
    session = _FakeSession([requests.exceptions.ConnectionError("boom")])
    source = _oauth_source(session)
    try:
        source.fetch_posts("testsub")
        assert False, "expected RedditBlocked"
    except RedditBlocked:
        pass


def test_oauth_single_401_forces_one_refresh_then_succeeds(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    session = _FakeSession([
        _FakeResponse(200, {"access_token": "tok1", "expires_in": 3600}),
        _FakeResponse(401, {}),
        _FakeResponse(200, {"access_token": "tok2", "expires_in": 3600}),
        _FakeResponse(200, _post_listing([_post_child()])),
    ])
    source = _oauth_source(session)
    posts = source.fetch_posts("testsub")
    assert len(posts) == 1
    assert len(_post_calls(session)) == 2


def test_oauth_403_raises_reddit_blocked():
    session = _FakeSession([
        _FakeResponse(200, {"access_token": "tok1", "expires_in": 3600}),
        _FakeResponse(403, {}),
    ])
    source = _oauth_source(session)
    try:
        source.fetch_posts("testsub")
        assert False, "expected RedditBlocked"
    except RedditBlocked:
        pass


def test_oauth_429_backoff_then_succeeds(monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    session = _FakeSession([
        _FakeResponse(200, {"access_token": "tok1", "expires_in": 3600}),
        _FakeResponse(429, {}, headers={"Retry-After": "3"}),
        _FakeResponse(200, _post_listing([_post_child()])),
    ])
    source = _oauth_source(session)
    posts = source.fetch_posts("testsub")
    assert len(posts) == 1
    assert sleeps == [3.0]


def test_oauth_fetch_path_has_no_json_suffix_and_sends_bearer_header(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    session = _FakeSession([
        _FakeResponse(200, {"access_token": "tok1", "expires_in": 3600}),
        _FakeResponse(200, _post_listing([_post_child()])),
    ])
    source = _oauth_source(session)
    source.fetch_posts("testsub")
    _, args, kwargs = _get_calls(session)[0]
    url = args[0]
    assert url == "https://oauth.reddit.com/r/testsub/top"
    assert ".json" not in url
    assert kwargs["headers"]["Authorization"] == "Bearer tok1"


def test_oauth_connection_error_then_200_retries_and_succeeds(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    session = _FakeSession([
        _FakeResponse(200, {"access_token": "tok1", "expires_in": 3600}),
        requests.exceptions.ConnectionError("reset"),
        _FakeResponse(200, _post_listing([_post_child()])),
    ])
    source = _oauth_source(session)
    posts = source.fetch_posts("testsub")
    assert len(posts) == 1


def test_oauth_resolve_subreddit_hits_oauth_search_endpoint(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    session = _FakeSession([
        _FakeResponse(200, {"access_token": "tok1", "expires_in": 3600}),
        _FakeResponse(200, _subreddit_search(["Fortnite"])),
    ])
    source = _oauth_source(session)
    assert source.resolve_subreddit("Fortnite") == "Fortnite"
    url = _get_calls(session)[0][1][0]
    assert url == "https://oauth.reddit.com/subreddits/search"


# ---------------------------------------------------------------------------
# _build_leaf_sources / build_reddit_source / build_subreddit_resolver
# (the critical "zero behavior change" proofs)
# ---------------------------------------------------------------------------

def test_build_leaf_sources_no_vars_returns_single_bare_json_source(monkeypatch):
    _clear_env(monkeypatch)
    leaves = _build_leaf_sources()
    assert len(leaves) == 1
    assert type(leaves[0]) is JsonRedditSource


def test_build_reddit_source_no_vars_returns_bare_cached_json(monkeypatch):
    _clear_env(monkeypatch)
    calls = []

    def cache_factory(namespace):
        calls.append(namespace)
        return InMemoryRedditCache()

    source = build_reddit_source(cache_factory)
    assert type(source) is CachedRedditSource
    assert type(source.inner) is JsonRedditSource
    assert calls == ["reddit"]


def test_build_reddit_source_wraps_with_posts_ttl_spanning_weekly_cadence(monkeypatch):
    """The posts cache TTL must exceed the 168h (7-day) run cadence, otherwise
    every Reddit-active game re-fetches its post listing on every weekly run
    (1 billed ScrapeOps request/game/week the cache can never prevent). The
    bare CachedRedditSource default is 24h; build_reddit_source overrides it to
    _POSTS_TTL_HOURS so a run reuses the prior run's cached posts (biweekly)."""
    _clear_env(monkeypatch)
    source = build_reddit_source(lambda ns: InMemoryRedditCache())
    assert type(source) is CachedRedditSource
    assert source.ttl_hours == _POSTS_TTL_HOURS
    assert _POSTS_TTL_HOURS > 168  # spans the weekly cadence


def test_build_reddit_source_applies_posts_ttl_to_every_leaf(monkeypatch):
    """With multiple egress leaves each wrapped CachedRedditSource gets the same
    extended TTL -- the biweekly saving must not depend on which leaf is active."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("REDDIT_PROXY_URL", "http://proxy.example:8080")
    source = build_reddit_source(lambda ns: InMemoryRedditCache())
    assert isinstance(source, FirstAvailableRedditSource)
    assert all(s.ttl_hours == _POSTS_TTL_HOURS for s in source.sources)


def test_build_reddit_source_proxy_only_returns_two_element_chain_in_order(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("REDDIT_PROXY_URL", "http://proxy.example:8080")
    calls = []

    def cache_factory(namespace):
        calls.append(namespace)
        return InMemoryRedditCache()

    source = build_reddit_source(cache_factory)
    assert isinstance(source, FirstAvailableRedditSource)
    assert len(source.sources) == 2
    assert type(source.sources[0]) is CachedRedditSource
    assert type(source.sources[0].inner) is ProxiedJsonRedditSource
    assert type(source.sources[1]) is CachedRedditSource
    assert type(source.sources[1].inner) is JsonRedditSource
    assert calls == ["reddit_proxy", "reddit"]


def test_build_leaf_sources_proxy_only_defaults_verify_true(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("REDDIT_PROXY_URL", "http://proxy.example:8080")
    leaves = _build_leaf_sources()
    proxy_leaf = next(l for l in leaves if type(l) is ProxiedJsonRedditSource)
    assert proxy_leaf.session.verify is True


def test_build_leaf_sources_proxy_insecure_ssl_env_var_sets_verify_false(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("REDDIT_PROXY_URL", "http://proxy.example:8080")
    monkeypatch.setenv("REDDIT_PROXY_INSECURE_SSL", "true")
    leaves = _build_leaf_sources()
    proxy_leaf = next(l for l in leaves if type(l) is ProxiedJsonRedditSource)
    assert proxy_leaf.session.verify is False


def test_build_reddit_source_partial_oauth_behaves_like_no_vars(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("REDDIT_CLIENT_ID", "cid")  # secret + refresh_token still unset
    source = build_reddit_source(lambda ns: InMemoryRedditCache())
    assert type(source) is CachedRedditSource
    assert type(source.inner) is JsonRedditSource


def test_build_reddit_source_all_vars_set_returns_three_element_oauth_proxy_json_chain(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("REDDIT_CLIENT_ID", "cid")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("REDDIT_REFRESH_TOKEN", "rtoken")
    monkeypatch.setenv("REDDIT_PROXY_URL", "http://proxy.example:8080")
    calls = []

    def cache_factory(namespace):
        calls.append(namespace)
        return InMemoryRedditCache()

    source = build_reddit_source(cache_factory)
    assert isinstance(source, FirstAvailableRedditSource)
    assert len(source.sources) == 3
    assert type(source.sources[0].inner) is OAuthRedditSource
    assert type(source.sources[1].inner) is ProxiedJsonRedditSource
    assert type(source.sources[2].inner) is JsonRedditSource
    assert calls == ["reddit_oauth", "reddit_proxy", "reddit"]


def test_build_subreddit_resolver_no_vars_returns_bare_json_source(monkeypatch):
    _clear_env(monkeypatch)
    resolver = build_subreddit_resolver()
    assert type(resolver) is JsonRedditSource


def test_build_subreddit_resolver_partial_oauth_behaves_like_no_vars(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("REDDIT_CLIENT_ID", "cid")
    resolver = build_subreddit_resolver()
    assert type(resolver) is JsonRedditSource


def test_build_subreddit_resolver_proxy_only_returns_two_element_uncached_chain(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("REDDIT_PROXY_URL", "http://proxy.example:8080")
    resolver = build_subreddit_resolver()
    assert isinstance(resolver, FirstAvailableRedditSource)
    assert len(resolver.sources) == 2
    assert type(resolver.sources[0]) is ProxiedJsonRedditSource
    assert type(resolver.sources[1]) is JsonRedditSource


def test_build_subreddit_resolver_all_vars_returns_three_element_uncached_chain(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("REDDIT_CLIENT_ID", "cid")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("REDDIT_REFRESH_TOKEN", "rtoken")
    monkeypatch.setenv("REDDIT_PROXY_URL", "http://proxy.example:8080")
    resolver = build_subreddit_resolver()
    assert isinstance(resolver, FirstAvailableRedditSource)
    assert len(resolver.sources) == 3
    assert type(resolver.sources[0]) is OAuthRedditSource
    assert type(resolver.sources[1]) is ProxiedJsonRedditSource
    assert type(resolver.sources[2]) is JsonRedditSource


# ---------------------------------------------------------------------------
# REDDIT_SOURCE_PAUSED
# ---------------------------------------------------------------------------

def test_build_reddit_source_paused_returns_null_source_with_no_cache_calls(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("REDDIT_SOURCE_PAUSED", "true")
    calls = []

    def cache_factory(namespace):
        calls.append(namespace)
        return InMemoryRedditCache()

    source = build_reddit_source(cache_factory)
    assert type(source) is NullRedditSource
    assert calls == []


def test_build_subreddit_resolver_paused_returns_null_source(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("REDDIT_SOURCE_PAUSED", "1")
    resolver = build_subreddit_resolver()
    assert type(resolver) is NullRedditSource


def test_paused_takes_priority_over_oauth_and_proxy_vars(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("REDDIT_CLIENT_ID", "cid")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("REDDIT_REFRESH_TOKEN", "rtoken")
    monkeypatch.setenv("REDDIT_PROXY_URL", "http://proxy.example:8080")
    monkeypatch.setenv("REDDIT_SOURCE_PAUSED", "yes")
    source = build_reddit_source(lambda ns: InMemoryRedditCache())
    assert type(source) is NullRedditSource


def test_null_reddit_source_raises_blocked_with_no_network_on_every_method():
    null_source = NullRedditSource()
    for call in (
        lambda: null_source.fetch_posts("gaming"),
        lambda: null_source.fetch_comments("abc123", "gaming"),
        lambda: null_source.resolve_subreddit("Some Game"),
    ):
        try:
            call()
            assert False, "expected RedditBlocked"
        except RedditBlocked:
            pass


def test_reddit_source_paused_falsy_values_behave_like_unset(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("REDDIT_SOURCE_PAUSED", "false")
    source = build_reddit_source(lambda ns: InMemoryRedditCache())
    assert type(source) is CachedRedditSource
    assert type(source.inner) is JsonRedditSource
