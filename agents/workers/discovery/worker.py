"""
Discovery Worker - Phase 6

Scans four external sources for watchlist candidates not yet tracked, scores
them against agents/skills/watchlist-relevance-scoring/SKILL.md's 100-point
rubric (see scoring.py), and writes watchlist_proposals rows for human review.
Never adds a candidate directly to the active watchlist -- that only happens
at manual approval time, which isn't built yet (tracked as a Phase 7
dashboard item: "Build watchlist proposal review queue").

Two proposal shapes:
- Game-level (Steam top-CCU / IGDB upcoming-release sourced): a specific
  game_id, scored via the full rubric in scoring.py.
- Company-level (EDGAR-sourced, agents/workers/discovery/edgar_fulltext_client.py):
  game_id is NULL -- a new filing surfaced a gaming-relevant public company
  not yet in `studios`, but no specific title has been identified. Scored on
  a fixed conservative value (_EDGAR_COMPANY_SCORE, always in the "watch"
  band), not the full rubric, since that rubric assumes a specific game.

Ticker resolution is never guessed (a hard SKILL.md constraint): a Steam/IGDB
candidate's studio_name is matched *exactly* (case-insensitive) against
get_studios_with_tickers()'s existing curated set. No match -> candidate
dropped, no proposal, no new studio row created. EDGAR candidates are the one
exception -- the filing itself discloses the ticker, so a new studio row is
created directly from that.

Reddit is corroboration only, not an independent trigger (per the SKILL:
"Reddit mention spikes must use the existing cached Reddit adapter", which
has no global keyword search) -- it only runs for candidates that already
passed the studio/ticker gate, folding into the "signal coverage" score
component. Its rate limiter (~8-10s/request) means a run touching many
candidates can take several minutes; _MAX_CANDIDATES_PER_SOURCE bounds this.

False-positive learning (kept simple per this session's scope decision): any
game/company that already has a pending or rejected proposal is skipped, so a
rejected candidate isn't re-proposed every week. No rejection-reason taxonomy
or review CLI yet -- deferred alongside Phase 7's dashboard.
"""

from __future__ import annotations

import os
from datetime import date

from agents.workers.discovery import scoring
from agents.workers.discovery.claude_rationale import (
    generate_company_rationale,
    generate_game_rationale,
)
from agents.workers.discovery.edgar_fulltext_client import search_new_gaming_filings
from agents.workers.market_player import igdb_client, steam_client
from agents.workers.sentiment.reddit_cache import SupabaseRedditCache
from agents.workers.sentiment.reddit_source import (
    RedditBlocked,
    build_reddit_source,
    build_subreddit_resolver,
    cached_resolve_subreddit,
)
from database.db_client import (
    find_or_create_game,
    find_or_create_studio,
    get_active_watchlist_external_ids,
    get_client,
    get_existing_company_proposal_status,
    get_existing_proposal_status,
    get_studios_with_tickers,
    get_tracked_game_counts_by_studio,
    write_watchlist_proposal,
)

_STEAM_MIN_CCU = 5000
_IGDB_DAYS_AHEAD = 60
_EDGAR_DAYS_BACK = 14
_EDGAR_MAX_RESULTS = 5
_EDGAR_COMPANY_SCORE = 55  # always "watch" band -- no specific game to score the full rubric against
_MAX_CANDIDATES_PER_SOURCE = 20
_REDDIT_MENTION_THRESHOLD = 5  # posts in the past week to count as a corroborating signal


def _default_igdb_candidates() -> list[dict]:
    client_id = os.environ["TWITCH_CLIENT_ID"]
    client_secret = os.environ["TWITCH_CLIENT_SECRET"]
    token = igdb_client.get_access_token(client_id, client_secret)
    return igdb_client.get_upcoming_releases(client_id, token, days_ahead=_IGDB_DAYS_AHEAD)


def _default_steam_candidates() -> list[dict]:
    return steam_client.get_top_ccu_games(min_ccu=_STEAM_MIN_CCU)


def _match_studio(studio_name: str | None, studios_by_name: dict[str, dict]) -> dict | None:
    """Exact (case-insensitive) match only -- never infer a ticker from name similarity."""
    if not studio_name:
        return None
    return studios_by_name.get(studio_name.strip().lower())


def _days_until(release_date: str | None, today: date) -> int | None:
    if not release_date:
        return None
    try:
        target = date.fromisoformat(release_date)
    except ValueError:
        return None
    return (target - today).days


