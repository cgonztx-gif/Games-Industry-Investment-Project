"""
Regression test for agents/synthesis/agent.py::_sentiment_by_game.

Guards against reintroducing the bug found while planning the news-article
stream (docs/news-source-decision-memo.md): source='news' rows must never be
averaged into avg_score alongside reddit/steam/youtube, since news measures
media tone/stance, not community sentiment. This also covers the
news_community_divergence signal added to _compute_divergence.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.synthesis.agent import _compute_divergence, _sentiment_by_game


def _row(game_id, source, score, themes=None):
    return {
        "game_id": game_id,
        "source": source,
        "sentiment_score": score,
        "top_themes": themes or [],
    }


def test_sentiment_by_game_excludes_news_from_avg_score():
    rows = [
        _row("g1", "reddit", 6.0, [{"aspect": "monetization", "polarity": "negative"}]),
        _row("g1", "steam", 8.0),
        _row("g1", "news", 2.0, [{"frame": "controversy", "stance": "negative"}]),
    ]

    result = _sentiment_by_game(rows)

    assert result["g1"]["avg_score"] == 7.0  # (6.0 + 8.0) / 2, news excluded
    assert result["g1"]["news_score"] == 2.0
    assert result["g1"]["themes"] == [{"aspect": "monetization", "polarity": "negative"}]
    assert result["g1"]["news_themes"] == [{"frame": "controversy", "stance": "negative"}]
    assert result["g1"]["sources"] == ["news", "reddit", "steam"]


def test_sentiment_by_game_news_score_none_when_no_news_rows():
    rows = [_row("g1", "reddit", 6.0)]

    result = _sentiment_by_game(rows)

    assert result["g1"]["avg_score"] == 6.0
    assert result["g1"]["news_score"] is None


def test_compute_divergence_flags_news_community_disagreement():
    outputs = {
        "player_metrics": [{"game_id": "g1", "concurrent_players": 1000, "review_velocity": 10}],
        "sentiment": [
            _row("g1", "reddit", 7.0),
            _row("g1", "news", 3.0),
        ],
        "patch_events": [],
    }

    divergences = _compute_divergence(outputs)

    types = {d["type"] for d in divergences}
    assert "news_community_divergence" in types
    news_div = next(d for d in divergences if d["type"] == "news_community_divergence")
    assert news_div["sentiment_score"] == 7.0
    assert news_div["news_score"] == 3.0


def test_compute_divergence_no_news_signal_when_scores_agree():
    outputs = {
        "player_metrics": [{"game_id": "g1", "concurrent_players": 1000, "review_velocity": 10}],
        "sentiment": [
            _row("g1", "reddit", 7.0),
            _row("g1", "news", 6.5),
        ],
        "patch_events": [],
    }

    divergences = _compute_divergence(outputs)

    assert not any(d["type"] == "news_community_divergence" for d in divergences)
