import time
import requests

_REVIEWS_URL = "https://store.steampowered.com/appreviews/{app_id}"
_REQUEST_DELAY = 0.5  # seconds between requests


def fetch_steam_reviews(app_id: str | None, num_per_page: int = 50) -> list[dict]:
    """
    Fetch recent Steam reviews for a game via the public store reviews endpoint.
    No API key required.

    Returns list of {"text": str, "score": int, "is_positive": bool}.
    text is capped at 600 chars. score = votes_up (used as engagement weight).
    Returns [] on any error or if app_id is None.
    """
    if not app_id:
        return []

    time.sleep(_REQUEST_DELAY)

    try:
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
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

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
