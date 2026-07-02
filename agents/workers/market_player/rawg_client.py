import re
import time
import requests
from difflib import SequenceMatcher
from typing import Optional

RAWG_BASE = "https://api.rawg.io/api"
MATCH_SCORE_THRESHOLD = 0.72
SUBTITLE_SCORE_THRESHOLD = 0.58
IGNORED_TITLE_TOKENS = {
    "a",
    "an",
    "and",
    "bundle",
    "collectors",
    "complete",
    "deluxe",
    "digital",
    "dlc",
    "edition",
    "expansion",
    "game",
    "gold",
    "launch",
    "of",
    "pack",
    "pass",
    "remaster",
    "remastered",
    "season",
    "special",
    "standard",
    "the",
    "ultimate",
    "update",
    "year",
}


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

    scored_results: list[tuple[float, dict]] = []
    for result in results:
        score = _title_match_score(title, result.get("name", ""))
        if score <= 0:
            continue
        if year and not _within_release_window(result.get("released"), year):
            continue
        scored_results.append((score, result))

    if not scored_results:
        return None

    score, result = max(scored_results, key=lambda item: item[0])
    parsed = _parse(result)
    parsed["match_score"] = round(score, 3)
    return parsed


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
        "rawg_name": raw.get("name"),
        "metacritic": raw.get("metacritic"),
        "esrb_rating": (raw.get("esrb_rating") or {}).get("name"),
    }


def _within_release_window(released: str | None, target_year: int) -> bool:
    if not released or len(released) < 4:
        return True
    try:
        release_year = int(released[:4])
    except ValueError:
        return True
    return abs(release_year - target_year) <= 1


def _normalize_title(title: str) -> str:
    normalized = title.lower()
    normalized = normalized.replace("&", " and ")
    normalized = re.sub(r"['’]", "", normalized)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _important_tokens(normalized_title: str) -> list[str]:
    return [
        token
        for token in normalized_title.split()
        if (len(token) > 1 or token.isdigit()) and token not in IGNORED_TITLE_TOKENS
    ]


def _numeric_tokens(tokens: list[str]) -> set[str]:
    return {token for token in tokens if token.isdigit()}


def _title_match_score(query_title: str, candidate_title: str) -> float:
    query_norm = _normalize_title(query_title)
    candidate_norm = _normalize_title(candidate_title)
    if not query_norm or not candidate_norm:
        return 0.0
    if query_norm == candidate_norm:
        return 1.0

    query_tokens = _important_tokens(query_norm)
    candidate_tokens = _important_tokens(candidate_norm)
    if not query_tokens or not candidate_tokens:
        return 0.0

    query_token_set = set(query_tokens)
    candidate_token_set = set(candidate_tokens)
    overlap_count = len(query_token_set & candidate_token_set)
    if overlap_count == 0:
        return 0.0

    # Do not accept a different numbered sequel/prequel as the match.
    query_numbers = _numeric_tokens(query_tokens)
    candidate_numbers = _numeric_tokens(candidate_tokens)
    if candidate_numbers - query_numbers:
        return 0.0

    query_overlap = overlap_count / len(query_token_set)
    candidate_overlap = overlap_count / len(candidate_token_set)
    sequence_score = SequenceMatcher(None, query_norm, candidate_norm).ratio()

    if query_norm.startswith(candidate_norm) or candidate_norm.startswith(query_norm):
        if query_overlap >= 0.65 and candidate_overlap >= 0.8 and sequence_score >= SUBTITLE_SCORE_THRESHOLD:
            return max(sequence_score, candidate_overlap)

    if query_overlap >= 0.8 and candidate_overlap >= 0.8 and sequence_score >= MATCH_SCORE_THRESHOLD:
        return sequence_score

    return 0.0
