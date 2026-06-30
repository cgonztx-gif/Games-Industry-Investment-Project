import json
import anthropic

_MODEL = "claude-haiku-4-5-20251001"
_client = anthropic.Anthropic()

_SYSTEM = (
    "You are a game industry analyst extracting aspect-based sentiment from player reviews. "
    "Return ONLY valid JSON — no prose, no markdown fences."
)

_USER_TEMPLATE = """\
Analyze these {source} player reviews for "{game_title}" and identify the top aspects discussed.

Reviews (most engaging first):
{reviews_block}

Return a JSON object with this exact structure:
{{"aspects": [{{"aspect": "string", "polarity": "positive"|"negative"|"mixed", "mention_count": integer}}]}}

Rules:
- Only include aspects mentioned in at least 2 reviews
- aspect must be lowercase_snake_case (e.g. "core_gameplay", "monetization", "server_stability", "content_updates", "matchmaking", "progression_system", "ui_ux", "graphics", "performance")
- List up to 5 aspects, ordered by mention_count descending
- If no aspect appears in 2+ reviews, return {{"aspects": []}}
"""


def run_absa(game_title: str, source: str, texts: list[str]) -> list[dict]:
    """
    Run Claude Haiku ABSA on a batch of review texts.

    texts: up to 50 strings, each already capped to 600 chars.
    Returns list of {"aspect": str, "polarity": str} dicts, top 3 by mention_count.
    Returns [] on any error (VADER score still writes — ABSA is non-fatal).
    """
    if not texts:
        return []

    capped = texts[:50]
    reviews_block = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(capped))
    prompt = _USER_TEMPLATE.format(
        source=source,
        game_title=game_title,
        reviews_block=reviews_block,
    )

    try:
        msg = _client.messages.create(
            model=_MODEL,
            max_tokens=512,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        data = json.loads(raw)
        aspects = data.get("aspects") or []
        # Sort by mention_count desc, take top 3, strip mention_count before returning
        aspects.sort(key=lambda a: a.get("mention_count", 0), reverse=True)
        return [{"aspect": a["aspect"], "polarity": a["polarity"]} for a in aspects[:3]]
    except Exception:
        return []
