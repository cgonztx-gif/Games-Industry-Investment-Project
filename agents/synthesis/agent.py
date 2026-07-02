"""
Synthesis Agent - Phase 4

Reads same-week worker outputs from Supabase, computes the authoritative
text-vs-quant divergence check, and writes a structured weekly briefing.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from database.db_client import get_client, get_weekly_outputs, write_weekly_briefing


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _group_by(rows: list[dict], key: str) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        value = row.get(key)
        if value:
            grouped[value].append(row)
    return grouped


def _sentiment_by_game(sentiment_rows: list[dict]) -> dict[str, dict]:
    grouped = _group_by(sentiment_rows, "game_id")
    result: dict[str, dict] = {}
    for game_id, rows in grouped.items():
        scores = [float(row["sentiment_score"]) for row in rows if row.get("sentiment_score") is not None]
        themes = []
        for row in rows:
            for theme in row.get("top_themes") or []:
                themes.append(theme)
        result[game_id] = {
            "avg_score": _avg(scores),
            "sources": sorted({row.get("source") for row in rows if row.get("source")}),
            "themes": themes[:5],
        }
    return result


def _compute_divergence(outputs: dict) -> list[dict]:
    metrics_by_game = {row["game_id"]: row for row in outputs["player_metrics"]}
    sentiment_by_game = _sentiment_by_game(outputs["sentiment"])
    patches_by_game = _group_by(outputs["patch_events"], "game_id")
    divergences = []

    for game_id, sentiment in sentiment_by_game.items():
        score = sentiment.get("avg_score")
        metrics = metrics_by_game.get(game_id)
        if score is None or metrics is None:
            continue

        ccu = metrics.get("concurrent_players")
        review_velocity = metrics.get("review_velocity")
        has_patch = bool(patches_by_game.get(game_id))
        quant_stable = (
            (ccu is None or ccu > 0)
            and (review_velocity is None or review_velocity >= 0)
        )

        if score <= 3.5 and quant_stable:
            divergences.append(
                {
                    "game_id": game_id,
                    "type": "bearish_text_stable_quant",
                    "sentiment_score": score,
                    "concurrent_players": ccu,
                    "review_velocity": review_velocity,
                    "patch_activity_this_week": has_patch,
                    "interpretation": (
                        "Negative text sentiment is not yet confirmed by same-week "
                        "player/review metrics; investigate whether this is an early "
                        "churn warning or a vocal-minority event."
                    ),
                }
            )
        elif score >= 6.5 and not quant_stable:
            divergences.append(
                {
                    "game_id": game_id,
                    "type": "bullish_text_weak_quant",
                    "sentiment_score": score,
                    "concurrent_players": ccu,
                    "review_velocity": review_velocity,
                    "patch_activity_this_week": has_patch,
                    "interpretation": (
                        "Positive text sentiment is not supported by same-week "
                        "quantitative momentum; treat as low-confidence until metrics improve."
                    ),
                }
            )

    return divergences


def _compute_risks(outputs: dict) -> list[dict]:
    sentiment_by_game = _sentiment_by_game(outputs["sentiment"])
    patches_by_game = _group_by(outputs["patch_events"], "game_id")
    risks = []

    for metric in outputs["player_metrics"]:
        game_id = metric["game_id"]
        sentiment = sentiment_by_game.get(game_id, {})
        score = sentiment.get("avg_score")
        review_velocity = metric.get("review_velocity")
        ccu = metric.get("concurrent_players")
        no_patch = not bool(patches_by_game.get(game_id))

        if score is not None and score <= 3.5 and (review_velocity or 0) <= 0 and no_patch:
            risks.append(
                {
                    "game_id": game_id,
                    "severity": "high",
                    "signal": "negative_sentiment_no_quant_momentum_no_patch",
                    "sentiment_score": score,
                    "concurrent_players": ccu,
                    "review_velocity": review_velocity,
                }
            )

    for signal in outputs["studio_signals"]:
        if signal.get("severity") == "high":
            risks.append(
                {
                    "studio_id": signal.get("studio_id"),
                    "severity": "high",
                    "signal": signal.get("signal_type"),
                    "description": signal.get("description"),
                }
            )

    return risks[:10]


def _confidence(outputs: dict) -> str:
    layer_count = sum(
        1
        for key in ("player_metrics", "sentiment", "patch_events", "studio_signals", "equity_signals")
        if outputs.get(key)
    )
    if layer_count >= 4:
        return "medium"
    if layer_count >= 2:
        return "low"
    return "very_low"


def run(run_date: str | None = None) -> dict:
    db = get_client()
    today = date.fromisoformat(run_date) if run_date else date.today()
    week_start = today - timedelta(days=6)
    outputs = get_weekly_outputs(db, today.isoformat(), week_start.isoformat())

    divergences = _compute_divergence(outputs)
    risks = _compute_risks(outputs)
    confidence = _confidence(outputs)
    opportunities = [
        item for item in divergences if item["type"] == "bearish_text_stable_quant"
    ][:5]

    portfolio_update = {
        "confidence": confidence,
        "equity_signals_count": len(outputs["equity_signals"]),
        "divergence_count": len(divergences),
        "risk_count": len(risks),
    }
    notable_events = {
        "patch_events": len(outputs["patch_events"]),
        "studio_signals": len(outputs["studio_signals"]),
    }
    reasoning_log = (
        "Synthesis read same-week worker outputs, grouped sentiment by game, "
        "computed text-vs-quant divergence against same-date player metrics, "
        "then layered patch activity and studio signals into risk flags."
    )
    briefing_text = (
        f"Weekly briefing for {today.isoformat()}: confidence={confidence}; "
        f"{len(divergences)} divergence signal(s), {len(risks)} risk flag(s), "
        f"{len(opportunities)} opportunity candidate(s)."
    )

    briefing = {
        "week_of": today.isoformat(),
        "briefing_text": briefing_text,
        "portfolio_update": portfolio_update,
        "top_opportunities": opportunities,
        "risk_flags": risks,
        "notable_events": notable_events,
        "reasoning_log": reasoning_log,
    }
    write_weekly_briefing(db, briefing)

    print(f"[synthesis] {briefing_text}")
    return {
        "date": today.isoformat(),
        "confidence": confidence,
        "divergence_count": len(divergences),
        "risk_count": len(risks),
        "opportunity_count": len(opportunities),
    }
