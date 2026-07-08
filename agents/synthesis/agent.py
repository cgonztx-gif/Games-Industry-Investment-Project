"""
Synthesis Agent - Phase 4

Reads same-week worker outputs from Supabase, computes the authoritative
text-vs-quant divergence check, and writes a structured weekly briefing.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from agents.synthesis.deep_dive import run_deep_dive
from agents.synthesis.email_delivery import send_briefing_email
from database.db_client import get_client, get_weekly_outputs, write_weekly_briefing

_MAX_DEEP_DIVES_PER_WEEK = 2

# source='news' measures media tone/stance, not community sentiment (see
# docs/news-source-decision-memo.md §3 and agents/workers/sentiment/
# news_stance_client.py) -- it must never be averaged into avg_score
# alongside reddit/steam/youtube. Kept as its own news_score/news_themes
# field instead; see investment-synthesis-framework SKILL.md.
_COMMUNITY_SOURCES = {"reddit", "steam", "youtube"}

# Minimum |avg_score - news_score| gap (on the shared 1-10 scale) to surface
# as a news_community_divergence signal -- source disagreement is itself a
# signal, per the memo's "source disagreement is signal" thesis.
_NEWS_COMMUNITY_DIVERGENCE_THRESHOLD = 2.5


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
        community_rows = [r for r in rows if r.get("source") in _COMMUNITY_SOURCES]
        news_rows = [r for r in rows if r.get("source") == "news"]
        scores = [float(r["sentiment_score"]) for r in community_rows if r.get("sentiment_score") is not None]
        news_scores = [float(r["sentiment_score"]) for r in news_rows if r.get("sentiment_score") is not None]
        result[game_id] = {
            "avg_score": _avg(scores),
            "news_score": _avg(news_scores),
            "sources": sorted({r.get("source") for r in rows if r.get("source")}),
            "themes": [t for r in community_rows for t in (r.get("top_themes") or [])][:5],
            "news_themes": [t for r in news_rows for t in (r.get("top_themes") or [])][:5],
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

        news_score = sentiment.get("news_score")
        if (
            news_score is not None
            and score is not None
            and abs(score - news_score) >= _NEWS_COMMUNITY_DIVERGENCE_THRESHOLD
        ):
            divergences.append(
                {
                    "game_id": game_id,
                    "type": "news_community_divergence",
                    "sentiment_score": score,
                    "news_score": news_score,
                    "interpretation": (
                        "Media coverage tone and community sentiment disagree this week "
                        "(community=" + str(score) + ", news=" + str(news_score) + ") "
                        "— worth checking which is leading."
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


def _high_severity_game_ids(risks: list[dict]) -> set[str]:
    return {r["game_id"] for r in risks if r.get("severity") == "high" and r.get("game_id")}


def _game_titles(db, game_ids: list[str]) -> dict[str, str]:
    if not game_ids:
        return {}
    rows = (
        db.table("games")
        .select("game_id,title")
        .in_("game_id", game_ids)
        .execute()
        .data
        or []
    )
    return {row["game_id"]: row["title"] for row in rows}


def _dispatch_deep_dives(
    divergences: list[dict],
    risks: list[dict],
    game_titles: dict[str, str],
    deep_dive_fn=run_deep_dive,
    max_dives: int = _MAX_DEEP_DIVES_PER_WEEK,
) -> list[str]:
    """
    Trigger condition, per investment-synthesis-framework SKILL.md section
    "Deep-Dive Researcher Triggers": dispatch "only for bounded questions that
    can change the briefing." This operationalizes two of that section's six
    documented triggers together -- "Sudden sentiment collapse with stable
    quant" (the bearish_text_stable_quant divergence type) co-occurring with a
    "severe risk flag" (a high-severity entry in _compute_risks for the same
    game_id) -- rather than firing on every divergence, consistent with the
    skill's framing that this is not a fishing expedition. Capped at
    `max_dives` per week as an additional bound on cost/latency.

    Mutates matching divergence dicts in place (adds "deep_dive_triggered" and
    "deep_dive" keys) so callers see the annotation through both `divergences`
    and the `opportunities` sublist (same dict objects). Never raises --
    `deep_dive_fn` already returns None on any internal failure.
    """
    high_severity = _high_severity_game_ids(risks)
    dispatched = 0
    notes: list[str] = []

    for item in divergences:
        if dispatched >= max_dives:
            break
        if item.get("type") != "bearish_text_stable_quant":
            continue
        game_id = item.get("game_id")
        if game_id not in high_severity:
            continue
        title = game_titles.get(game_id)
        if not title:
            continue

        question = (
            f'Is the sentiment drop for "{title}" this week explained by a specific '
            "controversy, a recent patch/balance change, or something else? Cite sources."
        )
        item["deep_dive_triggered"] = True
        result = deep_dive_fn(title, question)
        item["deep_dive"] = result
        dispatched += 1
        notes.append(
            f"Deep dive dispatched for {title} ({game_id}): "
            + ("findings returned" if result else "no findings (failed, refused, or unparseable)")
        )

    return notes


def run(run_date: str | None = None) -> dict:
    db = get_client()
    today = date.fromisoformat(run_date) if run_date else date.today()
    week_start = today - timedelta(days=6)
    outputs = get_weekly_outputs(db, today.isoformat(), week_start.isoformat())

    divergences = _compute_divergence(outputs)
    risks = _compute_risks(outputs)

    deep_dive_notes: list[str] = []
    try:
        bearish_game_ids = [
            d["game_id"] for d in divergences if d["type"] == "bearish_text_stable_quant"
        ]
        game_titles = _game_titles(db, bearish_game_ids)
        deep_dive_notes = _dispatch_deep_dives(divergences, risks, game_titles)
    except Exception as exc:
        print(f"[synthesis] deep-dive dispatch skipped due to error: {exc}")

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
    if deep_dive_notes:
        reasoning_log += " " + " ".join(deep_dive_notes)
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

    try:
        send_briefing_email(briefing)
    except Exception as exc:
        print(f"[synthesis] briefing email skipped due to error: {exc}")

    print(f"[synthesis] {briefing_text}")
    return {
        "date": today.isoformat(),
        "confidence": confidence,
        "divergence_count": len(divergences),
        "risk_count": len(risks),
        "opportunity_count": len(opportunities),
    }
