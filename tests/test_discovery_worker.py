"""
Unit tests for agents/workers/discovery/worker.py.

No live network / Supabase calls. run() exposes every DB/API touch point as an
injectable ``<verb>_fn``, so each test drives the full flow with plain fakes,
mirroring tests/test_returns_tracker.py's / tests/test_health_score.py's style.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.workers.discovery import worker
from agents.workers.sentiment.reddit_source import RedditBlocked

_FAKE_DB = "fake-db-sentinel"

_KNOWN_STUDIO = {"studio_id": "studio-1", "ticker": "KNOW", "name": "Known Studio"}


def _steam_candidate(**overrides):
    base = {
        "steam_app_id": "123",
        "title": "Big Hit Game",
        "ccu": 60_000,
        "genres": [],
        "is_live_service": True,
        "igdb_id": None,
        "rawg_slug": None,
        "release_date": None,
        "studio_name": "Known Studio",
        "ticker": None,
    }
    base.update(overrides)
    return base


def _igdb_candidate(**overrides):
    base = {
        "igdb_id": "55",
        "title": "Upcoming Hit",
        "genres": [],
        "platforms": [],
        "release_date": "2026-08-01",
        "is_live_service": False,
        "steam_app_id": None,
        "rawg_slug": None,
        "ccu": None,
        "studio_name": "Known Studio",
        "ticker": None,
        "hype": 500,
    }
    base.update(overrides)
    return base


def _edgar_candidate(**overrides):
    base = {
        "company_name": "New Gaming Co",
        "ticker": "NEWG",
        "cik": "123",
        "form": "S-1",
        "items": [],
        "file_date": "2026-07-01",
        "source_url": "http://example.test",
        "trigger_signal": "edgar_new_ipo_filing",
    }
    base.update(overrides)
    return base


def _run(**overrides):
    """run() with inert defaults (no candidates from any source); tests override the seams they exercise."""
    written = overrides.pop("_written", None)
    if written is None:
        written = []

    kwargs = dict(
        db=_FAKE_DB,
        today=date(2026, 7, 8),
        get_steam_candidates_fn=lambda: [],
        get_igdb_candidates_fn=lambda: [],
        search_edgar_fn=lambda known, days_back, max_results: [],
        build_reddit_resolver_fn=lambda: _NullResolver(),
        build_reddit_source_fn=lambda cache_factory: _NullRedditSource(),
        build_lookup_cache_fn=lambda db: _NullLookupCache(),
        generate_game_rationale_fn=lambda *a, **k: "game rationale",
        generate_company_rationale_fn=lambda c: "company rationale",
        get_studios_with_tickers_fn=lambda db: [_KNOWN_STUDIO],
        get_active_watchlist_external_ids_fn=lambda db: {"steam_ids": set(), "igdb_ids": set()},
        get_tracked_game_counts_by_studio_fn=lambda db: {},
        get_existing_proposal_status_fn=lambda db, gid: None,
        get_existing_company_proposal_status_fn=lambda db, sid: None,
        find_or_create_studio_fn=lambda db, s: "new-studio-id",
        find_or_create_game_fn=lambda db, g, sid: f"game-id:{g.get('steam_app_id') or g.get('igdb_id')}",
        write_proposal_fn=lambda db, p: written.append(p),
    )
    kwargs.update(overrides)
    return worker.run(**kwargs), written


class _NullResolver:
    def resolve_subreddit(self, title):
        return None


class _NullRedditSource:
    def fetch_posts(self, *a, **k):
        return []


class _NullLookupCache:
    def get(self, *a, **k):
        return None

    def set(self, *a, **k):
        pass


class _BoomResolver:
    def resolve_subreddit(self, title):
        raise RedditBlocked("blocked")


def test_no_candidates_from_any_source_writes_nothing():
    result, written = _run()
    assert result["proposals_written"] == 0
    assert result["error_count"] == 0
    assert written == []


def test_steam_candidate_with_known_studio_and_high_ccu_gets_proposed():
    result, written = _run(get_steam_candidates_fn=lambda: [_steam_candidate()])
    assert result["game_level_count"] == 1
    assert len(written) == 1
    proposal = written[0]
    assert proposal["trigger_signal"] == "steam_top_ccu_entry"
    assert proposal["studio_id"] == "studio-1"
    assert proposal["score"] >= 70
    assert proposal["recommended_sentiment_tier"] == "tier_a"


def test_candidate_with_unmatched_studio_is_dropped_never_guesses_ticker():
    result, written = _run(
        get_steam_candidates_fn=lambda: [_steam_candidate(studio_name="Totally Unknown Indie Studio")]
    )
    assert result["game_level_count"] == 0
    assert written == []


def test_already_active_watchlist_candidate_is_excluded():
    result, written = _run(
        get_steam_candidates_fn=lambda: [_steam_candidate(steam_app_id="123")],
        get_active_watchlist_external_ids_fn=lambda db: {"steam_ids": {"123"}, "igdb_ids": set()},
    )
    assert result["game_level_count"] == 0
    assert written == []


def test_low_score_candidate_is_not_proposed():
    # No CCU signal, not live-service, not upcoming -- well under the 50-point floor.
    result, written = _run(
        get_steam_candidates_fn=lambda: [
            _steam_candidate(ccu=0, is_live_service=False)
        ]
    )
    assert result["game_level_count"] == 0
    assert written == []


def test_already_proposed_game_is_not_re_proposed():
    result, written = _run(
        get_steam_candidates_fn=lambda: [_steam_candidate()],
        get_existing_proposal_status_fn=lambda db, gid: "rejected",
    )
    assert result["game_level_count"] == 0
    assert written == []


def test_igdb_candidate_uses_release_date_for_timeliness_and_igdb_id_dedup():
    result, written = _run(
        get_igdb_candidates_fn=lambda: [_igdb_candidate(igdb_id="55")],
        get_active_watchlist_external_ids_fn=lambda db: {"steam_ids": set(), "igdb_ids": {"55"}},
    )
    assert result["game_level_count"] == 0
    assert written == []

    result2, written2 = _run(get_igdb_candidates_fn=lambda: [_igdb_candidate(igdb_id="56")])
    assert result2["game_level_count"] == 1
    assert written2[0]["trigger_signal"] == "igdb_upcoming_release"


def test_edgar_candidate_creates_null_game_id_proposal():
    result, written = _run(search_edgar_fn=lambda known, days_back, max_results: [_edgar_candidate()])
    assert result["company_level_count"] == 1
    assert len(written) == 1
    proposal = written[0]
    assert proposal["game_id"] is None
    assert proposal["studio_id"] == "new-studio-id"
    assert proposal["trigger_signal"] == "edgar_new_ipo_filing"
    assert 50 <= proposal["score"] < 70
    assert proposal["recommended_sentiment_tier"] is None


def test_edgar_candidate_already_proposed_for_studio_is_skipped():
    result, written = _run(
        search_edgar_fn=lambda known, days_back, max_results: [_edgar_candidate()],
        get_existing_company_proposal_status_fn=lambda db, sid: "pending",
    )
    assert result["company_level_count"] == 0
    assert written == []


def test_steam_source_failure_degrades_to_zero_candidates_not_a_crash():
    def _boom():
        raise RuntimeError("steam api down")

    result, written = _run(get_steam_candidates_fn=_boom)
    assert result["error_count"] == 1
    assert result["errors"][0]["source"] == "steam"
    assert result["game_level_count"] == 0
    assert written == []


def test_igdb_source_failure_degrades_to_zero_candidates_not_a_crash():
    def _boom():
        raise RuntimeError("igdb auth failed")

    result, written = _run(get_igdb_candidates_fn=_boom)
    assert result["error_count"] == 1
    assert result["errors"][0]["source"] == "igdb"


def test_edgar_source_failure_degrades_to_zero_candidates_not_a_crash():
    def _boom(known, days_back, max_results):
        raise RuntimeError("edgar search failed")

    result, written = _run(search_edgar_fn=_boom)
    assert result["error_count"] == 1
    assert result["errors"][0]["source"] == "edgar"


def test_reddit_blocked_during_corroboration_does_not_prevent_proposal():
    result, written = _run(
        get_steam_candidates_fn=lambda: [_steam_candidate()],
        build_reddit_resolver_fn=lambda: _BoomResolver(),
    )
    # Reddit corroboration failing should just mean signal_count doesn't get
    # the +1 bump -- the candidate still scores well above the floor on CCU
    # + live-service alone and gets proposed.
    assert result["game_level_count"] == 1
    assert len(written) == 1


def test_one_bad_candidate_does_not_block_other_candidates_in_same_source():
    def _boom_find_or_create_game(db, g, sid):
        if g["steam_app_id"] == "bad":
            raise RuntimeError("db write failed")
        return f"game-id:{g['steam_app_id']}"

    result, written = _run(
        get_steam_candidates_fn=lambda: [
            _steam_candidate(steam_app_id="bad", title="Bad Candidate"),
            _steam_candidate(steam_app_id="good", title="Good Candidate"),
        ],
        find_or_create_game_fn=_boom_find_or_create_game,
    )
    assert result["error_count"] == 1
    assert result["game_level_count"] == 1
    assert written[0]["game_id"] == "game-id:good"
