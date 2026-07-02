"""
Patch Notes Worker - Phase 3

Uses the official Steam News API (ISteamNews/GetNewsForApp) to collect recent
patch/update posts for Steam-linked watchlist games and writes cadence events to
patch_events.
"""

from datetime import date

from agents.workers.patch_notes.steam_news_client import get_recent_news
from database.db_client import (
    get_client,
    get_last_patch_event,
    get_watchlist_games,
    write_patch_event,
)

_DAYS_BACK = 45


def _days_between(previous_date: str | None, current_date: str) -> int | None:
    if not previous_date:
        return None
    return (date.fromisoformat(current_date) - date.fromisoformat(previous_date)).days


def run() -> dict:
    db = get_client()
    games = [game for game in get_watchlist_games(db) if game.get("steam_app_id")]
    print(f"[patch_notes] {len(games)} Steam-linked games to check")

    games_checked = 0
    events_written = 0
    errors: list[dict] = []

    for i, game in enumerate(games, 1):
        game_id = game["game_id"]
        title = game.get("title", "unknown")
        steam_app_id = game["steam_app_id"]

        try:
            news_items = get_recent_news(steam_app_id, days_back=_DAYS_BACK)
            games_checked += 1
            last_event = get_last_patch_event(db, game_id)
            previous_date = last_event.get("date") if last_event else None

            for item in news_items:
                cadence_delta = _days_between(previous_date, item["date"])
                written = write_patch_event(
                    db,
                    {
                        "game_id": game_id,
                        "date": item["date"],
                        "patch_type": item["patch_type"],
                        "scope_summary": f"{item['title']} - {item['contents'][:500]}",
                        "cadence_delta": cadence_delta,
                        "source_url": item.get("url"),
                    },
                )
                previous_date = item["date"]
                if written:
                    events_written += 1

        except Exception as exc:
            errors.append({"title": title, "steam_app_id": steam_app_id, "error": str(exc)})

        if i % 25 == 0:
            print(f"  [{i}/{len(games)}] checked")

    print(
        f"[patch_notes] Complete - {games_checked} checked, "
        f"{events_written} events written, {len(errors)} errors."
    )
    return {
        "games_checked": games_checked,
        "events_written": events_written,
        "error_count": len(errors),
        "errors": errors,
    }
