"""
Media stance/frame classifier for the news source (source='news' in
sentiment_snapshots).

See docs/news-source-decision-memo.md §3: news coverage measures a different
axis than community sentiment -- media tone/stance and narrative frame, not
player-experience gripes -- so this is deliberately separate from
absa_client.py rather than reusing its player-complaint aspect vocabulary.
VADER and the vocal-minority guard are skipped entirely for this source (see
agents/workers/sentiment/worker.py); this one aggregate call *is* the score.
"""

import json
import re

import anthropic
from langsmith.wrappers import wrap_anthropic

_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_SONNET_MODEL = "claude-sonnet-4-6"
_client = wrap_anthropic(anthropic.Anthropic())

_SYSTEM = (
    "You are a financial/games-industry analyst summarizing how the press is "
    "covering a game or studio this week. Return ONLY valid JSON — no prose, "
    "no markdown fences."
)

_USER_TEMPLATE = """\
Analyze this week's press coverage for "{game_title}" and summarize the
media's tone and narrative framing.

Articles (most recent first):
{articles_block}

Return a JSON object with this exact structure:
{{"score": number 1.0-10.0, "themes": [{{"frame": "string", "stance": "positive"|"neutral"|"negative"}}], "outlet_count": integer, "coverage_note": "string"}}

Rules:
- score reflects overall media tone toward the company/game this week: 1.0 = very negative coverage, 5.5 = neutral/mixed, 10.0 = very positive coverage
- frame must be lowercase_snake_case, one of: financial_performance, product_quality, controversy, roadmap_future, competitive_position, monetization
- List up to 3 frames, ordered by how much of the coverage they represent
- outlet_count is the number of distinct outlets/domains represented in the articles above
- coverage_note is one short sentence: is this narrow (one outlet repeating) or broad (many outlets) pickup?
"""


def _model_for_tier(sentiment_tier: str) -> str:
    return _SONNET_MODEL if sentiment_tier == "tier_a" else _HAIKU_MODEL


def classify_stance(
    game_title: str,
    articles: list[dict],
    sentiment_tier: str = "listing_only",
) -> dict | None:
    """
    Run one aggregate Claude call over a game's week of matched news articles.

    articles: list of {"title", "snippet", "domain", ...} dicts (news_items rows).
    Returns {"score", "themes", "outlet_count", "coverage_note"}, or None on
    any failure. Unlike ABSA, this call *is* the score, not an enrichment
    layered on VADER, so a failure means skip writing the news row for that
    game this week entirely rather than a partial write.
    """
    if not articles:
        return None

    capped = articles[:50]
    articles_block = "\n".join(
        f"{i + 1}. [{a.get('domain') or 'unknown'}] {a.get('title', '')} — {(a.get('snippet') or '')[:300]}"
        for i, a in enumerate(capped)
    )
    prompt = _USER_TEMPLATE.format(game_title=game_title, articles_block=articles_block)

    try:
        msg = _client.messages.create(
            model=_model_for_tier(sentiment_tier),
            max_tokens=512,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
            raw = re.sub(r"\n?```.*$", "", raw, flags=re.DOTALL)
            raw = raw.strip()
        data = json.loads(raw)
        score = round(float(data["score"]), 1)
        themes = [
            {"frame": t["frame"], "stance": t["stance"]}
            for t in (data.get("themes") or [])[:3]
        ]
        outlet_count = int(
            data.get("outlet_count")
            or len({a.get("domain") for a in capped if a.get("domain")})
        )
        coverage_note = data.get("coverage_note") or ""
        return {
            "score": score,
            "themes": themes,
            "outlet_count": outlet_count,
            "coverage_note": coverage_note,
        }
    except Exception:
        return None
