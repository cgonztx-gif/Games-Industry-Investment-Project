"""
Unit tests for agents/workers/sentiment/news_stance_client.py.

No live network calls: _client.messages.create is monkeypatched, following
the pattern in tests/test_entity_matcher.py.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agents.workers.sentiment.news_stance_client as stance_mod
from agents.workers.sentiment.news_stance_client import (
    _HAIKU_MODEL,
    _SONNET_MODEL,
    classify_stance,
)


def _fake_msg(text: str):
    return type("Msg", (), {"content": [type("C", (), {"text": text})()]})()


def _article(title="Patch review", snippet="Great update", domain="ign.com"):
    return {"title": title, "snippet": snippet, "domain": domain}


def _stance_json(**overrides):
    base = {
        "score": 7.5,
        "themes": [{"frame": "product_quality", "stance": "positive"}],
        "outlet_count": 3,
        "coverage_note": "Broad pickup across multiple outlets.",
    }
    base.update(overrides)
    return json.dumps(base)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_returns_parsed_stance(monkeypatch):
    monkeypatch.setattr(
        stance_mod._client.messages, "create", lambda **kw: _fake_msg(_stance_json())
    )

    result = classify_stance("Elden Ring", [_article()])

    assert result is not None
    assert result["score"] == 7.5
    assert result["themes"] == [{"frame": "product_quality", "stance": "positive"}]
    assert result["outlet_count"] == 3
    assert result["coverage_note"] == "Broad pickup across multiple outlets."


# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------

def test_haiku_for_listing_only(monkeypatch):
    calls = []

    def _fake(**kw):
        calls.append(kw)
        return _fake_msg(_stance_json())

    monkeypatch.setattr(stance_mod._client.messages, "create", _fake)

    classify_stance("Game", [_article()], sentiment_tier="listing_only")

    assert calls[0]["model"] == _HAIKU_MODEL


def test_sonnet_for_tier_a(monkeypatch):
    calls = []

    def _fake(**kw):
        calls.append(kw)
        return _fake_msg(_stance_json())

    monkeypatch.setattr(stance_mod._client.messages, "create", _fake)

    classify_stance("Game", [_article()], sentiment_tier="tier_a")

    assert calls[0]["model"] == _SONNET_MODEL


# ---------------------------------------------------------------------------
# Markdown-fenced JSON
# ---------------------------------------------------------------------------

def test_markdown_fenced_json_parsed(monkeypatch):
    fenced = f"```json\n{_stance_json()}\n```"
    monkeypatch.setattr(
        stance_mod._client.messages, "create", lambda **kw: _fake_msg(fenced)
    )

    result = classify_stance("Game", [_article()])

    assert result is not None
    assert result["score"] == 7.5


# ---------------------------------------------------------------------------
# Articles cap at 50
# ---------------------------------------------------------------------------

def test_articles_capped_at_50(monkeypatch):
    calls = []

    def _fake(**kw):
        calls.append(kw)
        return _fake_msg(_stance_json())

    monkeypatch.setattr(stance_mod._client.messages, "create", _fake)

    articles = [_article(title=f"Article {i}", domain=f"site{i}.com") for i in range(60)]
    classify_stance("Game", articles)

    prompt = calls[0]["messages"][0]["content"]
    # Only 50 articles → "50." appears but "51." does not
    assert "50." in prompt
    assert "51." not in prompt


# ---------------------------------------------------------------------------
# outlet_count computed from domains when absent in response
# ---------------------------------------------------------------------------

def test_outlet_count_computed_from_domains_when_absent(monkeypatch):
    response_without_count = json.dumps({
        "score": 6.0,
        "themes": [],
        "coverage_note": "narrow",
    })
    monkeypatch.setattr(
        stance_mod._client.messages, "create", lambda **kw: _fake_msg(response_without_count)
    )

    articles = [
        _article(domain="ign.com"),
        _article(domain="eurogamer.net"),
        _article(domain="ign.com"),  # duplicate domain
    ]
    result = classify_stance("Game", articles)

    assert result is not None
    assert result["outlet_count"] == 2  # distinct domains: ign.com, eurogamer.net


# ---------------------------------------------------------------------------
# Empty articles → None (no call)
# ---------------------------------------------------------------------------

def test_empty_articles_returns_none(monkeypatch):
    def _boom(**kw):
        raise AssertionError("must not call model with no articles")

    monkeypatch.setattr(stance_mod._client.messages, "create", _boom)

    result = classify_stance("Game", [])

    assert result is None


# ---------------------------------------------------------------------------
# Exception → None, no propagation
# ---------------------------------------------------------------------------

def test_exception_returns_none(monkeypatch):
    def _boom(**kw):
        raise RuntimeError("api down")

    monkeypatch.setattr(stance_mod._client.messages, "create", _boom)

    result = classify_stance("Game", [_article()])

    assert result is None


def test_json_parse_error_returns_none(monkeypatch):
    monkeypatch.setattr(
        stance_mod._client.messages, "create", lambda **kw: _fake_msg("not json at all")
    )

    result = classify_stance("Game", [_article()])

    assert result is None
