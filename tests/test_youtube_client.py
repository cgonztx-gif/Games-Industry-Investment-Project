"""
Unit tests for agents/workers/sentiment/youtube_client.py.

No live HTTP: requests.get is monkeypatched to a fake responding per
playlistId/videoId, so both the shared global-playlist path (title/
description filtered) and the per-game curated-playlist path (unfiltered,
added in the per-game YouTube channel discovery follow-up) are covered
without any real network calls.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agents.workers.sentiment import youtube_client
from database.api_cache import InMemoryApiCache


# ---------------------------------------------------------------------------
# load_game_youtube_playlists
# ---------------------------------------------------------------------------

def test_load_game_youtube_playlists_missing_env(monkeypatch):
    monkeypatch.delenv("GAME_YOUTUBE_PLAYLISTS", raising=False)
    assert youtube_client.load_game_youtube_playlists() == {}


def test_load_game_youtube_playlists_invalid_json(monkeypatch):
    monkeypatch.setenv("GAME_YOUTUBE_PLAYLISTS", "{not valid json")
    assert youtube_client.load_game_youtube_playlists() == {}


def test_load_game_youtube_playlists_non_dict_json(monkeypatch):
    monkeypatch.setenv("GAME_YOUTUBE_PLAYLISTS", "[1, 2, 3]")
    assert youtube_client.load_game_youtube_playlists() == {}


def test_load_game_youtube_playlists_valid(monkeypatch):
    monkeypatch.setenv(
        "GAME_YOUTUBE_PLAYLISTS",
        '{"Fortnite": ["UUabc123"], '
        '"Apex Legends": "UUdef456", '
        '"Bad Entry": 123}',
    )
    result = youtube_client.load_game_youtube_playlists()
    assert result["Fortnite"] == ["UUabc123"]
    assert result["Apex Legends"] == ["UUdef456"]
    assert "Bad Entry" not in result


# ---------------------------------------------------------------------------
# fetch_youtube_comments
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def _make_fake_get(playlist_items: dict, comment_threads: dict):
    """
    playlist_items: playlistId -> list of {"title", "description", "videoId"}
    comment_threads: videoId -> list of {"text", "likeCount"}
    """

    def fake_get(url, params=None, timeout=None):
        params = params or {}
        if url.endswith("/playlistItems"):
            playlist_id = params["playlistId"]
            items = playlist_items.get(playlist_id, [])
            return _FakeResponse(
                {
                    "items": [
                        {
                            "snippet": {
                                "title": item.get("title", ""),
                                "description": item.get("description", ""),
                            },
                            "contentDetails": {"videoId": item["videoId"]},
                        }
                        for item in items
                    ]
                }
            )
        if url.endswith("/commentThreads"):
            video_id = params["videoId"]
            comments = comment_threads.get(video_id, [])
            return _FakeResponse(
                {
                    "items": [
                        {
                            "snippet": {
                                "topLevelComment": {
                                    "snippet": {
                                        "textDisplay": c["text"],
                                        "likeCount": c.get("likeCount", 0),
                                    }
                                }
                            }
                        }
                        for c in comments
                    ]
                }
            )
        raise AssertionError(f"unexpected URL {url}")

    return fake_get


def test_fetch_youtube_comments_no_api_key(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.setenv("YOUTUBE_UPLOAD_PLAYLISTS", "UUglobal")
    assert youtube_client.fetch_youtube_comments("Fortnite") == []


def test_fetch_youtube_comments_no_playlists_at_all(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "key")
    monkeypatch.delenv("YOUTUBE_UPLOAD_PLAYLISTS", raising=False)
    assert youtube_client.fetch_youtube_comments("Fortnite") == []


def test_fetch_youtube_comments_global_playlist_filters_by_title(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "key")
    monkeypatch.setenv("YOUTUBE_UPLOAD_PLAYLISTS", "UUglobal")

    fake_get = _make_fake_get(
        playlist_items={
            "UUglobal": [
                {"title": "Fortnite Chapter 6 Review", "description": "", "videoId": "vid1"},
                {"title": "Unrelated Other Game Review", "description": "", "videoId": "vid2"},
            ]
        },
        comment_threads={"vid1": [{"text": "great update", "likeCount": 5}]},
    )
    monkeypatch.setattr(youtube_client.requests, "get", fake_get)
    monkeypatch.setattr(youtube_client.time, "sleep", lambda *_: None)

    comments = youtube_client.fetch_youtube_comments("Fortnite", max_videos=5)
    assert len(comments) == 1
    assert comments[0]["video_id"] == "vid1"
    assert comments[0]["text"] == "great update"


def test_fetch_youtube_comments_game_playlist_is_unfiltered(monkeypatch):
    """A per-game curated playlist's videos are trusted regardless of title match."""
    monkeypatch.setenv("YOUTUBE_API_KEY", "key")
    monkeypatch.delenv("YOUTUBE_UPLOAD_PLAYLISTS", raising=False)

    fake_get = _make_fake_get(
        playlist_items={
            "UUgamecreator": [
                {"title": "Weekly community stream highlights", "description": "", "videoId": "vid9"},
            ]
        },
        comment_threads={"vid9": [{"text": "love this game", "likeCount": 3}]},
    )
    monkeypatch.setattr(youtube_client.requests, "get", fake_get)
    monkeypatch.setattr(youtube_client.time, "sleep", lambda *_: None)

    comments = youtube_client.fetch_youtube_comments(
        "Fortnite", max_videos=5, game_playlist_ids=["UUgamecreator"]
    )
    assert len(comments) == 1
    assert comments[0]["video_id"] == "vid9"


