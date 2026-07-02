def compute_divergence(
    sentiment_score: float,
    player_metrics: dict | None,
) -> tuple[bool, str | None]:
    """
    Emit a preliminary lagged flag against the latest stored player metrics.

    The authoritative same-week text-vs-quant divergence check belongs to the
    synthesis agent, which sees all worker outputs from the same run together.
    """
    if player_metrics is None:
        return False, None

    review_count = player_metrics.get("review_count") or 0
    metrics_date = player_metrics.get("date")
    if review_count < 100:
        return False, None

    if sentiment_score <= 3.5:
        text_signal = "bearish"
    elif sentiment_score >= 6.5:
        text_signal = "bullish"
    else:
        return False, None

    if text_signal == "bearish":
        return True, (
            f"Preliminary lagged flag: sentiment score {sentiment_score}/10 is bearish "
            f"despite {review_count:,} reviews in the latest stored player metric"
            f"{f' ({metrics_date})' if metrics_date else ''}. Synthesis must verify "
            "against same-week CCU, review velocity, and patch cadence before acting."
        )

    if text_signal == "bullish" and review_count < 500:
        return True, (
            f"Preliminary lagged flag: sentiment score {sentiment_score}/10 is bullish "
            f"but only {review_count:,} reviews exist in the latest stored player metric"
            f"{f' ({metrics_date})' if metrics_date else ''}. Treat as a thin-sample hint "
            "until synthesis checks same-week quantitative data."
        )

    return False, None
