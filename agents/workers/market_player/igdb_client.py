import time
import requests
from datetime import datetime, timedelta
from typing import Optional

IGDB_BASE = "https://api.igdb.com/v4"
TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"

# 10 years back — used to exclude very old catalog entries from company queries
_CUTOFF_10Y = int((datetime.now() - timedelta(days=365 * 10)).timestamp())

_GAME_FIELDS = (
    "fields id, name, genres.name, game_modes.name, "
    "first_release_date, platforms.name;"
)


def get_access_token(client_id: str, client_secret: str) -> str:
    resp = requests.post(
        TWITCH_TOKEN_URL,
        params={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _post(endpoint: str, body: str, client_id: str, token: str) -> list[dict]:
    time.sleep(0.26)  # stay under 4 req/sec IGDB rate limit
    resp = requests.post(
        f"{IGDB_BASE}/{endpoint}",
        headers={
            "Client-ID": client_id,
            "Authorization": f"Bearer {token}",
        },
        data=body,
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def get_company_id(client_id: str, token: str, name: str) -> Optional[int]:
    escaped = name.replace('"', '\\"')
    # Exact match first (fastest, most precise)
    results = _post(
        "companies",
        f'fields id, name; where name = "{escaped}"; limit 1;',
        client_id,
        token,
    )
    if results:
        return results[0]["id"]
    # Contains-match fallback for name variants (e.g. "Bandai Namco" → "Bandai Namco Entertainment")
    results = _post(
        "companies",
        f'fields id, name; where name ~ *"{escaped}"*; limit 10;',
        client_id,
        token,
    )
    if not results:
        return None
    name_lower = name.lower()
    for r in results:
        if r.get("name", "").lower() == name_lower:
            return r["id"]
    return results[0]["id"]


def _parse_game(raw: dict) -> dict:
    genres = [g["name"] for g in raw.get("genres") or []]
    game_modes = [m["name"] for m in raw.get("game_modes") or []]
    platforms = [p["name"] for p in raw.get("platforms") or []]
    release_ts = raw.get("first_release_date")
    release_date = (
        datetime.utcfromtimestamp(release_ts).strftime("%Y-%m-%d") if release_ts else None
    )
    is_live_service = "Massively Multiplayer Online (MMO)" in genres or any(
        "massively multiplayer" in m.lower() for m in game_modes
    )
    return {
        "igdb_id": str(raw["id"]),
        "title": raw.get("name", ""),
        "genres": genres,
        "platforms": platforms,
        "release_date": release_date,
        "is_live_service": is_live_service,
        "steam_app_id": None,
        "rawg_slug": None,
        "ccu": None,
        "studio_name": None,
        "ticker": None,
    }


def get_games_by_company(client_id: str, token: str, company_id: int) -> list[dict]:
    """
    Fetch a publisher's game catalog from IGDB.
    Note: IGDB links games to the direct studio/publisher, not the parent holding company,
    so results per company_id will typically be in the tens, not hundreds.
    """
    now_ts = int(datetime.now().timestamp())
    games = []
    offset = 0
    while True:
        body = (
            f"{_GAME_FIELDS}"
            f" where involved_companies.company = {company_id}"
            f" & first_release_date > {_CUTOFF_10Y}"
            f" & first_release_date < {now_ts};"
            f" limit 500; offset {offset};"
        )
        batch = _post("games", body, client_id, token)
        if not batch:
            break
        games.extend(_parse_game(g) for g in batch)
        if len(batch) < 500:
            break
        offset += 500
    return games


def get_recent_releases(client_id: str, token: str, days_back: int = 730) -> list[dict]:
    """Fetch all games released in the past N days across any platform."""
    cutoff = int((datetime.now() - timedelta(days=days_back)).timestamp())
    now_ts = int(datetime.now().timestamp())
    games = []
    offset = 0
    while offset < 2000:
        body = (
            f"{_GAME_FIELDS}"
            f" where first_release_date > {cutoff}"
            f" & first_release_date < {now_ts};"
            f" sort first_release_date desc;"
            f" limit 500; offset {offset};"
        )
        batch = _post("games", body, client_id, token)
        if not batch:
            break
        games.extend(_parse_game(g) for g in batch)
        if len(batch) < 500:
            break
        offset += 500
    return games
