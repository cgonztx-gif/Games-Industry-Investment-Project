"""
Unit tests for agents/workers/discovery/claude_rationale.py.

No live network calls: _client.messages.create is monkeypatched with a fake
that returns scripted responses or raises, following the pattern in
tests/test_entity_matcher.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agents.workers.discovery.claude_rationale as claude_rationale
from agents.workers.discovery.claude_rationale import (
    _FALLBACK,
    _MODEL,
    generate_company_rationale,
    generate_game_rationale,
)


def _fake_msg(text: str):
    return type("Msg", (), {"content": [type("C", (), {"text": text})()]})()


def _game_candidate(**overrides):
    base = {
        "title": "Big Hit",
        "studio_name": "Known Studio",
        "ticker": "KNOW",
        "trigger_signal": "steam_top_ccu",
        "is_live_service": True,
    }
    base.update(overrides)
    return base


def _company_candidate(**overrides):
    base = {
        "company_name": "New Gaming Co",
        "ticker": "NEWG",
        "trigger_signal": "edgar_new_ipo_filing",
        "form": "S-1",
        "file_date": "2026-07-01",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# generate_game_rationale
# ---------------------------------------------------------------------------

def test_game_rationale_returns_model_text(monkeypatch):
    calls = []

    def _fake_create(**kwargs):
        calls.append(kwargs)
        return _fake_msg("Strong player engagement justifies tracking.")

    monkeypatch.setattr(claude_rationale._client.messages, "create", _fake_create)

    result = generate_game_rationale(_game_candidate(), score=72, tier="high_confidence", components={"materiality": 25})

    assert result == "Strong player engagement justifies tracking."
    assert len(calls) == 1


def test_game_rationale_uses_haiku_model(monkeypatch):
    calls = []

    def _fake_create(**kwargs):
        calls.append(kwargs)
        return _fake_msg("rationale text")

    monkeypatch.setattr(claude_rationale._client.messages, "create", _fake_create)

    generate_game_rationale(_game_candidate(), score=55, tier="watch", components={})

    assert calls[0]["model"] == _MODEL


def test_game_rationale_fallback_on_exception(monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("api down")

    monkeypatch.setattr(claude_rationale._client.messages, "create", _boom)

    result = generate_game_rationale(_game_candidate(), score=55, tier="watch", components={})

    assert result == _FALLBACK


def test_game_rationale_fallback_on_empty_response(monkeypatch):
    monkeypatch.setattr(
        claude_rationale._client.messages, "create", lambda **kw: _fake_msg("   ")
    )

    result = generate_game_rationale(_game_candidate(), score=55, tier="watch", components={})

    assert result == _FALLBACK


# ---------------------------------------------------------------------------
# generate_company_rationale
# ---------------------------------------------------------------------------

def test_company_rationale_returns_model_text(monkeypatch):
    monkeypatch.setattr(
        claude_rationale._client.messages,
        "create",
        lambda **kw: _fake_msg("New IPO in gaming space worth reviewing."),
    )

    result = generate_company_rationale(_company_candidate())

    assert result == "New IPO in gaming space worth reviewing."


def test_company_rationale_fallback_on_exception(monkeypatch):
    def _boom(**kwargs):
        raise ConnectionError("timeout")

    monkeypatch.setattr(claude_rationale._client.messages, "create", _boom)

    result = generate_company_rationale(_company_candidate())

    assert result == _FALLBACK