def test_fetch_youtube_comments_merges_global_and_game_playlists(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "key")
    monkeypatch.setenv("YOUTUBE_UPLOAD_PLAYLISTS", "UUglobal")

    fake_get = _make_fake_get(
        playlist_items={
            "UUglobal": [
                {"title": "Fortnite Season Review", "description": "", "videoId": "vid1"},
            ],
            "UUgamecreator": [
                {"title": "Random stream title", "description": "", "videoId": "vid2"},
            ],
        },
        comment_threads={
            "vid1": [{"text": "comment from outlet", "likeCount": 1}],
            "vid2": [{"text": "comment from creator", "likeCount": 1}],
        },
    )
    monkeypatch.setattr(youtube_client.requests, "get", fake_get)
    monkeypatch.setattr(youtube_client.time, "sleep", lambda *_: None)

    comments = youtube_client.fetch_youtube_comments(
        "Fortnite", max_videos=5, game_playlist_ids=["UUgamecreator"]
    )
    video_ids = {c["video_id"] for c in comments}
    assert video_ids == {"vid1", "vid2"}


def test_fetch_youtube_comments_respects_max_videos_cap(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "key")
    monkeypatch.delenv("YOUTUBE_UPLOAD_PLAYLISTS", raising=False)

    fake_get = _make_fake_get(
        playlist_items={
            "UUgamecreator": [
                {"title": "video a", "description": "", "videoId": "vid1"},
                {"title": "video b", "description": "", "videoId": "vid2"},
                {"title": "video c", "description": "", "videoId": "vid3"},
            ]
        },
        comment_threads={
            "vid1": [{"text": "a", "likeCount": 1}],
            "vid2": [{"text": "b", "likeCount": 1}],
            "vid3": [{"text": "c", "likeCount": 1}],
        },
    )
    monkeypatch.setattr(youtube_client.requests, "get", fake_get)
    monkeypatch.setattr(youtube_client.time, "sleep", lambda *_: None)

    comments = youtube_client.fetch_youtube_comments(
        "Fortnite", max_videos=2, game_playlist_ids=["UUgamecreator"]
    )
    assert len(comments) == 2


