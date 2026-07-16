"""
Unit tests for agents/workers/sentiment/steam_reviews_client.py.

No live network calls: requests.get and time.sleep are monkeypatched;
InMemoryApiCache stands in for api_cache. Exercises the fresh-cache/
stale-cache/blocked degradation ladder and response parsing.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agents.workers.sentiment.steam_reviews_client as src
from agents.workers.sentiment.steam_reviews_client import fetch_steam_reviews
from database.api_cache import InMemoryApiCache


class _FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload or {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _wire(monkeypatch, payload=None, status_code=200):
    monkeypatch.setattr(src.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        src.requests, "get", lambda *a, **kw: _FakeResponse(payload, status_code)
    )


def test_none_app_id_returns_empty_without_network(monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("network must not be touched")

    monkeypatch.setattr(src.requests, "get", _boom)
    assert fetch_steam_reviews(None) == []


def test_parses_reviews_and_skips_empty_text(monkeypatch):
    _wire(monkeypatch, {"reviews": [
        {"review": "  Great game  ", "votes_up": 12, "voted_up": True},
        {"review": "", "votes_up": 5, "voted_up": False},          # skipped
        {"review": "x" * 1000, "votes_up": None, "voted_up": False},
    ]})

    reviews = fetch_steam_reviews("123")

    assert len(reviews) == 2
    assert reviews[0] == {"text": "Great game", "score": 12, "is_positive": True}
    assert len(reviews[1]["text"]) == 600  # capped
    assert reviews[1]["score"] == 0        # null votes_up coerced


def test_blocked_status_serves_stale_cache(monkeypatch):
    cache = InMemoryApiCache()
    cache.set("recent:123:50", [{"text": "cached", "score": 1, "is_positive": True}])
    # Make the fresh-read TTL miss so the live call runs and gets blocked.
    cache._store["recent:123:50"] = (cache._store["recent:123:50"][0], 0.0)
    _wire(monkeypatch, status_code=429)

    reviews = fetch_steam_reviews("123", cache=cache)

    assert reviews == [{"text": "cached", "score": 1, "is_positive": True}]


def test_blocked_status_with_no_cache_returns_empty(monkeypatch):
    _wire(monkeypatch, status_code=403)
    assert fetch_steam_reviews("123") == []


def test_fresh_cache_hit_skips_network(monkeypatch):
    cache = InMemoryApiCache()
    cache.set("recent:123:50", [{"text": "fresh", "score": 2, "is_positive": True}])

    def _boom(*a, **kw):
        raise AssertionError("network must not be touched on a fresh cache hit")

    monkeypatch.setattr(src.requests, "get", _boom)

    assert fetch_steam_reviews("123", cache=cache)[0]["text"] == "fresh"


def test_success_populates_cache(monkeypatch):
    cache = InMemoryApiCache()
    _wire(monkeypatch, {"reviews": [{"review": "solid", "votes_up": 3, "voted_up": True}]})

    fetch_steam_reviews("123", cache=cache)

    assert cache.get("recent:123:50")[0]["text"] == "solid"
