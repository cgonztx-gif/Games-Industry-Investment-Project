"""
Deterministic 0-100 scoring for Discovery candidates, per the 100-point rubric
in agents/skills/watchlist-relevance-scoring/SKILL.md Section 2. Pure functions,
no DB/API calls, so results are reproducible and auditable -- Claude is only
used downstream for rationale *text* (see claude_rationale.py), never for the
score itself.

score_candidate() is only ever called on candidates that already passed the
required gates in worker.py (most importantly: studio/ticker resolved via
exact-name match against get_studios_with_tickers(), never guessed). That's
why "public-parent confidence" below is a fixed 25 rather than a computed
component -- an ungated candidate never reaches this function at all.
"""

from __future__ import annotations

_PUBLIC_PARENT_CONFIDENCE = 25  # fixed: only gated (ticker-resolved) candidates score

HIGH_CONFIDENCE_THRESHOLD = 70
WATCH_THRESHOLD = 50


def _materiality_score(ccu: int | None, hype: int | None) -> int:
    """Best available evidence of scale: current CCU (Steam) or pre-release hype (IGDB)."""

    def _bucket(value: int | None, tiers: list[tuple[int, int]]) -> int:
        if value is None:
            return 0
        for floor, points in tiers:
            if value >= floor:
                return points
        return 0

    ccu_score = _bucket(ccu, [(50_000, 25), (10_000, 18), (1_000, 10), (1, 5)])
    hype_score = _bucket(hype, [(1_000, 25), (200, 18), (50, 10), (1, 5)])
    return max(ccu_score, hype_score)


def _live_service_score(is_live_service: bool) -> int:
    # Single-player premium titles can still be investable per the SKILL, so
    # non-live-service is a lower score, not zero.
    return 20 if is_live_service else 8


def _signal_coverage_score(signal_count: int) -> int:
    return min(signal_count, 3) * 5


def _timeliness_score(days_until_release: int | None, has_current_activity: bool) -> int:
    if days_until_release is not None and 0 <= days_until_release <= 60:
        return 10
    if has_current_activity:
        return 6
    return 3


def _uniqueness_score(studio_already_tracked: bool) -> int:
    return 2 if studio_already_tracked else 5


def score_candidate(
    *,
    ccu: int | None = None,
    hype: int | None = None,
    is_live_service: bool = False,
    days_until_release: int | None = None,
    signal_count: int = 0,
    studio_already_tracked: bool = False,
) -> tuple[int, dict[str, int]]:
    """
    Returns (score, components). components is keyed by rubric dimension name
    for building an auditable rationale/current_signal string.
    """
    components = {
        "public_parent_confidence": _PUBLIC_PARENT_CONFIDENCE,
        "materiality": _materiality_score(ccu, hype),
        "live_service": _live_service_score(is_live_service),
        "signal_coverage": _signal_coverage_score(signal_count),
        "timeliness": _timeliness_score(days_until_release, has_current_activity=ccu is not None),
        "uniqueness": _uniqueness_score(studio_already_tracked),
    }
    return sum(components.values()), components


def proposal_tier(score: int) -> str | None:
    """
    'high_confidence' (>=70), 'watch' (50-69), or None (<50 -- per the SKILL,
    reject/ignore; worker.py must not create a proposal for this candidate).
    """
    if score >= HIGH_CONFIDENCE_THRESHOLD:
        return "high_confidence"
    if score >= WATCH_THRESHOLD:
        return "watch"
    return None
