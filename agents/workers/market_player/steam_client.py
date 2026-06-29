import re
import time
import requests
from typing import Optional

STEAMSPY_BASE = "https://steamspy.com/api.php"


def _steamspy_get(params: dict) -> dict:
    time.sleep(1.0)
    resp = requests.get(STEAMSPY_BASE, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _parse_entry(appid: str, info: dict, is_live_service: bool = False) -> dict:
    return {
        "steam_app_id": str(appid),
        "title": info.get("name", ""),
        "ccu": info.get("ccu", 0),
        "genres": [],
        "is_live_service": is_live_service,
        "igdb_id": None,
        "rawg_slug": None,
        "release_date": None,
        "studio_name": info.get("developer") or None,
        "ticker": None,
    }


def get_top_ccu_games(min_ccu: int = 1000) -> list[dict]:
    """Top 100 games by CCU in the last 2 weeks."""
    data = _steamspy_get({"request": "top100in2weeks"})
    return [
        _parse_entry(appid, info)
        for appid, info in data.items()
        if isinstance(info, dict) and info.get("ccu", 0) >= min_ccu
    ]


def get_live_service_candidates(min_ccu: int = 5000) -> list[dict]:
    """Top 100 games of all time + Massively Multiplayer genre from SteamSpy."""
    results: dict[str, dict] = {}

    top_forever = _steamspy_get({"request": "top100forever"})
    for appid, info in top_forever.items():
        if isinstance(info, dict) and info.get("ccu", 0) >= min_ccu:
            results[str(appid)] = _parse_entry(appid, info, is_live_service=True)

    mmo = _steamspy_get({"request": "genre", "genre": "Massively Multiplayer"})
    for appid, info in mmo.items():
        if isinstance(info, dict) and info.get("ccu", 0) >= min_ccu:
            key = str(appid)
            if key in results:
                results[key]["genres"] = ["Massively Multiplayer Online (MMO)"]
                results[key]["is_live_service"] = True
            else:
                entry = _parse_entry(appid, info, is_live_service=True)
                entry["genres"] = ["Massively Multiplayer Online (MMO)"]
                results[key] = entry

    return list(results.values())


def get_app_metrics(steam_app_id: str) -> dict:
    """CCU and review data for a single Steam app via SteamSpy appdetails."""
    data = _steamspy_get({"request": "appdetails", "appid": steam_app_id})
    positive = data.get("positive") or 0
    negative = data.get("negative") or 0
    total = positive + negative
    review_score = round(positive / total * 100, 2) if total > 0 else None
    return {
        "ccu": data.get("ccu"),
        "review_score": review_score,
        "review_count": total or None,
    }


def normalize_title(title: str) -> str:
    t = title.lower()
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\b(the|a|an)\b\s*", "", t)
    return re.sub(r"\s+", " ", t).strip()


def resolve_app_id(title: str, steam_games: list[dict]) -> Optional[str]:
    """Find a Steam app ID for a game by normalized title match."""
    norm = normalize_title(title)
    for g in steam_games:
        if normalize_title(g["title"]) == norm:
            return g["steam_app_id"]
    return None