def _reddit_signal_count(title: str, resolver, lookup_cache, reddit_source) -> int:
    """
    Best-effort mention-volume corroboration for a candidate that already
    passed the studio/ticker gate. Returns 0 (not an error) on any failure --
    Reddit is a corroborating signal here, never a blocker.
    """
    try:
        subreddit = cached_resolve_subreddit(title, resolver, lookup_cache)
        if not subreddit:
            return 0
        posts = reddit_source.fetch_posts(subreddit, sort="new", timeframe="week", limit=25)
        return 1 if len(posts) >= _REDDIT_MENTION_THRESHOLD else 0
    except RedditBlocked:
        return 0
    except Exception:
        return 0


def _score_and_propose_game(
    db,
    candidate: dict,
    studio: dict,
    tracked_counts: dict[str, int],
    signal_count: int,
    days_until_release: int | None,
    generate_rationale_fn,
    find_or_create_game_fn,
    get_existing_proposal_status_fn,
    write_proposal_fn,
) -> dict | None:
    score, components = scoring.score_candidate(
        ccu=candidate.get("ccu"),
        hype=candidate.get("hype"),
        is_live_service=candidate.get("is_live_service", False),
        days_until_release=days_until_release,
        signal_count=signal_count,
        studio_already_tracked=tracked_counts.get(studio["studio_id"], 0) > 0,
    )
    tier = scoring.proposal_tier(score)
    if tier is None:
        return None

    game_id = find_or_create_game_fn(db, candidate, studio["studio_id"])
    if get_existing_proposal_status_fn(db, game_id) is not None:
        return None  # already proposed (pending or rejected) -- simple false-positive learning

    rationale = generate_rationale_fn(
        {**candidate, "studio_name": studio["name"], "ticker": studio.get("ticker")},
        score,
        tier,
        components,
    )
    proposal = {
        "game_id": game_id,
        "studio_id": studio["studio_id"],
        "trigger_signal": candidate["trigger_signal"],
        "claude_rationale": rationale,
        "score": score,
        "recommended_sentiment_tier": "tier_a" if tier == "high_confidence" else "listing_only",
    }
    write_proposal_fn(db, proposal)
    return proposal


def _process_game_candidates(
    db,
    candidates: list[dict],
    trigger_signal: str,
    already_tracked_ids: set[str],
    external_id_key: str,
    studios_by_name: dict[str, dict],
    tracked_counts: dict[str, int],
    reddit_resolver,
    lookup_cache,
    reddit_source,
    days_until_release_fn,
    generate_game_rationale_fn,
    find_or_create_game_fn,
    get_existing_proposal_status_fn,
    write_proposal_fn,
    errors: list[dict],
) -> list[dict]:
    proposals: list[dict] = []
    for raw in candidates[:_MAX_CANDIDATES_PER_SOURCE]:
        candidate = {**raw, "trigger_signal": trigger_signal}
        if candidate.get(external_id_key) in already_tracked_ids:
            continue
        studio = _match_studio(candidate.get("studio_name"), studios_by_name)
        if studio is None:
            continue
        try:
            signal_count = 1 + _reddit_signal_count(
                candidate.get("title", ""), reddit_resolver, lookup_cache, reddit_source
            )
            proposal = _score_and_propose_game(
                db,
                candidate,
                studio,
                tracked_counts,
                signal_count,
                days_until_release_fn(candidate),
                generate_game_rationale_fn,
                find_or_create_game_fn,
                get_existing_proposal_status_fn,
                write_proposal_fn,
            )
        except Exception as exc:  # noqa: BLE001 -- one bad candidate must not kill the run
            errors.append({"source": trigger_signal, "candidate": candidate.get("title"), "error": str(exc)})
            continue
        if proposal:
            proposals.append(proposal)
    return proposals


def _process_edgar_candidates(
    db,
    candidates: list[dict],
    find_or_create_studio_fn,
    get_existing_company_proposal_status_fn,
    generate_company_rationale_fn,
    write_proposal_fn,
    errors: list[dict],
) -> list[dict]:
    proposals: list[dict] = []
    for candidate in candidates:
        try:
            studio_id = find_or_create_studio_fn(
                db,
                {
                    "name": candidate["company_name"],
                    "ticker": candidate["ticker"],
                    "parent_name": None,
                },
            )
            if get_existing_company_proposal_status_fn(db, studio_id) is not None:
                continue
            rationale = generate_company_rationale_fn(candidate)
            proposal = {
                "game_id": None,
                "studio_id": studio_id,
                "trigger_signal": candidate["trigger_signal"],
                "claude_rationale": rationale,
                "score": _EDGAR_COMPANY_SCORE,
                "recommended_sentiment_tier": None,
            }
            write_proposal_fn(db, proposal)
            proposals.append(proposal)
        except Exception as exc:  # noqa: BLE001 -- one bad candidate must not kill the run
            errors.append(
                {"source": "edgar", "candidate": candidate.get("company_name"), "error": str(exc)}
            )
            continue
    return proposals


