"""
Patch Notes Worker - Phase 3

Uses the official Steam News API (ISteamNews/GetNewsForApp) plus manually
curated developer-blog / official patch-page URLs (GAME_PATCH_PAGES) to
collect recent patch/update posts for watchlist games and writes cadence
events to patch_events.
"""

from datetime import date

from agents.workers.patch_notes.blog_client import (
    CachedBlogSource,
    get_recent_entries,
    load_game_patch_pages,
)
from agents.workers.patch_notes.steam_news_client import get_recent_news, has_content_indicators
from database.api_cache import SupabaseApiCache
from database.db_client import (
    get_client,
    get_last_patch_event,
    get_watchlist_games,
    write_patch_event,
)

_DAYS_BACK = 45

# Conservative fallback when a game's genre/live-service status is unknown.
_DEFAULT_BASELINE_DAYS = 30

# Live-service baseline by genre keyword (checked against games.genre, case-insensitive).
# Titles not matching any keyword fall back to _LIVE_SERVICE_BASELINE_DAYS.
_LIVE_SERVICE_BASELINE_DAYS = 21
_GENRE_BASELINE_DAYS = {
    "battle royale": 10,
    "shooter": 14,
    "moba": 14,
    "mmo": 21,
    "sports": 30,
}

# Cadence status thresholds, expressed as a multiple of the resolved baseline.
_SLOWING_MULTIPLIER = 1.5
_ABSENT_MULTIPLIER = 3.0


def _days_between(previous_date: str | None, current_date: str) -> int | None:
    if not previous_date:
        return None
    return (date.fromisoformat(current_date) - date.fromisoformat(previous_date)).days


def _resolve_baseline_days(genre: str | None, is_live_service: bool) -> int:
    """Simple cadence baseline: genre-aware for live-service titles, conservative otherwise."""
    if not is_live_service:
        return _DEFAULT_BASELINE_DAYS
    if not genre:
        return _LIVE_SERVICE_BASELINE_DAYS
    genre_lower = genre.lower()
    for keyword, baseline_days in _GENRE_BASELINE_DAYS.items():
        if keyword in genre_lower:
            return baseline_days
    return _LIVE_SERVICE_BASELINE_DAYS


def _cadence_status(cadence_delta: int | None, baseline_days: int, is_live_service: bool) -> str:
    """Flag slowing/absent cadence for live-service titles; non-live-service titles just track pace."""
    if cadence_delta is None:
        return "baseline"
    if is_live_service and cadence_delta > baseline_days * _ABSENT_MULTIPLIER:
        return "absent"
    if is_live_service and cadence_delta > baseline_days * _SLOWING_MULTIPLIER:
        return "slowing"
    return "on_pace"


def run() -> dict:
    db = get_client()
    all_games = get_watchlist_games(db)
    patch_pages = load_game_patch_pages()
    blog_source = CachedBlogSource(SupabaseApiCache(client=db, source="dev_blog"))

    games = [
        game
        for game in all_games
        if game.get("steam_app_id") or patch_pages.get(game.get("title", ""))
    ]
    print(f"[patch_notes] {len(games)} games to check (Steam-linked and/or blog-configured)")

    games_checked = 0
    events_written = 0
    cadence_slowing = 0
    cadence_absent = 0
    monetization_without_content_count = 0
    errors: list[dict] = []

    for i, game in enumerate(games, 1):
        game_id = game["game_id"]
        title = game.get("title", "unknown")
        steam_app_id = game.get("steam_app_id")
        is_live_service = game.get("is_live_service", False)
        baseline_days = _resolve_baseline_days(game.get("genre"), is_live_service)

        news_items: list[dict] = []
        any_source_succeeded = False

        if steam_app_id:
            try:
                news_items.extend(get_recent_news(steam_app_id, days_back=_DAYS_BACK))
                any_source_succeeded = True
            except Exception as exc:
                errors.append(
                    {"title": title, "source": "steam_news", "steam_app_id": steam_app_id, "error": str(exc)}
                )

        for blog_url in patch_pages.get(title, []):
            try:
                news_items.extend(get_recent_entries(blog_source, blog_url, days_back=_DAYS_BACK))
                any_source_succeeded = True
            except Exception as exc:
                errors.append({"title": title, "source": "dev_blog", "url": blog_url, "error": str(exc)})

        if not any_source_succeeded:
            if i % 25 == 0:
                print(f"  [{i}/{len(games)}] checked")
            continue

        games_checked += 1
        news_items.sort(key=lambda item: item["date"])

        last_event = get_last_patch_event(db, game_id)
        previous_date = last_event.get("date") if last_event else None

        for item in news_items:
            cadence_delta = _days_between(previous_date, item["date"])
            cadence_status = _cadence_status(cadence_delta, baseline_days, is_live_service)
            monetization_without_content = item["patch_type"] == "monetization" and not (
                has_content_indicators(item["title"], item["contents"])
            )
            written = write_patch_event(
                db,
                {
                    "game_id": game_id,
                    "date": item["date"],
                    "patch_type": item["patch_type"],
                    "scope_summary": f"{item['title']} - {item['contents'][:500]}",
                    "cadence_delta": cadence_delta,
                    "cadence_status": cadence_status,
                    "cadence_baseline_days": baseline_days,
                    "monetization_without_content": monetization_without_content,
                    "source_url": item.get("url"),
                },
            )
            previous_date = item["date"]
            if written:
                events_written += 1
                if cadence_status == "slowing":
                    cadence_slowing += 1
                elif cadence_status == "absent":
                    cadence_absent += 1
                if monetization_without_content:
                    monetization_without_content_count += 1

        if i % 25 == 0:
            print(f"  [{i}/{len(games)}] checked")

    print(
        f"[patch_notes] Complete - {games_checked} checked, "
        f"{events_written} events written, {len(errors)} errors."
    )
    return {
        "games_checked": games_checked,
        "events_written": events_written,
        "cadence_slowing_count": cadence_slowing,
        "cadence_absent_count": cadence_absent,
        "monetization_without_content_count": monetization_without_content_count,
        "error_count": len(errors),
        "errors": errors,
    }
