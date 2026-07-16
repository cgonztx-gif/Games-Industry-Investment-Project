"""
Unit tests for agents/workers/patch_notes/worker.py::run() (plus the pure
cadence helpers, which had no direct test coverage before this pass).

No live network / Supabase calls -- every external touch point (DB client,
Steam News client, blog client) is monkeypatched on the worker module's own
namespace, matching the convention tests/test_news_worker.py establishes.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agents.workers.patch_notes.worker as patch_worker

_FAKE_DB = object()  # sentinel -- no real Supabase calls


def _game(
    game_id="g1",
    title="Apex Legends",
    steam_app_id="1172470",
    genre="battle royale",
    is_live_service=True,
):
    return {
        "game_id": game_id,
        "title": title,
        "steam_app_id": steam_app_id,
        "genre": genre,
        "is_live_service": is_live_service,
    }


def _news_item(date="2026-07-01", title="Update 1.2", contents="patch notes", patch_type="content_drop", url="https://a"):
    return {"date": date, "title": title, "contents": contents, "url": url, "patch_type": patch_type}


def _run(
    games=None,
    patch_pages=None,
    news_by_steam_id=None,
    news_raises_for=None,
    blog_entries_by_url=None,
    blog_raises_for=None,
    last_event_by_game=None,
    written=None,
    monkeypatch=None,
):
    if games is None:
        games = [_game()]
    if patch_pages is None:
        patch_pages = {}
    if news_by_steam_id is None:
        news_by_steam_id = {}
    if news_raises_for is None:
        news_raises_for = set()
    if blog_entries_by_url is None:
        blog_entries_by_url = {}
    if blog_raises_for is None:
        blog_raises_for = set()
    if last_event_by_game is None:
        last_event_by_game = {}
    if written is None:
        written = []

    monkeypatch.setattr(patch_worker, "get_client", lambda: _FAKE_DB)
    monkeypatch.setattr(patch_worker, "get_watchlist_games", lambda db: games)
    monkeypatch.setattr(patch_worker, "load_game_patch_pages", lambda: patch_pages)
    monkeypatch.setattr(patch_worker, "SupabaseApiCache", lambda client, source: None)
    monkeypatch.setattr(patch_worker, "CachedBlogSource", lambda cache: None)

    def _fake_get_recent_news(steam_app_id, days_back=45):
        if steam_app_id in news_raises_for:
            raise RuntimeError(f"steam news down for {steam_app_id}")
        return news_by_steam_id.get(steam_app_id, [])

    monkeypatch.setattr(patch_worker, "get_recent_news", _fake_get_recent_news)

    def _fake_get_recent_entries(source, url, days_back=45):
        if url in blog_raises_for:
            raise RuntimeError(f"blog fetch down for {url}")
        return blog_entries_by_url.get(url, [])

    monkeypatch.setattr(patch_worker, "get_recent_entries", _fake_get_recent_entries)
    monkeypatch.setattr(
        patch_worker, "get_last_patch_event", lambda db, game_id: last_event_by_game.get(game_id)
    )

    def _fake_write(db, event):
        written.append(event)
        return True

    monkeypatch.setattr(patch_worker, "write_patch_event", _fake_write)

    return patch_worker.run()


# ---------------------------------------------------------------------------
# Pure helper unit tests (no prior direct coverage)
# ---------------------------------------------------------------------------

def test_resolve_baseline_days_non_live_service_uses_default():
    assert patch_worker._resolve_baseline_days("RPG", is_live_service=False) == 30


def test_resolve_baseline_days_live_service_genre_match():
    assert patch_worker._resolve_baseline_days("Battle Royale", is_live_service=True) == 10


def test_resolve_baseline_days_live_service_no_genre_match_falls_back():
    assert patch_worker._resolve_baseline_days("Puzzle", is_live_service=True) == 21


def test_cadence_status_none_delta_is_baseline():
    assert patch_worker._cadence_status(None, 10, True) == "baseline"


def test_cadence_status_absent_above_3x_baseline():
    assert patch_worker._cadence_status(31, 10, True) == "absent"


def test_cadence_status_slowing_above_1_5x_baseline():
    assert patch_worker._cadence_status(16, 10, True) == "slowing"


def test_cadence_status_on_pace_within_baseline():
    assert patch_worker._cadence_status(5, 10, True) == "on_pace"


def test_cadence_status_non_live_service_always_on_pace():
    # Non-live-service titles just track pace -- no slowing/absent flags.
    assert patch_worker._cadence_status(9999, 10, False) == "on_pace"


# ---------------------------------------------------------------------------
# run() -- happy path
# ---------------------------------------------------------------------------

def test_happy_path_writes_events_for_steam_news(monkeypatch):
    games = [_game("g1", "Apex Legends", "1172470")]
    news = {"1172470": [_news_item(date="2026-07-01")]}
    written = []

    result = _run(games=games, news_by_steam_id=news, written=written, monkeypatch=monkeypatch)

    assert result["games_checked"] == 1
    assert result["events_written"] == 1
    assert result["error_count"] == 0
    assert written[0]["game_id"] == "g1"
    assert written[0]["cadence_baseline_days"] == 10  # battle royale baseline


def test_blog_configured_game_without_steam_id_is_included(monkeypatch):
    games = [
        {
            "game_id": "g2",
            "title": "Blog Only Game",
            "steam_app_id": None,
            "genre": None,
            "is_live_service": False,
        }
    ]
    patch_pages = {"Blog Only Game": ["https://blog.example.com/rss"]}
    blog_entries = {"https://blog.example.com/rss": [_news_item(date="2026-07-02", url="https://blog.example.com/rss")]}
    written = []

    result = _run(
        games=games,
        patch_pages=patch_pages,
        blog_entries_by_url=blog_entries,
        written=written,
        monkeypatch=monkeypatch,
    )

    assert result["games_checked"] == 1
    assert result["events_written"] == 1


def test_monetization_without_content_flagged(monkeypatch):
    games = [_game("g1", "Apex Legends", "1172470")]
    news = {
        "1172470": [
            _news_item(
                date="2026-07-01",
                title="New Battle Pass!",
                contents="Get the new bundle in the store now.",
                patch_type="monetization",
            )
        ]
    }
    written = []

    result = _run(games=games, news_by_steam_id=news, written=written, monkeypatch=monkeypatch)

    assert result["monetization_without_content_count"] == 1
    assert written[0]["monetization_without_content"] is True


def test_cadence_delta_computed_against_last_patch_event(monkeypatch):
    games = [_game("g1", "Apex Legends", "1172470", genre="battle royale")]
    news = {"1172470": [_news_item(date="2026-07-20")]}
    last_event = {"g1": {"date": "2026-07-01"}}
    written = []

    _run(
        games=games,
        news_by_steam_id=news,
        last_event_by_game=last_event,
        written=written,
        monkeypatch=monkeypatch,
    )

    # 19 days since last event vs. 10-day battle-royale baseline * 1.5 = 15 -> slowing
    assert written[0]["cadence_delta"] == 19
    assert written[0]["cadence_status"] == "slowing"


# ---------------------------------------------------------------------------
# run() -- degraded paths
# ---------------------------------------------------------------------------

def test_game_with_no_steam_id_and_no_patch_page_is_excluded(monkeypatch):
    games = [
        _game("g1", "Apex Legends", "1172470"),
        {"game_id": "g2", "title": "Untracked Game", "steam_app_id": None, "genre": None, "is_live_service": False},
    ]
    written = []

    result = _run(games=games, written=written, monkeypatch=monkeypatch)

    # g2 has neither a Steam ID nor a configured patch page -- filtered before
    # the loop even starts, so it must not count as "checked" or produce an error.
    assert result["games_checked"] <= 1
    assert result["error_count"] == 0


def test_steam_news_failure_with_no_fallback_source_skips_game(monkeypatch):
    games = [_game("g1", "Apex Legends", "1172470")]
    written = []

    result = _run(
        games=games,
        news_raises_for={"1172470"},
        written=written,
        monkeypatch=monkeypatch,
    )

    # any_source_succeeded stays False (only source configured failed) -> the
    # game is skipped entirely, not counted as checked, but the failure is
    # still recorded as an error.
    assert result["games_checked"] == 0
    assert result["error_count"] == 1
    assert result["events_written"] == 0
    assert written == []


def test_steam_news_failure_falls_back_to_configured_blog(monkeypatch):
    games = [_game("g1", "Apex Legends", "1172470")]
    patch_pages = {"Apex Legends": ["https://blog.example.com/rss"]}
    blog_entries = {"https://blog.example.com/rss": [_news_item(date="2026-07-05", url="https://blog.example.com/rss")]}
    written = []

    result = _run(
        games=games,
        patch_pages=patch_pages,
        news_raises_for={"1172470"},
        blog_entries_by_url=blog_entries,
        written=written,
        monkeypatch=monkeypatch,
    )

    assert result["error_count"] == 1  # the steam_news failure is still logged
    assert result["games_checked"] == 1  # but the blog source succeeded
    assert result["events_written"] == 1


def test_no_recent_events_from_any_source_writes_nothing(monkeypatch):
    games = [_game("g1", "Apex Legends", "1172470")]
    written = []

    result = _run(games=games, news_by_steam_id={"1172470": []}, written=written, monkeypatch=monkeypatch)

    assert result["games_checked"] == 1
    assert result["events_written"] == 0
    assert written == []


def test_empty_watchlist_returns_zeroed_stats(monkeypatch):
    result = _run(games=[], monkeypatch=monkeypatch)

    assert result["games_checked"] == 0
    assert result["events_written"] == 0
    assert result["error_count"] == 0
