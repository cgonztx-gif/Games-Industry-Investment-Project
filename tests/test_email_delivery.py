"""
Unit tests for agents/synthesis/email_delivery.py.

No live Resend calls: requests.post is mocked in every test that reaches the
network path. Mirrors tests/test_tracing.py's "no key means no-op" shape and
tests/test_deep_dive.py's fake-response style.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.synthesis import email_delivery


def _sample_briefing() -> dict:
    return {
        "week_of": "2026-06-29",
        "briefing_text": "Weekly briefing for 2026-06-29: confidence=medium; 2 divergence signal(s).",
        "portfolio_update": {"confidence": "medium"},
        "top_opportunities": [{"game_id": "game-1", "type": "bearish_text_stable_quant"}],
        "risk_flags": [{"game_id": "game-2", "severity": "high", "signal": "test"}],
        "notable_events": {"patch_events": 3, "studio_signals": 1},
        "reasoning_log": "test reasoning",
    }


def _fake_response(status_code: int, text: str = "") -> SimpleNamespace:
    return SimpleNamespace(status_code=status_code, text=text)


def test_noop_when_resend_api_key_unset(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.setenv("BRIEFING_EMAIL_TO", "someone@example.com")

    calls = []
    monkeypatch.setattr(
        email_delivery.requests, "post", lambda *a, **k: calls.append((a, k)) or _fake_response(200)
    )

    result = email_delivery.send_briefing_email(_sample_briefing())

    assert result is False
    assert calls == []


def test_noop_when_briefing_email_to_unset(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    monkeypatch.delenv("BRIEFING_EMAIL_TO", raising=False)

    calls = []
    monkeypatch.setattr(
        email_delivery.requests, "post", lambda *a, **k: calls.append((a, k)) or _fake_response(200)
    )

    result = email_delivery.send_briefing_email(_sample_briefing())

    assert result is False
    assert calls == []


def test_noop_when_both_unset(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("BRIEFING_EMAIL_TO", raising=False)

    calls = []
    monkeypatch.setattr(
        email_delivery.requests, "post", lambda *a, **k: calls.append((a, k)) or _fake_response(200)
    )

    result = email_delivery.send_briefing_email(_sample_briefing())

    assert result is False
    assert calls == []


def test_correct_payload_construction(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    monkeypatch.setenv("BRIEFING_EMAIL_TO", "someone@example.com")
    monkeypatch.delenv("BRIEFING_EMAIL_FROM", raising=False)

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _fake_response(200)

    monkeypatch.setattr(email_delivery.requests, "post", fake_post)

    result = email_delivery.send_briefing_email(_sample_briefing())

    assert result is True
    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["headers"]["Content-Type"] == "application/json"
    body = captured["json"]
    assert body["to"] == ["someone@example.com"]
    assert body["from"] == email_delivery._DEFAULT_FROM
    assert "2026-06-29" in body["subject"]
    assert "<h2>" in body["html"]
    assert "bearish_text_stable_quant" in body["html"]


def test_custom_from_address_used(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    monkeypatch.setenv("BRIEFING_EMAIL_TO", "someone@example.com")
    monkeypatch.setenv("BRIEFING_EMAIL_FROM", "Custom Sender <custom@example.com>")

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return _fake_response(200)

    monkeypatch.setattr(email_delivery.requests, "post", fake_post)

    email_delivery.send_briefing_email(_sample_briefing())

    assert captured["json"]["from"] == "Custom Sender <custom@example.com>"


def test_multiple_comma_separated_recipients_split_into_list(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    monkeypatch.setenv(
        "BRIEFING_EMAIL_TO", "first@example.com, second@example.com,third@example.com"
    )

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return _fake_response(200)

    monkeypatch.setattr(email_delivery.requests, "post", fake_post)

    email_delivery.send_briefing_email(_sample_briefing())

    assert captured["json"]["to"] == [
        "first@example.com",
        "second@example.com",
        "third@example.com",
    ]


def test_non_2xx_response_returns_false(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    monkeypatch.setenv("BRIEFING_EMAIL_TO", "someone@example.com")

    monkeypatch.setattr(
        email_delivery.requests,
        "post",
        lambda *a, **k: _fake_response(422, text="Unprocessable"),
    )

    result = email_delivery.send_briefing_email(_sample_briefing())

    assert result is False


def test_request_exception_returns_false(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    monkeypatch.setenv("BRIEFING_EMAIL_TO", "someone@example.com")

    def raise_exc(*args, **kwargs):
        raise RuntimeError("network error")

    monkeypatch.setattr(email_delivery.requests, "post", raise_exc)

    result = email_delivery.send_briefing_email(_sample_briefing())

    assert result is False
