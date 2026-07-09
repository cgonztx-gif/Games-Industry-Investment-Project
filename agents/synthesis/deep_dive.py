"""
Deep-dive researcher dispatch - Phase 4 synthesis agent.

One-shot, bounded research subagent with server-side web access, dispatched by
agents/synthesis/agent.py when a divergence signal is ambiguous enough to
warrant it (see agents/skills/investment-synthesis-framework/SKILL.md, section
"Deep-Dive Researcher Triggers"). This is a single client.messages.create(...)
call - not an agentic loop - that returns a short structured findings summary
so the raw web corpus never enters synthesis's own context.
"""

import json
import re

import anthropic

from agents.token_tracking import record_usage_from_message

_MODEL = "claude-sonnet-4-6"
_client = anthropic.Anthropic()

_MAX_TOOL_USES = 4

_TOOLS = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": _MAX_TOOL_USES},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": _MAX_TOOL_USES},
]

_SYSTEM = (
    "You are a games-industry research analyst dispatched to answer ONE bounded "
    "question for an investment synthesis pipeline. Use web_search / web_fetch to "
    "investigate only the specific question asked -- do not broaden the research. "
    "Only cite Tier 1-3 sources: official developer/publisher posts, patch notes, "
    "mainstream or trade press, storefront pages (Steam, etc.), SEC filings. Never "
    "cite or rely on LinkedIn profiles, Discord scrapes, or other Tier 4 (excluded) "
    "sources. Return ONLY valid JSON -- no prose, no markdown fences."
)

_USER_TEMPLATE = """\
Research question about "{game_title}":
{question}

Return a JSON object with this exact structure:
{{"summary": "2-4 sentence findings summary", "sources": ["https://...", ...], "confidence": "high"|"medium"|"low"}}

Rules:
- summary must directly answer the question with what you found, not a research plan
- sources must be real URLs returned by web_search/web_fetch -- never invent one
- confidence reflects how directly the sources answer the question
- if no clear answer is found, still return JSON with your best partial summary and confidence "low"
"""


def run_deep_dive(game_title: str, question: str, client=None) -> dict | None:
    """
    Dispatch a one-off, bounded research subagent with web access.

    Returns {"summary": str, "sources": list[str], "confidence": str} or None on
    any error, refusal, or malformed output -- deep-dive results are optional
    enrichment; callers must proceed without one.

    `client` is injectable for testing (defaults to the module-level Anthropic
    client, same lazy-env-auth pattern as absa_client.py).
    """
    active_client = client or _client
    prompt = _USER_TEMPLATE.format(game_title=game_title, question=question)

    try:
        msg = active_client.messages.create(
            model=_MODEL,
            max_tokens=1024,
            system=_SYSTEM,
            tools=_TOOLS,
            messages=[{"role": "user", "content": prompt}],
        )

        record_usage_from_message(_MODEL, msg)
        if msg.stop_reason == "refusal":
            return None

        text_parts = [
            block.text for block in msg.content if getattr(block, "type", None) == "text"
        ]
        if not text_parts:
            return None

        raw = "".join(text_parts).strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
            raw = re.sub(r"\n?```\s*$", "", raw, flags=re.DOTALL)
            raw = raw.strip()

        data = json.loads(raw)
        summary = data.get("summary")
        sources = data.get("sources")
        if not summary or not isinstance(sources, list):
            return None

        confidence = data.get("confidence")
        if confidence not in ("high", "medium", "low"):
            confidence = "low"

        return {
            "summary": str(summary).strip(),
            "sources": [str(s) for s in sources][:8],
            "confidence": confidence,
        }
    except Exception:
        return None