def run(
    db=None,
    today: date | None = None,
    get_steam_candidates_fn=_default_steam_candidates,
    get_igdb_candidates_fn=_default_igdb_candidates,
    search_edgar_fn=search_new_gaming_filings,
    build_reddit_resolver_fn=build_subreddit_resolver,
    build_reddit_source_fn=build_reddit_source,
    build_lookup_cache_fn=lambda db: SupabaseRedditCache(client=db, source="subreddit_lookup"),
    generate_game_rationale_fn=generate_game_rationale,
    generate_company_rationale_fn=generate_company_rationale,
    get_studios_with_tickers_fn=get_studios_with_tickers,
    get_active_watchlist_external_ids_fn=get_active_watchlist_external_ids,
    get_tracked_game_counts_by_studio_fn=get_tracked_game_counts_by_studio,
    get_existing_proposal_status_fn=get_existing_proposal_status,
    get_existing_company_proposal_status_fn=get_existing_company_proposal_status,
    find_or_create_studio_fn=find_or_create_studio,
    find_or_create_game_fn=find_or_create_game,
    write_proposal_fn=write_watchlist_proposal,
) -> dict:
    active_db = db if db is not None else get_client()
    run_date = today or date.today()
    errors: list[dict] = []

    studios = get_studios_with_tickers_fn(active_db)
    studios_by_name = {s["name"].strip().lower(): s for s in studios if s.get("name")}
    known_tickers = {s["ticker"] for s in studios if s.get("ticker")}
    tracked_external_ids = get_active_watchlist_external_ids_fn(active_db)
    tracked_counts = get_tracked_game_counts_by_studio_fn(active_db)

    lookup_cache = build_lookup_cache_fn(active_db)
    reddit_resolver = build_reddit_resolver_fn()
    reddit_source = build_reddit_source_fn(
        lambda source: SupabaseRedditCache(client=active_db, source=source)
    )

    try:
        steam_candidates = get_steam_candidates_fn()
    except Exception as exc:  # noqa: BLE001
        errors.append({"source": "steam", "error": str(exc)})
        steam_candidates = []

    steam_proposals = _process_game_candidates(
        active_db,
        steam_candidates,
        "steam_top_ccu_entry",
        tracked_external_ids["steam_ids"],
        "steam_app_id",
        studios_by_name,
        tracked_counts,
        reddit_resolver,
        lookup_cache,
        reddit_source,
        days_until_release_fn=lambda _c: None,  # Steam top-CCU entries are already live
        generate_game_rationale_fn=generate_game_rationale_fn,
        find_or_create_game_fn=find_or_create_game_fn,
        get_existing_proposal_status_fn=get_existing_proposal_status_fn,
        write_proposal_fn=write_proposal_fn,
        errors=errors,
    )

    try:
        igdb_candidates = get_igdb_candidates_fn()
    except Exception as exc:  # noqa: BLE001
        errors.append({"source": "igdb", "error": str(exc)})
        igdb_candidates = []

    igdb_proposals = _process_game_candidates(
        active_db,
        igdb_candidates,
        "igdb_upcoming_release",
        tracked_external_ids["igdb_ids"],
        "igdb_id",
        studios_by_name,
        tracked_counts,
        reddit_resolver,
        lookup_cache,
        reddit_source,
        days_until_release_fn=lambda c: _days_until(c.get("release_date"), run_date),
        generate_game_rationale_fn=generate_game_rationale_fn,
        find_or_create_game_fn=find_or_create_game_fn,
        get_existing_proposal_status_fn=get_existing_proposal_status_fn,
        write_proposal_fn=write_proposal_fn,
        errors=errors,
    )

    try:
        edgar_candidates = search_edgar_fn(
            known_tickers, days_back=_EDGAR_DAYS_BACK, max_results=_EDGAR_MAX_RESULTS
        )
    except Exception as exc:  # noqa: BLE001
        errors.append({"source": "edgar", "error": str(exc)})
        edgar_candidates = []

    edgar_proposals = _process_edgar_candidates(
        active_db,
        edgar_candidates,
        find_or_create_studio_fn,
        get_existing_company_proposal_status_fn,
        generate_company_rationale_fn,
        write_proposal_fn,
        errors,
    )

    game_level = steam_proposals + igdb_proposals
    all_proposals = game_level + edgar_proposals

    print(
        f"[discovery] {len(all_proposals)} proposals written "
        f"({len(game_level)} game-level, {len(edgar_proposals)} company-level) | "
        f"{len(errors)} errors"
    )

    return {
        "date": run_date.isoformat(),
        "proposals_written": len(all_proposals),
        "game_level_count": len(game_level),
        "company_level_count": len(edgar_proposals),
        "error_count": len(errors),
        "proposals": all_proposals,
        "errors": errors,
    }
