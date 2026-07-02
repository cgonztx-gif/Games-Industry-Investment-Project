"""
Market & Player Data Worker — Phase 2

Reads every active watchlist game with a Steam ID, fetches current CCU from the
official Steam Web API and review metrics from cached Steam appreviews, then
writes one row per game into player_metrics.

Returns a structured summary dict consumed by the orchestrator.
"""

import os
from datetime import date

from agents.workers.market_player.steam_client import get_app_metrics
from database.api_cache import SupabaseApiCache
from database.db_client import (
    get_client,
    get_watchlist_games,
    get_last_player_metrics,
    write_player_metrics,
)


def run() -> dict:
    db = get_client()
    review_cache = SupabaseApiCache(client=db, source="steam_appreviews")
    today = date.today().isoformat()

    all_games = get_watchlist_games(db)
    steam_games = [g for g in all_games if g.get("steam_app_id")]
    skipped = len(all_games) - len(steam_games)

    print(f"[market_player] {len(steam_games)} games with Steam IDs | {skipped} skipped (no Steam ID)")

    processed: list[dict] = []
    errors: list[dict] = []

    for i, game in enumerate(steam_games, 1):
        game_id = game["game_id"]
        title = game.get("title", "unknown")
        steam_id = game["steam_app_id"]

        try:
            metrics = get_app_metrics(steam_id, review_cache=review_cache)

            prev = get_last_player_metrics(db, game_id)
            velocity = None
            if prev and metrics.get("review_count") is not None:
                velocity = metrics["review_count"] - (prev.get("review_count") or 0)

            write_player_metrics(db, {
                "game_id": game_id,
                "date": today,
                "concurrent_players": metrics.get("ccu"),
                "peak_players_24h": None,
                "review_score": metrics.get("review_score"),
                "review_count": metrics.get("review_count"),
                "review_velocity": velocity,
            })

            processed.append({
                "game_id": game_id,
                "title": title,
                "ccu": metrics.get("ccu") or 0,
                "review_score": metrics.get("review_score"),
            })

        except Exception as exc:
            errors.append({"title": title, "steam_app_id": steam_id, "error": str(exc)})

        if i % 25 == 0:
            print(f"  [{i}/{len(steam_games)}] processed …")

    top_10 = sorted(processed, key=lambda x: x["ccu"], reverse=True)[:10]

    print(f"[market_player] Complete — {len(processed)} written, {len(errors)} errors.")

    return {
        "date": today,
        "games_processed": len(processed),
        "games_skipped_no_steam_id": skipped,
        "error_count": len(errors),
        "top_10_by_ccu": top_10,
        "errors": errors,
    }
