"""
Composite health_score for equity_signals rows.

Deterministic, quantitative-only composite (0-10, higher = healthier) built
from four existing worker outputs across a ticker's mapped games: community
sentiment, patch cadence status, player-count momentum, and studio distress
signals. Each component is independently computed and only included in the
average if data exists for it -- a ticker with zero worker coverage yet
(most of the watchlist, per the Supabase data-gap audit) gets health_score =
None rather than a fabricated neutral score.

This is deliberately NOT the materiality-weighted health score described in
agents/skills/equity-signal-mapping/SKILL.md Section 2 -- that weighting
(High/Medium/Low/Immaterial per game) is a judgment call with no persisted
data source anywhere in this codebase yet, and would need an LLM reasoning
step this worker doesn't currently have. Every mapped game and every mapped
studio is weighted equally here. Treat this as a quantitative floor that a
future materiality-aware pass can refine, not the final word from the skill.
"""

from __future__ import annotations

_SENTIMENT_LOOKBACK_DAYS = 14
_PATCH_LOOKBACK_DAYS = 60
_PLAYER_LOOKBACK_DAYS = 30
_STUDIO_SIGNAL_LOOKBACK_DAYS = 90

_CADENCE_SCORES = {"on_pace": 10.0, "baseline": 7.0, "slowing": 3.0, "absent": 0.0}
_SEVERITY_SCORES = {"low": 7.0, "medium": 4.0, "high": 0.0}

LOOKBACK_DAYS = {
    "sentiment": _SENTIMENT_LOOKBACK_DAYS,
    "patch_events": _PATCH_LOOKBACK_DAYS,
    "player_metrics": _PLAYER_LOOKBACK_DAYS,
    "studio_signals": _STUDIO_SIGNAL_LOOKBACK_DAYS,
}


def _latest_per_game(rows: list[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for row in rows:
        gid = row.get("game_id")
        if gid is None:
            continue
        if gid not in latest or row["date"] > latest[gid]["date"]:
            latest[gid] = row
    return latest


def _two_most_recent_per_game(rows: list[dict]) -> dict[str, list[dict]]:
    by_game: dict[str, list[dict]] = {}
    for row in rows:
        gid = row.get("game_id")
        if gid is None:
            continue
        by_game.setdefault(gid, []).append(row)
    result = {}
    for gid, game_rows in by_game.items():
        game_rows.sort(key=lambda r: r["date"], reverse=True)
        if len(game_rows) >= 2:
            result[gid] = game_rows[:2]
    return result


def _momentum_score(latest_ccu: float, previous_ccu: float) -> float | None:
    if not previous_ccu:
        return None
    pct_change = (latest_ccu - previous_ccu) / previous_ccu * 100
    if pct_change >= 10:
        return 10.0
    if pct_change >= 0:
        return 7.0
    if pct_change >= -10:
        return 5.0
    if pct_change >= -25:
        return 3.0
    return 0.0


def _sentiment_component(sentiment_rows: list[dict]) -> float | None:
    scores = [
        float(row["sentiment_score"])
        for row in sentiment_rows
        if row.get("sentiment_score") is not None
    ]
    return sum(scores) / len(scores) if scores else None


def _patch_cadence_component(patch_rows: list[dict]) -> float | None:
    latest = _latest_per_game(patch_rows)
    scores = [
        _CADENCE_SCORES[row["cadence_status"]]
        for row in latest.values()
        if row.get("cadence_status") in _CADENCE_SCORES
    ]
    return sum(scores) / len(scores) if scores else None


def _player_momentum_component(player_metrics_rows: list[dict]) -> float | None:
    pairs = _two_most_recent_per_game(player_metrics_rows)
    scores = []
    for game_rows in pairs.values():
        latest_ccu = game_rows[0].get("concurrent_players")
        previous_ccu = game_rows[1].get("concurrent_players")
        if latest_ccu is None or previous_ccu is None:
            continue
        score = _momentum_score(latest_ccu, previous_ccu)
        if score is not None:
            scores.append(score)
    return sum(scores) / len(scores) if scores else None


def _studio_distress_component(studio_ids: list[str], studio_signal_rows: list[dict]) -> float | None:
    """
    None if the ticker has no known studio mapping (nothing to check). If a
    studio mapping exists, absence of studio_signals rows in the lookback
    window means "no distress event found" (studio_signals is an event log,
    written only when something notable happens -- see write_studio_signal),
    so this defaults to a clean 10.0 rather than being omitted.
    """
    if not studio_ids:
        return None
    severities = [
        _SEVERITY_SCORES[row["severity"]]
        for row in studio_signal_rows
        if row.get("severity") in _SEVERITY_SCORES
    ]
    return min(severities) if severities else 10.0


def compute_health_score(
    *,
    sentiment_rows: list[dict],
    patch_event_rows: list[dict],
    player_metrics_rows: list[dict],
    studio_ids: list[str],
    studio_signal_rows: list[dict],
) -> tuple[float | None, dict[str, float]]:
    """
    Returns (health_score, components). health_score is the equal-weighted
    average of whichever components have data, rounded to 1 decimal; None if
    no component has any data at all (the common case for still-uncovered
    watchlist entries). components only contains the components that fired,
    keyed by name, for building an auditable current_signal string.
    """
    components = {}

    sentiment = _sentiment_component(sentiment_rows)
    if sentiment is not None:
        components["sentiment"] = round(sentiment, 1)

    patch_cadence = _patch_cadence_component(patch_event_rows)
    if patch_cadence is not None:
        components["patch_cadence"] = round(patch_cadence, 1)

    player_momentum = _player_momentum_component(player_metrics_rows)
    if player_momentum is not None:
        components["player_momentum"] = round(player_momentum, 1)

    studio_distress = _studio_distress_component(studio_ids, studio_signal_rows)
    if studio_distress is not None:
        components["studio_distress"] = round(studio_distress, 1)

    if not components:
        return None, components

    health_score = round(sum(components.values()) / len(components), 1)
    return health_score, components
