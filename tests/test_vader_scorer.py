"""
Unit tests for agents/workers/sentiment/vader_scorer.py (pure logic, no I/O).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.workers.sentiment.vader_scorer import score_texts


def test_empty_list_returns_neutral():
    assert score_texts([]) == 5.5


def test_positive_text_scores_above_neutral():
    score = score_texts([{"text": "This game is absolutely amazing, I love it!", "score": 10}])
    assert score > 5.5


def test_negative_text_scores_below_neutral():
    score = score_texts([{"text": "Terrible, broken, worst game I have ever played.", "score": 10}])
    assert score < 5.5


def test_score_is_bounded_1_to_10():
    hi = score_texts([{"text": "perfect wonderful amazing best", "score": 1}])
    lo = score_texts([{"text": "awful horrible disgusting worst", "score": 1}])
    assert 1.0 <= lo <= 10.0
    assert 1.0 <= hi <= 10.0


def test_engagement_weighting_pulls_toward_high_score_texts():
    texts = [
        {"text": "This game is absolutely amazing, I love it!", "score": 500},
        {"text": "Terrible, broken, worst game I have ever played.", "score": 1},
    ]
    weighted = score_texts(texts)
    flipped = score_texts([
        {"text": "This game is absolutely amazing, I love it!", "score": 1},
        {"text": "Terrible, broken, worst game I have ever played.", "score": 500},
    ])
    assert weighted > flipped


def test_negative_vote_scores_are_floored_at_weight_1():
    # A heavily-downvoted text must not get zero/negative weight (division by
    # a non-positive total) -- max(1, score) floors it.
    score = score_texts([{"text": "meh", "score": -50}])
    assert 1.0 <= score <= 10.0


def test_missing_score_key_defaults_to_weight_1():
    score = score_texts([{"text": "pretty good fun game"}])
    assert 1.0 <= score <= 10.0
