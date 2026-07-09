"""
Two-stage game-relevance matching for the news worker
(see docs/news-source-decision-memo.md §2).

Stage 1 -- cheap deterministic word-boundary match on title+snippet against
each watchlist entity's canonical title (strong), aliases (strong), and
studio name (weak). Runs on every candidate article, no API cost.

Stage 2 -- Claude Haiku disambiguation, only for ambiguous candidates: a
studio-only match, or an entity flagged games.title_is_ambiguous (common-word
titles like "Control"/"Destiny"/"Rust" -- see migration 008). The verdict is
cached by (article url, entity title) so a given article is judged once ever,
regardless of how many entities it's checked against across runs. Fails
closed (excludes the match) on any error -- a missed article is cheaper than
a corrupted sentiment score.
"""

from __future__ import annotations

import re

import anthropic
from langsmith.wrappers import wrap_anthropic

from agents.token_tracking import record_usage_from_message
from database.api_cache import ApiCache

_MODEL = "claude-haiku-4-5-20251001"
_client = wrap_anthropic(anthropic.Anthropic())

_MIN_TERM_LEN = 3


def _contains_term(blob: str, term: str) -> bool:
    term = (term or "").strip()
    if len(term) < _MIN_TERM_LEN:
        return False
    pattern = r"\b" + re.escape(term.lower()) + r"\b"
    return re.search(pattern, blob) is not None


def match_candidates(article: dict, entities: list[dict]) -> list[dict]:
    """Stage 1. Returns [{"game_id", "match_strength": "title"|"alias"|"studio_only"}]."""
    blob = f"{article.get('title', '')} {article.get('snippet', '')}".lower()
    candidates: list[dict] = []
    for entity in entities:
        strength = None
        if _contains_term(blob, entity.get("title", "")):
            strength = "title"
        else:
            for alias in entity.get("aliases") or []:
                if _contains_term(blob, alias):
                    strength = "alias"
                    break
        if strength is None and entity.get("studio_name") and _contains_term(blob, entity["studio_name"]):
            strength = "studio_only"
        if strength:
            candidates.append({"game_id": entity["game_id"], "match_strength": strength})
    return candidates


def needs_disambiguation(entity: dict, match: dict) -> bool:
    return match["match_strength"] == "studio_only" or bool(entity.get("title_is_ambiguous"))


def disambiguate(article: dict, entity_title: str, cache: ApiCache) -> bool:
    """Stage 2. One Haiku call, cached by (url, entity_title). Fails closed."""
    key = f"verdict:{article.get('url', '')}:{entity_title}"
    cached = cache.get(key)
    if cached is not None:
        return bool(cached.get("verdict"))

    prompt = (
        f'Article title: "{article.get("title", "")}"\n'
        f'Snippet: "{article.get("snippet", "")}"\n\n'
        f'Is this article primarily about "{entity_title}"? '
        "Answer with exactly one word: yes or no."
    )
    try:
        msg = _client.messages.create(
            model=_MODEL,
            max_tokens=8,
            messages=[{"role": "user", "content": prompt}],
        )
        record_usage_from_message(_MODEL, msg)
        raw = msg.content[0].text.strip().lower()
        verdict = raw.startswith("yes")
        cache.set(key, {"verdict": verdict})
        return verdict
    except Exception:
        return False


def resolve_matched_entities(article: dict, entities: list[dict], cache: ApiCache) -> list[str]:
    """Orchestrates Stage 1 + Stage 2. Returns confidently-matched game_ids."""
    candidates = match_candidates(article, entities)
    entities_by_id = {e["game_id"]: e for e in entities}
    resolved: list[str] = []
    for match in candidates:
        entity = entities_by_id[match["game_id"]]
        if needs_disambiguation(entity, match):
            if disambiguate(article, entity["title"], cache):
                resolved.append(match["game_id"])
        else:
            resolved.append(match["game_id"])
    return resolved
