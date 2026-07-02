import re
import time
import logging
import os
import requests
from typing import Optional

from database.api_cache import ApiCache

STEAM_CHARTS_URL = "https://api.steampowered.com/ISteamChartsService/GetMostPlayedGames/v1/"
STEAM_APP_LIST_URL = "https://api.steampowered.com/ISteamApps/GetAppList/v2/"
STEAM_CURRENT_PLAYERS_URL = (
    "https://api.steampowered.com/ISteamUserStats/"
    "GetNumberOfCurrentPlayers/v1/"
)
STEAM_REVIEWS_URL = "https://store.steampowered.com/appreviews/{app_id}"
_APPREVIEWS_DELAY = 0.5

logger = logging.getLogger("steam_client")


class SteamReviewsBlocked(Exception):
    """Steam appreviews throttled or blocked this run."""


def _steam_get(url: str, params: dict | None = None) -> dict:
    time.sleep(0.5)
    resp = requests.get(url, params=params or {}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _steam_api_key_params() -> dict:
    key = os.environ.get("STEAM_API_KEY")
    return {"key": key} if key else {}


def _app_name_map() -> dict[str, str]:
    data = _steam_get(STEAM_APP_LIST_URL)
    apps = (data.get("applist") or {}).get("apps") or []
    return {str(app.get("appid")): app.get("name", "") for app in apps}


def _most_played_rows() -> list[dict]:
    data = _steam_get(STEAM_CHARTS_URL, params=_steam_api_key_params())
    response = data.get("response") or {}
    return response.get("ranks") or []


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
    """Top most-played Steam games from the official Steam Charts API."""
    names = _app_name_map()
    results = []
    for row in _most_played_rows():
        appid = str(row.get("appid") or "")
        ccu = row.get("current_in_game") or row.get("peak_in_game") or 0
        if not appid or ccu < min_ccu:
            continue
        results.append(
            _parse_entry(
                appid,
                {"name": names.get(appid, ""), "ccu": ccu, "developer": None},
            )
        )
    return results


def get_live_service_candidates(min_ccu: int = 5000) -> list[dict]:
    """
    High-CCU candidates from the official Steam Charts API.

    Steam does not expose live-service classification directly, so these are
    candidates for IGDB/RAWG enrichment rather than final labels.
    """
    names = _app_name_map()
    results: dict[str, dict] = {}
    for row in _most_played_rows():
        appid = str(row.get("appid") or "")
        ccu = row.get("current_in_game") or row.get("peak_in_game") or 0
        if not appid or ccu < min_ccu:
            continue
        results[appid] = _parse_entry(
            appid,
            {"name": names.get(appid, ""), "ccu": ccu, "developer": None},
            is_live_service=True,
        )
    return list(results.values())


def get_current_players(steam_app_id: str) -> int | None:
    """Current players via official Steam Web API."""
    resp = requests.get(
        STEAM_CURRENT_PLAYERS_URL,
        params={"appid": steam_app_id},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    response = data.get("response") or {}
    if response.get("result") not in (None, 1):
        return None
    count = response.get("player_count")
    return int(count) if count is not None else None


def _fetch_review_summary(steam_app_id: str) -> dict:
    time.sleep(_APPREVIEWS_DELAY)
    resp = requests.get(
        STEAM_REVIEWS_URL.format(app_id=steam_app_id),
        params={
            "json": "1",
            "filter": "all",
            "language": "all",
            "review_type": "all",
            "purchase_type": "all",
            "num_per_page": "1",
        },
        timeout=15,
    )
    if resp.status_code in (429, 503):
        raise SteamReviewsBlocked(f"{resp.status_code} for Steam appreviews {steam_app_id}")
    if resp.status_code in (403, 451):
        raise SteamReviewsBlocked(f"{resp.status_code} for Steam appreviews {steam_app_id}")
    resp.raise_for_status()
    data = resp.json()
    summary = data.get("query_summary") or {}
    positive = summary.get("total_positive") or 0
    negative = summary.get("total_negative") or 0
    total = positive + negative
    review_score = round(positive / total * 100, 2) if total > 0 else None
    return {
        "review_score": review_score,
        "review_count": total or None,
    }


def get_review_summary(
    steam_app_id: str,
    cache: ApiCache | None = None,
    ttl_hours: int = 24,
) -> dict:
    """
    Review summary via Steam's public appreviews endpoint.

    appreviews is Tier 2 in the risk register, so this function uses the shared
    api_cache table and serves stale data if Steam throttles or the endpoint is
    temporarily unreachable.
    """
    key = f"summary:{steam_app_id}"
    if cache:
        fresh = cache.get(key, max_age_hours=ttl_hours)
        if isinstance(fresh, dict):
            return fresh

    try:
        summary = _fetch_review_summary(steam_app_id)
        if cache:
            cache.set(key, summary)
        return summary
    except Exception:
        if cache:
            stale = cache.get(key)
            if isinstance(stale, dict):
                logger.warning(
                    "serving stale Steam review summary for app %s",
                    steam_app_id,
                    exc_info=True,
                )
                return stale
        raise


def get_app_metrics(
    steam_app_id: str,
    review_cache: ApiCache | None = None,
) -> dict:
    """Current-player and review metrics for a single Steam app."""
    ccu = None
    try:
        ccu = get_current_players(steam_app_id)
    except Exception:
        logger.warning("current-player fetch failed for app %s", steam_app_id, exc_info=True)

    summary = get_review_summary(steam_app_id, cache=review_cache)
    return {
        "ccu": ccu,
        "review_score": summary.get("review_score"),
        "review_count": summary.get("review_count"),
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
