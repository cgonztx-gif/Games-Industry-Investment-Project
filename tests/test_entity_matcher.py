"""
Unit tests for agents/workers/news/entity_matcher.py.

Covers the false-positive/false-negative classes named in
docs/news-source-decision-memo.md §2: common-word titles and studio-only
matches must force Stage-2 disambiguation; unambiguous title/alias matches
must NOT trigger a Stage-2 call (asserted via a call-raising fake, matching
tests/test_reddit_source.py's house convention); Stage-2 failures must fail
closed (exclude the match) rather than corrupt a score.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.workers.news.entity_matcher import (
    disambiguate,
    match_candidates,
    needs_disambiguation,
    resolve_matched_entities,
)
from database.api_cache import InMemoryApiCache


def _entity(game_id="g1", title="Elden Ring", aliases=None, studio_name=None, ambiguous=False):
    return {
        "game_id": game_id,
        "title": title,
        "aliases": aliases or [],
        "studio_name": studio_name,
        "title_is_ambiguous": ambiguous,
    }


def _article(title="", snippet="", url="https://example.com/a"):
    return {"title": title, "snippet": snippet, "url": url}


# ---------------------------------------------------------------------------
# Stage 1: match_candidates
# ---------------------------------------------------------------------------

def test_match_candidates_matches_on_canonical_title():
    article = _article(title="Elden Ring DLC announced", snippet="")
    entities = [_entity()]

    candidates = match_candidates(article, entities)

    assert candidates == [{"game_id": "g1", "match_strength": "title"}]


def test_match_candidates_matches_on_alias():
    article = _article(title="", snippet="New patch for BG3 fixes multiplayer bug")
    entities = [_entity(title="Baldur's Gate 3", aliases=["BG3"])]

    candidates = match_candidates(article, entities)

    assert candidates == [{"game_id": "g1", "match_strength": "alias"}]


def test_match_candidates_studio_only_is_weak_signal():
    article = _article(title="Big Studio Inc reports layoffs", snippet="")
    entities = [_entity(title="Some Unrelated Game", studio_name="Big Studio Inc")]

    candidates = match_candidates(article, entities)

    assert candidates == [{"game_id": "g1", "match_strength": "studio_only"}]


def test_match_candidates_no_match_returns_empty():
    article = _article(title="Completely unrelated headline", snippet="nothing here")
    entities = [_entity()]

    assert match_candidates(article, entities) == []


def test_match_candidates_requires_word_boundary():
    # "Rust" must not match inside "Trustworthy" or similar substrings.
    article = _article(title="A trustworthy review site", snippet="")
    entities = [_entity(title="Rust")]

    assert match_candidates(article, entities) == []


# ---------------------------------------------------------------------------
# needs_disambiguation -- the "only for ambiguous candidates" rule
# ---------------------------------------------------------------------------

def test_needs_disambiguation_true_for_studio_only_match():
    entity = _entity(ambiguous=False)
    match = {"game_id": "g1", "match_strength": "studio_only"}

    assert needs_disambiguation(entity, match) is True


def test_needs_disambiguation_true_for_ambiguous_title_even_on_title_match():
    # Common-word titles (Control, Destiny, Rust, ...) force Stage 2 even
    # when Stage 1 matched on the canonical title itself.
    entity = _entity(title="Control", ambiguous=True)
    match = {"game_id": "g1", "match_strength": "title"}

    assert needs_disambiguation(entity, match) is True


def test_needs_disambiguation_false_for_unambiguous_title_match():
    entity = _entity(title="Elden Ring", ambiguous=False)
    match = {"game_id": "g1", "match_strength": "title"}

    assert needs_disambiguation(entity, match) is False


def test_needs_disambiguation_false_for_unambiguous_alias_match():
    entity = _entity(title="Baldur's Gate 3", aliases=["BG3"], ambiguous=False)
    match = {"game_id": "g1", "match_strength": "alias"}

    assert needs_disambiguation(entity, match) is False


# ---------------------------------------------------------------------------
# disambiguate -- cached verdicts, fails closed
# ---------------------------------------------------------------------------

def test_disambiguate_returns_cached_verdict_without_calling_the_model(monkeypatch):
    import agents.workers.news.entity_matcher as entity_matcher

    def _boom(*args, **kwargs):
        raise AssertionError("must not call the model on a cache hit")

    monkeypatch.setattr(entity_matcher._client.messages, "create", _boom)
    cache = InMemoryApiCache()
    cache.set("verdict:https://example.com/a:Control", {"verdict": True})

    result = disambiguate(_article(url="https://example.com/a"), "Control", cache)

    assert result is True


def test_disambiguate_fails_closed_on_model_error(monkeypatch):
    import agents.workers.news.entity_matcher as entity_matcher

    def _boom(*args, **kwargs):
        raise RuntimeError("api down")

    monkeypatch.setattr(entity_matcher._client.messages, "create", _boom)
    cache = InMemoryApiCache()

    result = disambiguate(_article(url="https://example.com/a"), "Control", cache)

    assert result is False
    # A transient failure must not be cached as a confirmed "no" -- the next
    # call for this (url, entity) pair should retry, not stay stuck.
    assert cache.get("verdict:https://example.com/a:Control") is None


# ---------------------------------------------------------------------------
# resolve_matched_entities -- end-to-end orchestration
# ---------------------------------------------------------------------------

def test_resolve_matched_entities_skips_stage_two_for_unambiguous_match(monkeypatch):
    import agents.workers.news.entity_matcher as entity_matcher

    def _boom(*args, **kwargs):
        raise AssertionError("Stage 2 must not run for an unambiguous title match")

    monkeypatch.setattr(entity_matcher._client.messages, "create", _boom)
    article = _article(title="Elden Ring DLC announced")
    entities = [_entity()]

    resolved = resolve_matched_entities(article, entities, InMemoryApiCache())

    assert resolved == ["g1"]


def test_resolve_matched_entities_excludes_ambiguous_match_on_no_verdict(monkeypatch):
    import agents.workers.news.entity_matcher as entity_matcher

    class _FakeMsg:
        content = [type("C", (), {"text": "no"})()]

    monkeypatch.setattr(
        entity_matcher._client.messages, "create", lambda **kwargs: _FakeMsg()
    )
    article = _article(title="Control freaks: a review of home automation gadgets")
    entities = [_entity(title="Control", ambiguous=True)]

    resolved = resolve_matched_entities(article, entities, InMemoryApiCache())

    assert resolved == []


def test_resolve_matched_entities_includes_ambiguous_match_on_yes_verdict(monkeypatch):
    import agents.workers.news.entity_matcher as entity_matcher

    class _FakeMsg:
        content = [type("C", (), {"text": "yes"})()]

    monkeypatch.setattr(
        entity_matcher._client.messages, "create", lambda **kwargs: _FakeMsg()
    )
    article = _article(title="Control gets a surprise new DLC from Remedy")
    entities = [_entity(title="Control", ambiguous=True)]

    resolved = resolve_matched_entities(article, entities, InMemoryApiCache())

    assert resolved == ["g1"]
