"""
Unit tests for the deep-dive dispatch trigger logic in agents/synthesis/agent.py.

No live Supabase and no live Anthropic client: _dispatch_deep_dives() takes an
injectable `deep_dive_fn`, so these tests use a fake in place of
agents.synthesis.deep_dive.run_deep_dive.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.synthesis.agent import _dispatch_deep_dives, _high_severity_game_ids


def _divergence(game_id: str, div_type: str) -> dict:
    return {
        "game_id": game_id,
        "type": div_type,
        "sentiment_score": 2.5,
        "concurrent_players": 100,
        "review_velocity": 5,
        "patch_activity_this_week": False,
        "interpretation": "test",
    }


def _risk(game_id: str, severity: str) -> dict:
    return {"game_id": game_id, "severity": severity, "signal": "test"}


def test_high_severity_game_ids_filters_by_severity_and_presence():
    risks = [
        _risk("game-1", "high"),
        _risk("game-2", "medium"),
        {"studio_id": "studio-1", "severity": "high", "signal": "layoffs"},
    ]

    assert _high_severity_game_ids(risks) == {"game-1"}


def test_dispatches_only_for_bearish_stable_quant_with_high_severity_risk():
    divergences = [
        _divergence("game-1", "bearish_text_stable_quant"),
        _divergence("game-2", "bullish_text_weak_quant"),
    ]
    risks = [_risk("game-1", "high"), _risk("game-2", "high")]
    titles = {"game-1": "Game One", "game-2": "Game Two"}
    calls = []

    def fake_deep_dive(title, question):
        calls.append((title, question))
        return {"summary": "ok", "sources": [], "confidence": "medium"}

    notes = _dispatch_deep_dives(divergences, risks, titles, deep_dive_fn=fake_deep_dive)

    assert len(calls) == 1
    assert calls[0][0] == "Game One"
    assert divergences[0]["deep_dive_triggered"] is True
    assert divergences[0]["deep_dive"]["summary"] == "ok"
    assert "deep_dive" not in divergences[1]
    assert len(notes) == 1


def test_respects_max_dives_cap():
    divergences = [
        _divergence("game-1", "bearish_text_stable_quant"),
        _divergence("game-2", "bearish_text_stable_quant"),
        _divergence("game-3", "bearish_text_stable_quant"),
    ]
    risks = [_risk(gid, "high") for gid in ("game-1", "game-2", "game-3")]
    titles = {"game-1": "One", "game-2": "Two", "game-3": "Three"}
    calls = []

    def fake_deep_dive(title, question):
        calls.append(title)
        return {"summary": "ok", "sources": [], "confidence": "low"}

    notes = _dispatch_deep_dives(
        divergences, risks, titles, deep_dive_fn=fake_deep_dive, max_dives=2
    )

    assert len(calls) == 2
    assert len(notes) == 2
    assert "deep_dive" not in divergences[2]


def test_failed_deep_dive_does_not_raise_and_notes_failure():
    divergences = [_divergence("game-1", "bearish_text_stable_quant")]
    risks = [_risk("game-1", "high")]
    titles = {"game-1": "Game One"}

    def failing_deep_dive(title, question):
        return None

    notes = _dispatch_deep_dives(divergences, risks, titles, deep_dive_fn=failing_deep_dive)

    assert divergences[0]["deep_dive"] is None
    assert divergences[0]["deep_dive_triggered"] is True
    assert "no findings" in notes[0]


def test_missing_title_skips_dispatch_entirely():
    divergences = [_divergence("game-1", "bearish_text_stable_quant")]
    risks = [_risk("game-1", "high")]
    titles: dict[str, str] = {}
    calls = []

    def fake_deep_dive(title, question):
        calls.append(title)
        return {"summary": "ok", "sources": [], "confidence": "low"}

    notes = _dispatch_deep_dives(divergences, risks, titles, deep_dive_fn=fake_deep_dive)

    assert calls == []
    assert "deep_dive" not in divergences[0]
    assert notes == []
