import os
from typing import Optional

from supabase import create_client, Client


def get_client() -> Client:
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


def find_or_create_studio(client: Client, studio: dict) -> str:
    """Return studio_id, creating the row if it doesn't exist yet."""
    resp = (
        client.table("studios")
        .select("studio_id")
        .eq("name", studio["name"])
        .execute()
    )
    if resp.data:
        return resp.data[0]["studio_id"]

    resp = (
        client.table("studios")
        .insert(
            {
                "name": studio["name"],
                "ticker": studio.get("ticker"),
                "parent_name": studio.get("parent_name"),
            }
        )
        .execute()
    )
    return resp.data[0]["studio_id"]


def find_or_create_game(client: Client, game: dict, studio_id: str) -> str:
    """Return game_id, creating (or enriching) the row as needed."""
    existing_id: Optional[str] = None

    if game.get("igdb_id"):
        resp = (
            client.table("games")
            .select("game_id")
            .eq("igdb_id", game["igdb_id"])
            .execute()
        )
        if resp.data:
            existing_id = resp.data[0]["game_id"]

    if not existing_id and game.get("steam_app_id"):
        resp = (
            client.table("games")
            .select("game_id")
            .eq("steam_app_id", game["steam_app_id"])
            .execute()
        )
        if resp.data:
            existing_id = resp.data[0]["game_id"]

    if existing_id:
        # Patch in any identifiers we now know that weren't there before
        patch: dict = {}
        if game.get("steam_app_id"):
            patch["steam_app_id"] = game["steam_app_id"]
        if game.get("rawg_slug"):
            patch["rawg_slug"] = game["rawg_slug"]
        if patch:
            client.table("games").update(patch).eq("game_id", existing_id).execute()
        return existing_id

    resp = (
        client.table("games")
        .insert(
            {
                "title": game["title"],
                "studio_id": studio_id,
                "genre": ", ".join(game.get("genres") or []) or None,
                "release_date": game.get("release_date"),
                "is_live_service": game.get("is_live_service", False),
                "steam_app_id": game.get("steam_app_id"),
                "igdb_id": game.get("igdb_id"),
                "rawg_slug": game.get("rawg_slug"),
            }
        )
        .execute()
    )
    return resp.data[0]["game_id"]


def insert_watchlist_entry(
    client: Client, game_id: str, studio_id: str, ticker: Optional[str]
) -> bool:
    """Insert watchlist entry. Returns True if inserted, False if already existed."""
    existing = (
        client.table("watchlist")
        .select("id")
        .eq("game_id", game_id)
        .eq("added_by", "seed")
        .execute()
    )
    if existing.data:
        return False

    client.table("watchlist").insert(
        {
            "game_id": game_id,
            "studio_id": studio_id,
            "ticker": ticker,
            "active": True,
            "added_by": "seed",
        }
    ).execute()
    return True


def get_seeded_game_ids(client: Client) -> set[str]:
    """Return game_ids already in watchlist with added_by='seed' (idempotency check)."""
    resp = (
        client.table("watchlist")
        .select("game_id")
        .eq("added_by", "seed")
        .execute()
    )
    return {row["game_id"] for row in resp.data}


def get_seeded_external_ids(client: Client) -> dict[str, set[str]]:
    """
    Return pre-loaded sets of igdb_ids and steam_app_ids already in the watchlist.
    Used for O(1) early-skip in the seed loop — avoids any Supabase calls for
    games we've already processed, preventing HTTP/2 stream exhaustion on re-runs.
    """
    resp = (
        client.table("watchlist")
        .select("games(igdb_id, steam_app_id)")
        .eq("added_by", "seed")
        .execute()
    )
    igdb_ids: set[str] = set()
    steam_ids: set[str] = set()
    for row in resp.data:
        game = row.get("games") or {}
        if game.get("igdb_id"):
            igdb_ids.add(game["igdb_id"])
        if game.get("steam_app_id"):
            steam_ids.add(game["steam_app_id"])
    return {"igdb_ids": igdb_ids, "steam_ids": steam_ids}


# ---------------------------------------------------------------------------
# Market & Player Data worker helpers
# ---------------------------------------------------------------------------

def get_watchlist_games(client: Client) -> list[dict]:
    """Return all active watchlist games joined with their games row."""
    resp = (
        client.table("watchlist")
        .select("game_id, games(game_id, title, steam_app_id, igdb_id)")
        .eq("active", True)
        .execute()
    )
    result = []
    for row in resp.data:
        game = row.get("games")
        if game:
            result.append({**game, "watchlist_game_id": row["game_id"]})
    return result


def get_last_player_metrics(client: Client, game_id: str) -> Optional[dict]:
    """Most recent player_metrics row for a game (used for review_velocity calc)."""
    resp = (
        client.table("player_metrics")
        .select("review_count, date")
        .eq("game_id", game_id)
        .order("date", desc=True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def write_player_metrics(client: Client, metrics: dict) -> None:
    """Upsert one player_metrics row. Safe to call multiple times per (game_id, date)."""
    client.table("player_metrics").upsert(metrics, on_conflict="game_id,date").execute()


# ---------------------------------------------------------------------------
# Financial Overlay worker helpers
# ---------------------------------------------------------------------------

def get_watchlist_tickers(client: Client) -> list[dict]:
    """
    Return distinct {ticker, studio_id} for all studios with a public ticker.
    Deduplicates by ticker (e.g. TTWO maps to Take-Two, 2K, and Rockstar —
    only the first studio_id encountered is kept).
    """
    resp = (
        client.table("studios")
        .select("studio_id, ticker")
        .not_.is_("ticker", "null")
        .execute()
    )
    seen: dict[str, str] = {}
    for row in resp.data:
        t = row["ticker"]
        if t not in seen:
            seen[t] = row["studio_id"]
    return [{"ticker": t, "studio_id": sid} for t, sid in seen.items()]


def write_equity_metrics(client: Client, metrics: dict) -> None:
    """Upsert one portfolio_positions_context row. Safe to call multiple times per (ticker, date)."""
    client.table("portfolio_positions_context").upsert(metrics, on_conflict="ticker,date").execute()


# ---------------------------------------------------------------------------
# Studio Intel worker helpers
# ---------------------------------------------------------------------------

def get_studios_with_tickers(client: Client) -> list[dict]:
    """
    Return distinct {ticker, studio_id, name} for studios with a public ticker.
    Deduplicates by ticker so each parent company appears once.
    """
    resp = (
        client.table("studios")
        .select("studio_id, ticker, name")
        .not_.is_("ticker", "null")
        .execute()
    )
    seen: dict[str, dict] = {}
    for row in resp.data:
        t = row["ticker"]
        if t not in seen:
            seen[t] = {"ticker": t, "studio_id": row["studio_id"], "name": row["name"]}
    return list(seen.values())


def write_studio_signal(client: Client, signal: dict) -> bool:
    """
    Insert one studio_signals row. Returns True if inserted, False if already exists.
    Idempotency key: studio_id + date + signal_type + source_url.
    """
    existing = (
        client.table("studio_signals")
        .select("id")
        .eq("studio_id", signal["studio_id"])
        .eq("date", signal["date"])
        .eq("signal_type", signal["signal_type"])
        .eq("source_url", signal["source_url"])
        .execute()
    )
    if existing.data:
        return False

    client.table("studio_signals").insert(signal).execute()
    return True
