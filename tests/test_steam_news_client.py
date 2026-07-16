"""
Unit tests for agents/workers/patch_notes/steam_news_client.py.

No live network calls: requests.get and time.sleep are monkeypatched. Focus is
the response parsing get_recent_news() does against realistic ISteamNews
payloads (missing appnews, old items, non-update noise, HTML contents) plus
the pure classifiers.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agents.workers.patch_notes.steam_news_client as snc
from agents.workers.patch_notes.steam_news_client import (
    classify_patch,
    get_recent_news,
    has_content_indicators,
    looks_like_update,
)


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _ts(days_ago: int) -> int:
    return int((datetime.now(timezone.utc) - timedelta(days=days_ago)).timestamp())


def _wire(monkeypatch, payload):
    monkeypatch.setattr(snc.time, "sleep", lambda _: None)
    monkeypatch.setattr(snc.requests, "get", lambda *a, **kw: _FakeResponse(payload))


# ---------------------------------------------------------------------------
# classifiers
# ---------------------------------------------------------------------------

def test_classify_patch_precedence():
    assert classify_patch("Emergency hotfix", "") == "hotfix"
    assert classify_patch("Weapon balance pass", "buffs and nerfs") == "balance"
    assert classify_patch("New battle pass", "store bundle") == "monetization"
    assert classify_patch("Engine upgrade", "performance and stability") == "engine"
    assert classify_patch("Season 12 launch", "new map and mode") == "content_drop"
    assert classify_patch("Community spotlight", "fan art roundup") == "other"


def test_looks_like_update_and_content_indicators():
    assert looks_like_update("Patch 1.2 notes", "")
    assert not looks_like_update("Developer AMA announcement", "join us on reddit")
    assert has_content_indicators("Season 5", "new map")
    assert not has_content_indicators("Bugfix pass", "fixed crashes")


# ---------------------------------------------------------------------------
# get_recent_news parsing
# ---------------------------------------------------------------------------

def test_get_recent_news_parses_and_sorts_update_items(monkeypatch):
    _wire(monkeypatch, {"appnews": {"newsitems": [
        {"date": _ts(2), "title": "Patch 2.0 update", "contents": "<b>Balance</b> changes",
         "url": "https://steam/2"},
        {"date": _ts(10), "title": "Hotfix 1.9.1", "contents": "emergency fix",
         "url": "https://steam/1"},
    ]}})

    items = get_recent_news("123", days_back=45)

    assert [i["url"] for i in items] == ["https://steam/1", "https://steam/2"]
    assert items[1]["contents"] == "Balance changes"  # HTML stripped
    assert items[0]["patch_type"] == "hotfix"


def test_get_recent_news_filters_old_and_non_update_items(monkeypatch):
    _wire(monkeypatch, {"appnews": {"newsitems": [
        {"date": _ts(90), "title": "Patch notes from long ago", "contents": "update",
         "url": "https://steam/old"},
        {"date": _ts(1), "title": "Soundtrack now on vinyl", "contents": "merch drop",
         "url": "https://steam/merch"},
    ]}})

    assert get_recent_news("123", days_back=45) == []


def test_get_recent_news_tolerates_missing_appnews(monkeypatch):
    _wire(monkeypatch, {})
    assert get_recent_news("123") == []


def test_get_recent_news_tolerates_missing_fields(monkeypatch):
    # No title/contents/url on an otherwise-recent item: must not crash, and
    # (with no update keyword present) must be filtered rather than written.
    _wire(monkeypatch, {"appnews": {"newsitems": [{"date": _ts(1)}]}})
    assert get_recent_news("123") == []


def test_get_recent_news_http_error_raises_to_caller(monkeypatch):
    """The worker's per-game try/except owns degradation; the client raises."""
    monkeypatch.setattr(snc.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        snc.requests, "get", lambda *a, **kw: _FakeResponse({}, status_code=500)
    )
    import pytest

    with pytest.raises(RuntimeError, match="HTTP 500"):
        get_recent_news("123")
