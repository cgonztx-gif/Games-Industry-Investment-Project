"""
Unit tests for agents/synthesis/deep_dive.py.

No live network and no real Anthropic client construction beyond what the
module import performs: run_deep_dive() takes an injectable `client`, so all
tests use a fake client with a scripted `.messages.create(...)` response.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.synthesis.deep_dive import run_deep_dive


class FakeClient:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.messages = SimpleNamespace(create=self._create)
        self.last_kwargs = None

    def _create(self, **kwargs):
        self.last_kwargs = kwargs
        if self._exc:
            raise self._exc
        return self._response


def _text_response(text: str, stop_reason: str = "end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=text)],
    )


def test_happy_path_valid_json():
    payload = (
        '{"summary": "The drop is tied to a balance patch, not a controversy.", '
        '"sources": ["https://example.com/patch"], "confidence": "high"}'
    )
    client = FakeClient(response=_text_response(payload))

    result = run_deep_dive("Example Game", "Why did sentiment drop?", client=client)

    assert result == {
        "summary": "The drop is tied to a balance patch, not a controversy.",
        "sources": ["https://example.com/patch"],
        "confidence": "high",
    }


def test_markdown_fenced_json_is_stripped():
    payload = (
        '```json\n{"summary": "Findings here.", "sources": [], "confidence": "low"}\n```'
    )
    client = FakeClient(response=_text_response(payload))

    result = run_deep_dive("Example Game", "question", client=client)

    assert result is not None
    assert result["summary"] == "Findings here."
    assert result["sources"] == []


def test_refusal_stop_reason_returns_none():
    client = FakeClient(response=_text_response("", stop_reason="refusal"))

    result = run_deep_dive("Example Game", "question", client=client)

    assert result is None


def test_malformed_json_returns_none():
    client = FakeClient(response=_text_response("not valid json"))

    result = run_deep_dive("Example Game", "question", client=client)

    assert result is None


def test_missing_required_keys_returns_none():
    client = FakeClient(response=_text_response('{"summary": "only summary"}'))

    result = run_deep_dive("Example Game", "question", client=client)

    assert result is None


def test_no_text_block_returns_none():
    response = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="tool_use", input={})],
    )
    client = FakeClient(response=response)

    result = run_deep_dive("Example Game", "question", client=client)

    assert result is None


def test_client_exception_returns_none():
    client = FakeClient(exc=RuntimeError("network error"))

    result = run_deep_dive("Example Game", "question", client=client)

    assert result is None


def test_invalid_confidence_defaults_to_low():
    payload = '{"summary": "Findings.", "sources": [], "confidence": "extremely-high"}'
    client = FakeClient(response=_text_response(payload))

    result = run_deep_dive("Example Game", "question", client=client)

    assert result is not None
    assert result["confidence"] == "low"


def test_sources_capped_at_eight():
    sources = [f"https://example.com/{i}" for i in range(12)]
    payload = (
        '{"summary": "Findings.", "sources": '
        + str(sources).replace("'", '"')
        + ', "confidence": "medium"}'
    )
    client = FakeClient(response=_text_response(payload))

    result = run_deep_dive("Example Game", "question", client=client)

    assert result is not None
    assert len(result["sources"]) == 8
