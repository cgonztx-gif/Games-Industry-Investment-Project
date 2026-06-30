def compute_divergence(
    sentiment_score: float,
    player_metrics: dict | None,
) -> tuple[bool, str | None]:
    """
    Flag when text sentiment diverges from the review-count signal.

    player_metrics: output of get_last_player_metrics() with keys {review_count, date}.
    Returns (divergence_flag, vocal_minority_note).
    """
    if player_metrics is None:
        return False, None

    review_count = player_metrics.get("review_count") or 0
    if review_count < 100:
        return False, None  # too sparse for a meaningful divergence check

    if sentiment_score <= 3.5:
        text_signal = "bearish"
    elif sentiment_score >= 6.5:
        text_signal = "bullish"
    else:
        return False, None  # neutral sentiment — no divergence

    divergence = False
    note = None

    if text_signal == "bearish" and review_count >= 100:
        # Negative sentiment despite a well-reviewed game → possible vocal minority
        divergence = True
        note = (
            f"Sentiment score {sentiment_score}/10 is bearish despite "
            f"{review_count:,} reviews; may reflect vocal-minority noise — "
            "verify with CCU trend before acting on signal."
        )
    elif text_signal == "bullish" and review_count < 500:
        # Positive sentiment but thin review base → could be inflated by fans
        divergence = True
        note = (
            f"Sentiment score {sentiment_score}/10 is bullish but only "
            f"{review_count:,} reviews exist; thin sample — treat with caution."
        )

    return divergence, note
