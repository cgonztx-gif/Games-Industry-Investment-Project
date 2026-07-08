"""
Claude Haiku rationale generation for Discovery proposals -- one to three
sentences of investment-relevance justification per candidate, per the
`claude_rationale` field in agents/skills/watchlist-relevance-scoring/SKILL.md's
output contract. Scoring itself is deterministic (see scoring.py); Claude is
only used for this text, never for the score.
"""

import anthropic
from langsmith.wrappers import wrap_anthropic

_MODEL = "claude-haiku-4-5-20251001"
_client = wrap_anthropic(anthropic.Anthropic())

_SYSTEM = (
    "You are a game industry investment analyst writing a one-to-three "
    "sentence rationale for why a discovery candidate is or isn't worth "
    "tracking. Be specific about the evidence given, not generic. Return "
    "ONLY the rationale text -- no preamble, no markdown, no JSON."
)

_FALLBACK = (
    "Rationale unavailable (Claude call failed) -- see trigger_signal and "
    "score for the underlying evidence."
)

_GAME_TEMPLATE = """\
Candidate: "{title}" (studio: {studio_name}, ticker: {ticker})
Trigger: {trigger_signal}
Score: {score}/100 ({tier})
Score breakdown: {components}
Live-service: {is_live_service}

Write a 1-3 sentence investment-relevance rationale for adding this to the watchlist.
"""

_COMPANY_TEMPLATE = """\
Candidate: {company_name} (ticker: {ticker})
Trigger: {trigger_signal} (SEC form {form}, filed {file_date})
This is a company-level signal only -- no specific game has been identified \
yet; a human reviewer still needs to determine which title(s), if any, to add.

Write a 1-2 sentence rationale for why this new public filing is worth a human's attention.
"""


def _call(prompt: str) -> str:
    try:
        msg = _client.messages.create(
            model=_MODEL,
            max_tokens=256,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        return text or _FALLBACK
    except Exception:
        return _FALLBACK


def generate_game_rationale(candidate: dict, score: int, tier: str, components: dict) -> str:
    """Rationale for a game-level (Steam/IGDB-sourced) candidate."""
    prompt = _GAME_TEMPLATE.format(
        title=candidate.get("title", "Unknown"),
        studio_name=candidate.get("studio_name") or "unknown",
        ticker=candidate.get("ticker") or "unknown",
        trigger_signal=candidate.get("trigger_signal", "unknown"),
        score=score,
        tier=tier,
        components=", ".join(f"{k}={v}" for k, v in components.items()),
        is_live_service=candidate.get("is_live_service", False),
    )
    return _call(prompt)


def generate_company_rationale(candidate: dict) -> str:
    """Rationale for a company-level (EDGAR-sourced, no game_id yet) candidate."""
    prompt = _COMPANY_TEMPLATE.format(
        company_name=candidate.get("company_name", "Unknown"),
        ticker=candidate.get("ticker", "unknown"),
        trigger_signal=candidate.get("trigger_signal", "unknown"),
        form=candidate.get("form", "unknown"),
        file_date=candidate.get("file_date", "unknown"),
    )
    return _call(prompt)
