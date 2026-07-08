"""
Unit tests for scripts/rawg_backfill.py.

No live Supabase client or RAWG API: _get_games_missing_steam is exercised
against a local _FakeGamesTable that mimics the chained
select/not_/is_/order/range/execute builder supabase-py returns, and
_state_skip_ids / _is_stale_no_match are pure functions exercised directly.
Same house convention as tests/test_gdelt_client.py: plain recording fakes,
no mocking library, zero live network/DB calls.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.rawg_backfill import (
    FETCH_PAGE_SIZE,
    _get_games_missing_steam,
    _is_stale_no_match,
    _state_skip_ids,
)


class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeGamesTable:
    """Mimics the chained supabase-py query builder used by _get_games_missing_steam."""

    def __init__(self, rows):
        self._rows = rows
        self._range: tuple[int, int] | None = None

    def select(self, *_a, **_k):
        return self

    @property
    def not_(self):
        return self

    def is_(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def execute(self):
        if self._range is None:
            return _FakeResult(list(self._rows))
        start, end = self._range
        return _FakeResult(self._rows[start : end + 1])


class _FakeClient:
    def __init__(self, rows):
        self._rows = rows
        self.table_calls = 0

    def table(self, _name):
        self.table_calls += 1
        return _FakeGamesTable(self._rows)


def _rows(n):
    return [{"game_id": str(i), "title": f"Game {i:04d}", "rawg_slug": f"slug-{i}"} for i in range(n)]


def _attempt(status, days_old=None):
    entry = {"status": status, "title": "Some Game"}
    if days_old is not None:
        attempted_at = datetime.now(timezone.utc) - timedelta(days=days_old)
        entry["attempted_at"] = attempted_at.isoformat(timespec="seconds")
    return entry


# --- _get_games_missing_steam pagination (BUG 2) ---


def test_get_games_missing_steam_returns_more_than_one_page():
    total = FETCH_PAGE_SIZE + 250
    client = _FakeClient(_rows(total))

    result = _get_games_missing_steam(client, limit=0, offset=0)

    assert len(result) == total
    assert client.table_calls == 2  # one full page + one partial page


def test_get_games_missing_steam_limit_spans_multiple_pages():
    total = FETCH_PAGE_SIZE + 250
    client = _FakeClient(_rows(total))

    result = _get_games_missing_steam(client, limit=FETCH_PAGE_SIZE + 100, offset=0)

    assert len(result) == FETCH_PAGE_SIZE + 100
    assert result[0]["game_id"] == "0"
    assert result[-1]["game_id"] == str(FETCH_PAGE_SIZE + 99)


def test_get_games_missing_steam_limit_under_one_page_fetches_single_page():
    client = _FakeClient(_rows(FETCH_PAGE_SIZE + 250))

    result = _get_games_missing_steam(client, limit=500, offset=0)

    assert len(result) == 500
    assert client.table_calls == 1


def test_get_games_missing_steam_respects_offset():
    client = _FakeClient(_rows(FETCH_PAGE_SIZE + 250))

    result = _get_games_missing_steam(client, limit=100, offset=FETCH_PAGE_SIZE)

    assert len(result) == 100
    assert result[0]["game_id"] == str(FETCH_PAGE_SIZE)


def test_get_games_missing_steam_empty_result():
    client = _FakeClient([])

    result = _get_games_missing_steam(client, limit=0, offset=0)

    assert result == []
    assert client.table_calls == 1


# --- _state_skip_ids / _is_stale_no_match stale retry (BUG 1) ---


def test_matched_rows_always_skipped():
    state = {"rawg_missing": {"1": _attempt("matched")}}

    skip_ids = _state_skip_ids(state, retry_errors=False, retry_stale_days=30)

    assert skip_ids == {"1"}


def test_no_match_skipped_when_recent():
    state = {"rawg_missing": {"1": _attempt("no_match", days_old=5)}}

    skip_ids = _state_skip_ids(state, retry_errors=False, retry_stale_days=30)

    assert skip_ids == {"1"}


def test_no_match_eligible_for_retry_when_stale():
    state = {"rawg_missing": {"1": _attempt("no_match", days_old=45)}}

    skip_ids = _state_skip_ids(state, retry_errors=False, retry_stale_days=30)

    assert skip_ids == set()


def test_no_match_never_retried_when_retry_stale_days_is_zero():
    state = {"rawg_missing": {"1": _attempt("no_match", days_old=9999)}}

    skip_ids = _state_skip_ids(state, retry_errors=False, retry_stale_days=0)

    assert skip_ids == {"1"}


def test_error_rows_use_existing_retry_errors_flag_unaffected_by_stale_days():
    state = {"rawg_missing": {"1": _attempt("error", days_old=9999)}}

    assert _state_skip_ids(state, retry_errors=False, retry_stale_days=30) == {"1"}
    assert _state_skip_ids(state, retry_errors=True, retry_stale_days=30) == set()


def test_is_stale_no_match_missing_timestamp_treated_as_stale():
    assert _is_stale_no_match(None, retry_stale_days=30, now=datetime.now(timezone.utc)) is True


def test_is_stale_no_match_unparseable_timestamp_treated_as_stale():
    assert _is_stale_no_match("not-a-date", retry_stale_days=30, now=datetime.now(timezone.utc)) is True


def test_is_stale_no_match_boundary_is_inclusive():
    now = datetime.now(timezone.utc)
    exactly_30_days_ago = (now - timedelta(days=30)).isoformat(timespec="seconds")

    assert _is_stale_no_match(exactly_30_days_ago, retry_stale_days=30, now=now) is True
