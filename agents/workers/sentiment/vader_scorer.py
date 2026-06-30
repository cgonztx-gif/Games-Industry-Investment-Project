import time
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

_analyzer = SentimentIntensityAnalyzer()


def score_texts(texts_with_weights: list[dict]) -> float:
    """
    Compute a weighted-average VADER sentiment score scaled to 1–10.

    texts_with_weights: list of {"text": str, "score": int}
    Engagement weight = max(1, score) — suppresses low-upvote outliers (vocal-minority guard).
    Returns 5.5 (neutral) if the list is empty.
    """
    if not texts_with_weights:
        return 5.5

    total_weight = 0.0
    weighted_sum = 0.0

    for item in texts_with_weights:
        compound = _analyzer.polarity_scores(item["text"])["compound"]
        weight = max(1, item.get("score", 1))
        weighted_sum += compound * weight
        total_weight += weight

    weighted_avg = weighted_sum / total_weight
    # Scale [-1, 1] → [1.0, 10.0]
    scaled = (weighted_avg + 1) / 2 * 9 + 1
    return round(scaled, 1)
