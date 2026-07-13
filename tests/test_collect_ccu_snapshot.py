"""
Unit tests for scripts/collect_ccu_snapshot.py.

run() takes injectable ``db`` / ``get_watchlist_games_fn`` / ``get_current_players_fn`` /
``write_fn`` / ``sleep_fn``, so every test drives the per-game loop and error
isolation with fakes -- no live network/DB calls.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import collect_ccu_snapshot

_FAKE_DB = "fake-db-sentinel"  # only passed through to injected fakes


def _games(*rows):
    return lambda db: list(rows)


def test_writes_one_row_per_game_with_steam_id():
    written = []

    result = collect_ccu_snapshot.run(
        db=_FAKE_DB,
        get_watchlist_games_fn=_games(
            {"game_id": "g1", "title": "Game One", "steam_app_id": "111"},
            {"game_id": "g2", "title": "Game Two", "steam_app_id": "222"},
        ),
        get_current_players_fn=lambda steam_id: {"111": 500, "222": 900}[steam_id],
        write_fn=lambda db, rows: written.extend(rows),
        sleep_fn=lambda seconds: None,
    )

    assert written == [
        {"game_id": "g1", "concurrent_players": 500},
        {"game_id": "g2", "concurrent_players": 900},
    ]
    assert result == {
        "games_processed": 2,
        "games_skipped_no_steam_id": 0,
        "error_count": 0,
        "errors": [],
    }


def test_skips_games_without_steam_app_id():
    written = []

    result = collect_ccu_snapshot.run(
        db=_FAKE_DB,
        get_watchlist_games_fn=_games(
            {"game_id": "g1", "title": "Has Steam", "steam_app_id": "111"},
            {"game_id": "g2", "title": "No Steam", "steam_app_id": None},
        ),
        get_current_players_fn=lambda steam_id: 500,
        write_fn=lambda db, rows: written.extend(rows),
        sleep_fn=lambda seconds: None,
    )

    assert written == [{"game_id": "g1", "concurrent_players": 500}]
    assert result["games_skipped_no_steam_id"] == 1


def test_one_game_failing_does_not_block_the_rest():
    written = []

    def fake_get_current_players(steam_id):
        if steam_id == "bad":
            raise RuntimeError("Steam 503")
        return 42

    result = collect_ccu_snapshot.run(
        db=_FAKE_DB,
        get_watchlist_games_fn=_games(
            {"game_id": "g1", "title": "Broken", "steam_app_id": "bad"},
            {"game_id": "g2", "title": "Fine", "steam_app_id": "good"},
        ),
        get_current_players_fn=fake_get_current_players,
        write_fn=lambda db, rows: written.extend(rows),
        sleep_fn=lambda seconds: None,
    )

    assert written == [{"game_id": "g2", "concurrent_players": 42}]
    assert result["error_count"] == 1
    assert result["errors"][0]["title"] == "Broken"


def test_no_steam_games_writes_nothing():
    written = []

    result = collect_ccu_snapshot.run(
        db=_FAKE_DB,
        get_watchlist_games_fn=_games({"game_id": "g1", "title": "No Steam", "steam_app_id": None}),
        get_current_players_fn=lambda steam_id: 1,
        write_fn=lambda db, rows: written.extend(rows),
        sleep_fn=lambda seconds: None,
    )

    assert written == []
    assert result["games_processed"] == 0
    assert result["games_skipped_no_steam_id"] == 1
