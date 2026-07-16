"""
Unit tests for agents/workers/sentiment/divergence.py (pure logic, no I/O).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.workers.sentiment.divergence import compute_divergence


def test_no_player_metrics_never_flags():
    assert compute_divergence(1.0, None) == (False, None)


def test_thin_review_base_never_flags():
    flag, note = compute_divergence(2.0, {"review_count": 99, "date": "2026-07-01"})
    assert flag is False
    assert note is None


def test_null_review_count_treated_as_zero():
    flag, note = compute_divergence(2.0, {"review_count": None, "date": "2026-07-01"})
    assert flag is False


def test_bearish_score_with_large_review_base_flags():
    flag, note = compute_divergence(3.5, {"review_count": 5000, "date": "2026-07-01"})
    assert flag is True
    assert "bearish" in note
    assert "5,000" in note


def test_neutral_score_never_flags():
    assert compute_divergence(5.0, {"review_count": 5000}) == (False, None)


def test_bullish_with_thin_sample_flags():
    flag, note = compute_divergence(8.0, {"review_count": 200, "date": "2026-07-01"})
    assert flag is True
    assert "thin-sample" in note


def test_bullish_with_large_sample_does_not_flag():
    assert compute_divergence(8.0, {"review_count": 5000}) == (False, None)


def test_missing_date_field_omits_date_suffix_without_crashing():
    flag, note = compute_divergence(2.0, {"review_count": 1000})
    assert flag is True
    assert "(" not in note.split("metric")[1].split(".")[0]
