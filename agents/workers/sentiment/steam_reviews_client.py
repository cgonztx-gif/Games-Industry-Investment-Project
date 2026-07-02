import time
import requests

from database.api_cache import ApiCache

_REVIEWS_URL = "https://store.steampowered.com/appreviews/{app_id}"
_REQUEST_DELAY = 0.5  # seconds between requests


class SteamReviewsBlocked(Exception):
    """Steam appreviews throttled or blocked this run."""


def _fetch_steam_reviews_uncached(app_id: str, num_per_page: int) -> list[dict]:
    time.sleep(_REQUEST_DELAY)

    resp = requests.get(
        _REVIEWS_URL.format(app_id=app_id),
        params={
            "json": "1",
            "filter": "recent",
            "language": "english",
            "num_per_page": num_per_page,
            "purchase_type": "all",
        },
        timeout=15,
    )
    if resp.status_code in (429, 503, 403, 451):
        raise SteamReviewsBlocked(f"{resp.status_code} for Steam appreviews {app_id}")
    resp.raise_for_status()
    data = resp.json()

    raw_reviews = data.get("reviews") or []
    result = []
    for r in raw_reviews:
        text = (r.get("review") or "").strip()
        if not text:
            continue
        result.append({
            "text": text[:600],
            "score": int(r.get("votes_up") or 0),
            "is_positive": bool(r.get("voted_up", True)),
        })
    return result


def fetch_steam_reviews(
    app_id: str | None,
    num_per_page: int = 50,
    cache: ApiCache | None = None,
    ttl_hours: int = 24,
) -> list[dict]:
    """
    Fetch recent Steam reviews for a game via the public store reviews endpoint.
    No API key required.

    Returns list of {"text": str, "score": int, "is_positive": bool}.
    text is capped at 600 chars. score = votes_up (used as engagement weight).
    Returns [] if app_id is None. On endpoint errors, serves stale cache when available.
    """
    if not app_id:
        return []

    key = f"recent:{app_id}:{num_per_page}"
    if cache:
        fresh = cache.get(key, max_age_hours=ttl_hours)
        if isinstance(fresh, list):
            return fresh

    try:
        reviews = _fetch_steam_reviews_uncached(app_id, num_per_page)
        if cache:
            cache.set(key, reviews)
        return reviews
    except Exception:
        if cache:
            stale = cache.get(key)
            if isinstance(stale, list):
                return stale
        return []
