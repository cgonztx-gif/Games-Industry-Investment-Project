from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta, timezone

import requests

_NEWS_URL = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
_REQUEST_DELAY = 0.25

_PATCH_KEYWORDS = {
    "patch",
    "update",
    "hotfix",
    "balance",
    "season",
    "content",
    "release notes",
    "maintenance",
}


def _clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def classify_patch(title: str, contents: str) -> str:
    blob = f"{title} {contents}".lower()
    if any(term in blob for term in ("hotfix", "emergency fix")):
        return "hotfix"
    if any(term in blob for term in ("balance", "buff", "nerf", "weapon tuning")):
        return "balance"
    if any(term in blob for term in ("battle pass", "store", "shop", "bundle", "monetization")):
        return "monetization"
    if any(term in blob for term in ("engine", "performance", "crash", "stability", "renderer")):
        return "engine"
    if any(term in blob for term in ("season", "map", "mode", "event", "dlc", "expansion", "content")):
        return "content_drop"
    return "other"


def looks_like_update(title: str, contents: str) -> bool:
    blob = f"{title} {contents}".lower()
    return any(keyword in blob for keyword in _PATCH_KEYWORDS)


def get_recent_news(
    steam_app_id: str,
    days_back: int = 45,
    count: int = 20,
) -> list[dict]:
    time.sleep(_REQUEST_DELAY)
    resp = requests.get(
        _NEWS_URL,
        params={
            "appid": steam_app_id,
            "count": count,
            "maxlength": 2000,
            "format": "json",
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    items: list[dict] = []
    for raw in (data.get("appnews") or {}).get("newsitems", []):
        published = datetime.fromtimestamp(raw.get("date", 0), tz=timezone.utc)
        if published < cutoff:
            continue
        title = raw.get("title") or ""
        contents = _clean_html(raw.get("contents") or "")
        if not looks_like_update(title, contents):
            continue
        items.append(
            {
                "date": published.date().isoformat(),
                "title": title,
                "contents": contents,
                "url": raw.get("url"),
                "patch_type": classify_patch(title, contents),
            }
        )

    return sorted(items, key=lambda item: item["date"])
