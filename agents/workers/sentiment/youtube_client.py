from __future__ import annotations

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
) -> list[dict]:
    """
    Fetch YouTube comments using official Data API calls only.

    Video discovery is intentionally limited to configured upload playlists via
    playlistItems.list. This avoids the quota-expensive search.list endpoint.
    Set YOUTUBE_API_KEY and comma-separated YOUTUBE_UPLOAD_PLAYLISTS to enable.
    """
    api_key = os.environ.get("YOUTUBE_API_KEY")
    playlists = _playlist_ids()
    if not api_key or not playlists:
        return []

    cache_key = f"comments:{_normalize(game_title)}"
    if cache:
        fresh = cache.get(cache_key, max_age_hours=ttl_hours)
        if isinstance(fresh, list):
            return fresh

    try:
        video_ids = _candidate_videos(api_key, game_title, playlists)[:max_videos]
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
