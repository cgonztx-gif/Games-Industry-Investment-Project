import re
import time
import requests
from typing import Optional

RAWG_BASE = "https://api.rawg.io/api"


def search_game(api_key: str, title: str, year: Optional[int] = None) -> Optional[dict]:
    """Search RAWG for a game. Returns slug + metadata of the best match, or None."""
    time.sleep(3.0)  # stay well under 20 req/min free tier
    params: dict = {"key": api_key, "search": title, "page_size": 5}
    if year:
        params["dates"] = f"{year - 1}-01-01,{year + 1}-12-31"

    try:
        resp = requests.get(f"{RAWG_BASE}/games", params=params, timeout=15)
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception:
        return None

    if not results:
        return None

    norm = title.lower().strip()
    for r in results:
        if r.get("name", "").lower().strip() == norm:
            return _parse(r)
    return _parse(results[0])


def get_steam_app_id(api_key: str, rawg_slug: str) -> Optional[str]:
    """
    Fetch the RAWG /games/{slug}/stores endpoint and extract the Steam app ID.
    The main detail endpoint returns empty URLs; only the /stores sub-endpoint
    has the actual store URLs.
    Returns the app ID string (e.g. "730") or None if not found.
    """
    time.sleep(3.0)
    try:
        resp = requests.get(
            f"{RAWG_BASE}/games/{rawg_slug}/stores",
            params={"key": api_key},
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json().get("results") or []
    except Exception:
        return None

    for entry in results:
        # store_id 1 = Steam in the RAWG taxonomy
        if entry.get("store_id") == 1:
            url = entry.get("url", "")
            match = re.search(r"/app/(\d+)", url)
            if match:
                return match.group(1)
    return None


def _parse(raw: dict) -> dict:
    return {
        "rawg_slug": raw.get("slug"),
        "metacritic": raw.get("metacritic"),
        "esrb_rating": (raw.get("esrb_rating") or {}).get("name"),
    }
