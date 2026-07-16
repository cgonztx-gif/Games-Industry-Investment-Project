"""
Unit tests for agents/workers/market_player/worker.py::run().

No live network / Supabase calls -- every external touch point (DB client,
Steam metrics fetch, api_cache) is monkeypatched on the worker module's own
namespace, matching the convention tests/test_news_worker.py establishes for
workers that don't yet use dependency injection.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agents.workers.market_player.worker as market_worker

_FAKE_DB = object()  # sentinel -- no real Supabase calls


def _game(game_id="g1", title="Elden Ring", steam_app_id="123"):
    return {"game_id": game_id, "title": title, "steam_app_id": steam_app_id}


def _run(
    games=None,
    metrics_by_app_id=None,
    peak_map=None,
    last_metrics_by_game=None,
    written=None,
    metrics_raises_for=None,
    monkeypatch=None,
):
    if games is None:
        games = [_game()]
    if metrics_by_app_id is None:
        metrics_by_app_id = {}
    if peak_map is None:
        peak_map = {}
    if last_metrics_by_game is None:
        last_metrics_by_game = {}
    if written is None:
        written = []
    if metrics_raises_for is None:
        metrics_raises_for = set()

    monkeypatch.setattr(market_worker, "get_client", lambda: _FAKE_DB)
    monkeypatch.setattr(market_worker, "get_watchlist_games", lambda db: games)
    monkeypatch.setattr(market_worker, "SupabaseApiCache", lambda client, source: None)
    monkeypatch.setattr(
        market_worker, "get_peak_players_24h_map", lambda: peak_map
    )

    def _fake_get_app_metrics(steam_id, review_cache=None):
        if steam_id in metrics_raises_for:
            raise RuntimeError(f"steam api down for {steam_id}")
        return metrics_by_app_id.get(
            steam_id, {"ccu": 100, "review_score": 90.0, "review_count": 50}
        )

    monkeypatch.setattr(market_worker, "get_app_metrics", _fake_get_app_metrics)
    monkeypatch.setattr(
        market_worker,
        "get_last_player_metrics",
        lambda db, game_id: last_metrics_by_game.get(game_id),
    )

    def _fake_write(db, row):
        written.append(row)

    monkeypatch.setattr(market_worker, "write_player_metrics", _fake_write)

    return market_worker.run()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_happy_path_writes_one_row_per_game(monkeypatch):
    games = [_game("g1", "Elden Ring", "100"), _game("g2", "Hades II", "200")]
    metrics = {
        "100": {"ccu": 5000, "review_score": 95.0, "review_count": 1000},
        "200": {"ccu": 2000, "review_score": 88.0, "review_count": 400},
    }
    written = []

    result = _run(games=games, metrics_by_app_id=metrics, written=written, monkeypatch=monkeypatch)

    assert result["games_processed"] == 2
    assert result["games_skipped_no_steam_id"] == 0
    assert result["error_count"] == 0
    assert len(written) == 2
    assert {w["game_id"] for w in written} == {"g1", "g2"}


def test_top_10_by_ccu_sorted_descending(monkeypatch):
    games = [_game("g1", "Low", "1"), _game("g2", "High", "2")]
    metrics = {
        "1": {"ccu": 10, "review_score": 50.0, "review_count": 5},
        "2": {"ccu": 9000, "review_score": 90.0, "review_count": 900},
    }

    result = _run(games=games, metrics_by_app_id=metrics, monkeypatch=monkeypatch)

    top = result["top_10_by_ccu"]
    assert top[0]["title"] == "High"
    assert top[1]["title"] == "Low"


def test_review_velocity_computed_against_previous_snapshot(monkeypatch):
    games = [_game("g1", "Elden Ring", "100")]
    metrics = {"100": {"ccu": 5000, "review_score": 95.0, "review_count": 1200}}
    last_metrics = {"g1": {"review_count": 1000, "date": "2026-07-01"}}
    written = []

    _run(
        games=games,
        metrics_by_app_id=metrics,
        last_metrics_by_game=last_metrics,
        written=written,
        monkeypatch=monkeypatch,
    )

    assert written[0]["review_velocity"] == 200


def test_review_velocity_none_when_no_previous_snapshot(monkeypatch):
    games = [_game("g1", "Elden Ring", "100")]
    metrics = {"100": {"ccu": 5000, "review_score": 95.0, "review_count": 1200}}
    written = []

    _run(games=games, metrics_by_app_id=metrics, written=written, monkeypatch=monkeypatch)

    assert written[0]["review_velocity"] is None


def test_peak_players_24h_mapped_by_steam_app_id(monkeypatch):
    games = [_game("g1", "Elden Ring", "100")]
    written = []

    _run(
        games=games,
        peak_map={"100": 12345},
        written=written,
        monkeypatch=monkeypatch,
    )

    assert written[0]["peak_players_24h"] == 12345


# ---------------------------------------------------------------------------
# Degraded paths
# ---------------------------------------------------------------------------

def test_games_without_steam_app_id_are_skipped(monkeypatch):
    games = [
        _game("g1", "Elden Ring", "100"),
        {"game_id": "g2", "title": "No Steam ID Game", "steam_app_id": None},
    ]
    written = []

    result = _run(games=games, written=written, monkeypatch=monkeypatch)

    assert result["games_skipped_no_steam_id"] == 1
    assert result["games_processed"] == 1
    assert len(written) == 1


def test_peak_map_fetch_failure_degrades_to_empty_map_not_crash(monkeypatch):
    games = [_game("g1", "Elden Ring", "100")]

    def _boom():
        raise RuntimeError("steam charts down")

    monkeypatch.setattr(market_worker, "get_client", lambda: _FAKE_DB)
    monkeypatch.setattr(market_worker, "get_watchlist_games", lambda db: games)
    monkeypatch.setattr(market_worker, "SupabaseApiCache", lambda client, source: None)
    monkeypatch.setattr(market_worker, "get_peak_players_24h_map", _boom)
    monkeypatch.setattr(
        market_worker,
        "get_app_metrics",
        lambda steam_id, review_cache=None: {"ccu": 100, "review_score": 90.0, "review_count": 10},
    )
    monkeypatch.setattr(market_worker, "get_last_player_metrics", lambda db, game_id: None)
    written = []
    monkeypatch.setattr(market_worker, "write_player_metrics", lambda db, row: written.append(row))

    result = market_worker.run()

    assert result["games_processed"] == 1
    assert written[0]["peak_players_24h"] is None


def test_per_game_error_is_isolated_and_run_continues(monkeypatch):
    games = [_game("g1", "Broken Game", "100"), _game("g2", "Fine Game", "200")]
    written = []

    result = _run(
        games=games,
        metrics_by_app_id={"200": {"ccu": 500, "review_score": 80.0, "review_count": 20}},
        metrics_raises_for={"100"},
        written=written,
        monkeypatch=monkeypatch,
    )

    assert result["error_count"] == 1
    assert result["games_processed"] == 1
    assert written[0]["game_id"] == "g2"
    assert result["errors"][0]["title"] == "Broken Game"


def test_empty_watchlist_returns_zeroed_stats(monkeypatch):
    result = _run(games=[], monkeypatch=monkeypatch)

    assert result["games_processed"] == 0
    assert result["games_skipped_no_steam_id"] == 0
    assert result["error_count"] == 0
    assert result["top_10_by_ccu"] == []
