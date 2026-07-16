from __future__ import annotations

import itertools
import json
import os
import re
import time
from typing import Iterable

import requests

from database.api_cache import ApiCache

_YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
_REQUEST_DELAY = 0.2


def _normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _playlist_ids() -> list[str]:
    raw = os.environ.get("YOUTUBE_UPLOAD_PLAYLISTS", "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def load_game_youtube_playlists() -> dict[str, list[str]]:
    """
    Load optional manually-curated per-game/per-studio creator upload-playlist
    IDs -- same defensive JSON-env convention as
    patch_notes/blog_client.py::load_game_patch_pages() and
    studio_intel/ats_clients.py::load_ats_board_map().

    Expected env shape:
      GAME_YOUTUBE_PLAYLISTS='{"Fortnite": ["UUabc123..."],
                                "Apex Legends": "UUdef456..."}'

    Keyed by exact watchlist game title (games.title). Values may be a single
    playlist ID string or a list of IDs. Missing/invalid env value returns {}
    (no games have one configured by default -- this is a curated allowlist,
    not a discovery mechanism, to avoid the quota-expensive search.list
    endpoint). Each ID must be a channel's "uploads" playlist ID (the same
    shape YOUTUBE_UPLOAD_PLAYLISTS already expects), not a raw channel ID or
    video ID.

    Unlike the global YOUTUBE_UPLOAD_PLAYLISTS list -- which is filtered by a
    title/description substring match in _candidate_videos() because a single
    outlet channel covers many unrelated games -- videos from a per-game
    playlist configured here are trusted as already game-scoped and are not
    re-filtered by title (see _trusted_candidate_videos()).
    """
    raw = os.environ.get("GAME_YOUTUBE_PLAYLISTS", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}

    result: dict[str, list[str]] = {}
    for title, playlist_ids in data.items():
        if isinstance(playlist_ids, str):
            result[title] = [playlist_ids] if playlist_ids.strip() else []
        elif isinstance(playlist_ids, list):
            result[title] = [p for p in playlist_ids if isinstance(p, str) and p.strip()]
    return result


def _get(url: str, params: dict) -> dict:
    time.sleep(_REQUEST_DELAY)
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _candidate_videos(api_key: str, game_title: str, playlist_ids: Iterable[str]) -> list[str]:
    game_norm = _normalize(game_title)
    video_ids: list[str] = []

    for playlist_id in playlist_ids:
        data = _get(
            f"{_YOUTUBE_API}/playlistItems",
            {
                "key": api_key,
                "part": "snippet,contentDetails",
                "playlistId": playlist_id,
                "maxResults": 50,
            },
        )
        for item in data.get("items", []):
            snippet = item.get("snippet") or {}
            title = _normalize(snippet.get("title", ""))
            description = _normalize(snippet.get("description", ""))
            if game_norm and (game_norm in title or game_norm in description):
                video_id = (item.get("contentDetails") or {}).get("videoId")
                if video_id and video_id not in video_ids:
                    video_ids.append(video_id)

    return video_ids


def _trusted_candidate_videos(
    api_key: str, playlist_ids: Iterable[str], per_playlist_limit: int
) -> list[str]:
    """
    Like _candidate_videos() but for manually-curated, already-game-scoped
    playlists (load_game_youtube_playlists()) -- returns each playlist's most
    recent uploads directly, with no title/description filtering, since the
    curation itself is the relevance signal.

    Videos are interleaved round-robin across playlists rather than
    concatenated playlist-by-playlist, since the caller truncates the
    combined result to max_videos: a plain concatenation would let an early
    playlist (often the official/trailer channel, which commonly has
    comments disabled or hidden) crowd out every later playlist's videos
    entirely once per-playlist limit >= max_videos / len(playlist_ids).
    """
    per_playlist: list[list[str]] = []
    for playlist_id in playlist_ids:
        data = _get(
            f"{_YOUTUBE_API}/playlistItems",
            {
                "key": api_key,
                "part": "contentDetails",
                "playlistId": playlist_id,
                "maxResults": min(per_playlist_limit, 50),
            },
        )
        ids = [
            (item.get("contentDetails") or {}).get("videoId")
            for item in data.get("items", [])
        ]
        per_playlist.append([v for v in ids if v])

    video_ids: list[str] = []
    seen: set[str] = set()
    for round_videos in itertools.zip_longest(*per_playlist):
        for video_id in round_videos:
            if video_id and video_id not in seen:
                seen.add(video_id)
                video_ids.append(video_id)
    return video_ids


def _comments_for_video(api_key: str, video_id: str, limit: int) -> list[dict]:
    try:
        data = _get(
            f"{_YOUTUBE_API}/commentThreads",
            {
                "key": api_key,
                "part": "snippet",
                "videoId": video_id,
                "maxResults": min(limit, 100),
                "order": "relevance",
                "textFormat": "plainText",
            },
        )
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status == 403:
            return []  # commentsDisabled and quota-shaped misses are normal.
        raise

    comments: list[dict] = []
    for item in data.get("items", []):
        snippet = (item.get("snippet") or {}).get("topLevelComment", {}).get("snippet", {})
        text = (snippet.get("textDisplay") or "").strip()
        if not text:
            continue
        comments.append(
            {
                "text": text[:600],
                "score": int(snippet.get("likeCount") or 0),
                "video_id": video_id,
            }
        )
    return comments


def fetch_youtube_comments(
    game_title: str,
    cache: ApiCache | None = None,
    max_videos: int = 2,
    comments_per_video: int = 50,
    ttl_hours: int = 24,
    game_playlist_ids: Iterable[str] | None = None,
) -> list[dict]:
    """
    Fetch YouTube comments using official Data API calls only.

    Video discovery combines two sources, both via playlistItems.list only --
    this avoids the quota-expensive search.list endpoint entirely:
      1. The shared global outlet playlists (YOUTUBE_UPLOAD_PLAYLISTS), title/
         description-filtered against game_title since one outlet channel
         covers many games (_candidate_videos()).
      2. Optional per-game/per-studio creator playlists passed in via
         game_playlist_ids (sourced from GAME_YOUTUBE_PLAYLISTS by the
         caller, keyed by game_title) -- trusted as already game-scoped and
         used unfiltered (_trusted_candidate_videos()).
    Set YOUTUBE_API_KEY plus at least one of YOUTUBE_UPLOAD_PLAYLISTS /
    game_playlist_ids to enable.
    """
    api_key = os.environ.get("YOUTUBE_API_KEY")
    global_playlists = _playlist_ids()
    game_playlists = [p for p in (game_playlist_ids or []) if p]
    if not api_key or not (global_playlists or game_playlists):
        return []

    cache_key = f"comments:{_normalize(game_title)}"
    if cache:
        fresh = cache.get(cache_key, max_age_hours=ttl_hours)
        if isinstance(fresh, list):
            return fresh

    try:
        global_ids = (
            _candidate_videos(api_key, game_title, global_playlists) if global_playlists else []
        )
        trusted_ids = (
            _trusted_candidate_videos(api_key, game_playlists, max_videos) if game_playlists else []
        )
        # Same crowding-out failure mode the 2026-07-12 fix closed WITHIN
        # _trusted_candidate_videos, one level up: concatenating global_ids
        # before trusted_ids and then truncating to max_videos let the global
        # outlet playlists (title-filtered, so they fire whenever an outlet
        # happens to cover this game) fill the entire cap before any
        # per-game-curated community video was ever considered. Interleave
        # the two sources round-robin instead of letting list order decide.
        video_ids: list[str] = []
        seen: set[str] = set()
        for pair in itertools.zip_longest(global_ids, trusted_ids):
            for video_id in pair:
                if video_id and video_id not in seen:
                    seen.add(video_id)
                    video_ids.append(video_id)
        video_ids = video_ids[:max_videos]

        comments: list[dict] = []
        for video_id in video_ids:
            comments.extend(_comments_for_video(api_key, video_id, comments_per_video))
        if cache:
            cache.set(cache_key, comments)
        return comments
    except Exception:
        if cache:
            stale = cache.get(cache_key)
            if isinstance(stale, list):
                return stale
        return []
