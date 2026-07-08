"""
Unit tests for agents/workers/discovery/scoring.py.

Pure functions, no DB/API calls -- covers each rubric component's bucket
boundaries and the three proposal_tier bands.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.workers.discovery.scoring import proposal_tier, score_candidate


def test_all_minimum_inputs_land_below_watch_threshold():
    score, components = score_candidate()
    # public_parent=25, materiality=0, live_service=8, signal_coverage=0,
    # timeliness=3 (no release date, no current activity), uniqueness=5
    assert components == {
        "public_parent_confidence": 25,
        "materiality": 0,
        "live_service": 8,
        "signal_coverage": 0,
        "timeliness": 3,
        "uniqueness": 5,
    }
    assert score == 41
    assert proposal_tier(score) is None


def test_high_ccu_live_service_with_corroboration_reaches_high_confidence():
    score, components = score_candidate(
        ccu=60_000,
        is_live_service=True,
        signal_count=2,
        studio_already_tracked=False,
    )
    assert components["materiality"] == 25
    assert components["live_service"] == 20
    assert components["signal_coverage"] == 10
    assert components["timeliness"] == 6  # already-live (ccu present), not within 60-day window
    assert components["uniqueness"] == 5
    assert score == 91
    assert proposal_tier(score) == "high_confidence"


def test_materiality_bucket_boundaries_for_ccu():
    assert score_candidate(ccu=50_000)[1]["materiality"] == 25
    assert score_candidate(ccu=49_999)[1]["materiality"] == 18
    assert score_candidate(ccu=10_000)[1]["materiality"] == 18
    assert score_candidate(ccu=9_999)[1]["materiality"] == 10
    assert score_candidate(ccu=1_000)[1]["materiality"] == 10
    assert score_candidate(ccu=999)[1]["materiality"] == 5
    assert score_candidate(ccu=1)[1]["materiality"] == 5
    assert score_candidate(ccu=0)[1]["materiality"] == 0


def test_materiality_uses_hype_when_no_ccu_present():
    assert score_candidate(hype=1_000)[1]["materiality"] == 25
    assert score_candidate(hype=200)[1]["materiality"] == 18
    assert score_candidate(hype=50)[1]["materiality"] == 10
    assert score_candidate(hype=1)[1]["materiality"] == 5
    assert score_candidate(hype=None)[1]["materiality"] == 0


def test_materiality_takes_the_better_of_ccu_and_hype():
    score, components = score_candidate(ccu=500, hype=5_000)
    assert components["materiality"] == 25  # hype bucket wins


def test_timeliness_prefers_upcoming_60_day_window_over_current_activity():
    score, components = score_candidate(ccu=10_000, days_until_release=30)
    assert components["timeliness"] == 10


def test_timeliness_falls_back_to_current_activity_outside_60_day_window():
    score, components = score_candidate(ccu=10_000, days_until_release=90)
    assert components["timeliness"] == 6


def test_timeliness_lowest_with_no_release_date_and_no_activity():
    score, components = score_candidate(days_until_release=None)
    assert components["timeliness"] == 3


def test_signal_coverage_caps_at_three_signals():
    assert score_candidate(signal_count=1)[1]["signal_coverage"] == 5
    assert score_candidate(signal_count=3)[1]["signal_coverage"] == 15
    assert score_candidate(signal_count=10)[1]["signal_coverage"] == 15


def test_uniqueness_penalizes_already_tracked_studio():
    assert score_candidate(studio_already_tracked=True)[1]["uniqueness"] == 2
    assert score_candidate(studio_already_tracked=False)[1]["uniqueness"] == 5


def test_proposal_tier_bands():
    assert proposal_tier(70) == "high_confidence"
    assert proposal_tier(100) == "high_confidence"
    assert proposal_tier(69) == "watch"
    assert proposal_tier(50) == "watch"
    assert proposal_tier(49) is None
    assert proposal_tier(0) is None
