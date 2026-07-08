"""
Unit tests for agents/workers/financial_overlay/health_score.py.

Pure function, no DB/API calls -- covers each component's data-present and
data-absent paths, and the None-when-no-coverage-at-all floor.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.workers.financial_overlay.health_score import compute_health_score


def _score(**overrides):
    kwargs = dict(
        sentiment_rows=[],
        patch_event_rows=[],
        player_metrics_rows=[],
        studio_ids=[],
        studio_signal_rows=[],
    )
    kwargs.update(overrides)
    return compute_health_score(**kwargs)


def test_no_data_anywhere_returns_none_not_a_fabricated_score():
    score, components = _score()
    assert score is None
    assert components == {}


def test_sentiment_only_averages_latest_scores_across_games():
    score, components = _score(
        sentiment_rows=[
            {"game_id": "a", "date": "2026-07-01", "sentiment_score": 8.0},
            {"game_id": "b", "date": "2026-07-01", "sentiment_score": 6.0},
        ]
    )
    assert components == {"sentiment": 7.0}
    assert score == 7.0


def test_patch_cadence_uses_latest_status_per_game_only():
    score, components = _score(
        patch_event_rows=[
            {"game_id": "a", "date": "2026-06-01", "cadence_status": "absent"},
            {"game_id": "a", "date": "2026-07-01", "cadence_status": "on_pace"},  # newer wins
        ]
    )
    assert components == {"patch_cadence": 10.0}
    assert score == 10.0


def test_player_momentum_needs_two_data_points_per_game():
    score, components = _score(
        player_metrics_rows=[
            {"game_id": "a", "date": "2026-07-01", "concurrent_players": 1000},
        ]
    )
    # Only one data point for game "a" -- momentum can't be computed.
    assert components == {}
    assert score is None


def test_player_momentum_growth_and_decline():
    growth, components = _score(
        player_metrics_rows=[
            {"game_id": "a", "date": "2026-06-25", "concurrent_players": 1000},
            {"game_id": "a", "date": "2026-07-01", "concurrent_players": 1200},
        ]
    )
    assert components["player_momentum"] == 10.0

    decline, components = _score(
        player_metrics_rows=[
            {"game_id": "a", "date": "2026-06-25", "concurrent_players": 1000},
            {"game_id": "a", "date": "2026-07-01", "concurrent_players": 600},
        ]
    )
    assert components["player_momentum"] == 0.0


def test_studio_distress_defaults_clean_when_studio_known_but_no_signals():
    score, components = _score(studio_ids=["studio-1"])
    assert components == {"studio_distress": 10.0}
    assert score == 10.0


def test_studio_distress_omitted_when_no_studio_mapping():
    score, components = _score(studio_ids=[])
    assert "studio_distress" not in components
    assert score is None


def test_studio_distress_takes_worst_severity_in_window():
    score, components = _score(
        studio_ids=["studio-1"],
        studio_signal_rows=[
            {"studio_id": "studio-1", "date": "2026-06-01", "severity": "low"},
            {"studio_id": "studio-1", "date": "2026-06-15", "severity": "high"},
        ],
    )
    assert components["studio_distress"] == 0.0


def test_composite_is_equal_weighted_average_of_available_components():
    score, components = _score(
        sentiment_rows=[{"game_id": "a", "date": "2026-07-01", "sentiment_score": 8.0}],
        patch_event_rows=[{"game_id": "a", "date": "2026-07-01", "cadence_status": "on_pace"}],
        studio_ids=["studio-1"],
    )
    # sentiment=8.0, patch_cadence=10.0, studio_distress=10.0 -> mean 9.3
    assert components == {"sentiment": 8.0, "patch_cadence": 10.0, "studio_distress": 10.0}
    assert score == 9.3