def test_fetch_youtube_comments_interleaves_multiple_trusted_playlists(monkeypatch):
    """
    Regression test: with 2+ trusted playlists and max_videos capping the
    combined pool, videos must be interleaved round-robin across playlists,
    not concatenated -- otherwise a playlist listed first (e.g. an official
    trailer channel with comments disabled) starves every later playlist's
    videos out of the truncated result entirely.
    """
    monkeypatch.setenv("YOUTUBE_API_KEY", "key")
    monkeypatch.delenv("YOUTUBE_UPLOAD_PLAYLISTS", raising=False)

    fake_get = _make_fake_get(
        playlist_items={
            "UUofficial": [
                {"title": "Trailer 1", "description": "", "videoId": "official1"},
                {"title": "Trailer 2", "description": "", "videoId": "official2"},
            ],
            "UUcommunity": [
                {"title": "Community video 1", "description": "", "videoId": "community1"},
                {"title": "Community video 2", "description": "", "videoId": "community2"},
            ],
        },
        comment_threads={
            # Official channel videos have comments disabled -- 0 comments.
            "official1": [],
            "official2": [],
            "community1": [{"text": "great strat", "likeCount": 1}],
            "community2": [{"text": "nice build", "likeCount": 1}],
        },
    )
    monkeypatch.setattr(youtube_client.requests, "get", fake_get)
    monkeypatch.setattr(youtube_client.time, "sleep", lambda *_: None)

    comments = youtube_client.fetch_youtube_comments(
        "Fortnite",
        max_videos=2,
        game_playlist_ids=["UUofficial", "UUcommunity"],
    )
    assert len(comments) == 1
    assert comments[0]["video_id"] == "community1"


def test_fetch_youtube_comments_interleaves_global_and_trusted_playlists(monkeypatch):
    """
    Regression test for a bug adjacent to the 2026-07-12 trusted-playlist
    interleaving fix: the *outer* combine step in fetch_youtube_comments()
    still concatenated global-playlist candidates before trusted (per-game
    curated) candidates and then truncated to max_videos. A game with any
    global-outlet coverage at all (title-filtered, so common) crowded out
    every per-game community creator video, even though the whole point of
    GAME_YOUTUBE_PLAYLISTS is to guarantee community coverage the global
    outlet list misses.
    """
    monkeypatch.setenv("YOUTUBE_API_KEY", "key")
    monkeypatch.setenv("YOUTUBE_UPLOAD_PLAYLISTS", "UUglobal")

    fake_get = _make_fake_get(
        playlist_items={
            "UUglobal": [
                {"title": "Fortnite Recap 1", "description": "", "videoId": "global1"},
                {"title": "Fortnite Recap 2", "description": "", "videoId": "global2"},
            ],
            "UUcommunity": [
                {"title": "community stream", "description": "", "videoId": "community1"},
            ],
        },
        comment_threads={
            "global1": [{"text": "from official", "likeCount": 1}],
            "global2": [{"text": "from official 2", "likeCount": 1}],
            "community1": [{"text": "from community", "likeCount": 1}],
        },
    )
    monkeypatch.setattr(youtube_client.requests, "get", fake_get)
    monkeypatch.setattr(youtube_client.time, "sleep", lambda *_: None)

    comments = youtube_client.fetch_youtube_comments(
        "Fortnite", max_videos=2, game_playlist_ids=["UUcommunity"]
    )
    video_ids = {c["video_id"] for c in comments}
    assert "community1" in video_ids


def test_fetch_youtube_comments_uses_cache(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "key")
    monkeypatch.setenv("YOUTUBE_UPLOAD_PLAYLISTS", "UUglobal")

    def boom(*_args, **_kwargs):
        raise AssertionError("should not hit the network on a cache hit")

    monkeypatch.setattr(youtube_client.requests, "get", boom)

    cache = InMemoryApiCache()
    cache.set("comments:fortnite", [{"text": "cached", "score": 1, "video_id": "vid1"}])

    comments = youtube_client.fetch_youtube_comments("Fortnite", cache=cache)
    assert comments == [{"text": "cached", "score": 1, "video_id": "vid1"}]


def test_fetch_youtube_comments_falls_back_to_stale_cache_on_error(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "key")
    monkeypatch.setenv("YOUTUBE_UPLOAD_PLAYLISTS", "UUglobal")

    def boom(*_args, **_kwargs):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(youtube_client.requests, "get", boom)

    cache = InMemoryApiCache()
    # Pre-populate without going through .set()'s fresh path check semantics --
    # a plain .set() call is enough since .get() with no max_age_hours (the
    # stale-fallback branch) ignores freshness entirely.
    cache.set("comments:fortnite", [{"text": "stale", "score": 1, "video_id": "vid1"}])

    comments = youtube_client.fetch_youtube_comments("Fortnite", cache=cache, ttl_hours=0)
    assert comments == [{"text": "stale", "score": 1, "video_id": "vid1"}]
