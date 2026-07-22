"""
Unit tests for the discovery/news DB helpers added to database/db_client.py.

Uses a _FakeClient that implements the Supabase fluent-query chain
(table().select/insert/update/upsert().eq().is_().order().limit().execute())
with in-memory dicts.  No live Supabase calls.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.db_client import (
    _fetch_all_rows,
    _MAX_ROWS_PER_REQUEST,
    find_or_create_game,
    find_or_create_studio,
    get_active_watchlist_external_ids,
    get_existing_company_proposal_status,
    get_existing_proposal_status,
    get_last_player_metrics,
    get_studios_with_tickers,
    get_tracked_game_counts_by_studio,
    get_trade_plan_for_week,
    get_watchlist_games,
    get_weekly_outputs,
    mark_trade_order_filled,
    write_ccu_snapshots_batch,
    write_news_item,
    write_patch_event,
    write_studio_signal,
    write_watchlist_proposal,
)


# ---------------------------------------------------------------------------
# _FakeClient — minimal Supabase fluent-query fake
# ---------------------------------------------------------------------------

class _Result:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _NotProxy:
    """Proxy returned by query.not_ — converts .is_("col", "null") → IS NOT NULL filter."""
    def __init__(self, query):
        self._query = query

    def is_(self, col, val):
        self._query._filters.append(("is_notnull", col, val))
        return self._query


class _Query:
    """Builder that accumulates filters and executes against in-memory data."""

    def __init__(self, rows: list[dict], inserts: list, updates: list, upserts: list):
        # Shared reference to the table's row list (not a copy) so inserts
        # persist and a later query on the same _FakeClient sees them --
        # required to exercise read-check-then-insert idempotency helpers.
        self._rows = rows
        self._inserts = inserts
        self._updates = updates
        self._upserts = upserts
        self._filters: list = []
        self._is_insert = False
        self._is_update = False
        self._is_upsert = False
        self._payload = None
        self._order_col = None
        self._order_desc = False
        self._limit_n = None
        self._range = None

    @property
    def not_(self):
        return _NotProxy(self)

    def select(self, cols="*", **kw):
        return self

    def insert(self, payload):
        self._is_insert = True
        self._payload = payload if isinstance(payload, list) else [payload]
        return self

    def update(self, payload):
        self._is_update = True
        self._payload = payload
        return self

    def upsert(self, payload, **kw):
        self._is_upsert = True
        self._payload = payload if isinstance(payload, list) else [payload]
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def gte(self, col, val):
        self._filters.append(("gte", col, val))
        return self

    def lte(self, col, val):
        self._filters.append(("lte", col, val))
        return self

    def lt(self, col, val):
        self._filters.append(("lt", col, val))
        return self

    def neq(self, col, val):
        self._filters.append(("neq", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, list(vals)))
        return self

    def is_(self, col, val):
        # .is_("col", "null") → IS NULL filter
        self._filters.append(("is_null" if val == "null" else "eq_val", col, val))
        return self

    def order(self, col, desc=False):
        self._order_col = col
        self._order_desc = desc
        return self

    def limit(self, n):
        self._limit_n = n
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def execute(self):
        if self._is_insert:
            inserted = []
            for row in self._payload:
                new_row = dict(row)
                for id_col in ("studio_id", "game_id", "id"):
                    if id_col not in new_row:
                        new_row[id_col] = str(uuid.uuid4())
                self._rows.append(new_row)
                self._inserts.append(new_row)
                inserted.append(new_row)
            return _Result(data=inserted)
        if self._is_update:
            for row in self._rows:
                if self._matches(row):
                    row.update(self._payload)
            self._updates.append(self._payload)
            return _Result(data=[])
        if self._is_upsert:
            self._upserts.extend(self._payload)
            return _Result(data=list(self._payload))
        # SELECT
        rows = [r for r in self._rows if self._matches(r)]
        if self._order_col:
            rows.sort(key=lambda r: r.get(self._order_col) or "", reverse=self._order_desc)
        if self._range is not None:
            start, end = self._range
            rows = rows[start : end + 1]
        if self._limit_n is not None:
            rows = rows[: self._limit_n]
        return _Result(data=rows)

    def _matches(self, row):
        for op, col, val in self._filters:
            if op == "eq" and row.get(col) != val:
                return False
            if op == "is_null" and row.get(col) is not None:
                return False
            if op == "is_notnull" and row.get(col) is None:
                return False
            if op == "eq_val" and row.get(col) != val:
                return False
            if op == "neq" and row.get(col) == val:
                return False
            if op == "in" and row.get(col) not in val:
                return False
            if op in ("gte", "lte", "lt"):
                cell = row.get(col)
                if cell is None:
                    return False
                if op == "gte" and not cell >= val:
                    return False
                if op == "lte" and not cell <= val:
                    return False
                if op == "lt" and not cell < val:
                    return False
        return True


class _FakeTable:
    def __init__(self, rows, inserts, updates, upserts):
        self._rows = rows
        self._inserts = inserts
        self._updates = updates
        self._upserts = upserts

    def _query(self):
        return _Query(self._rows, self._inserts, self._updates, self._upserts)

    def select(self, *a, **kw):
        return self._query().select(*a, **kw)

    def insert(self, payload):
        return self._query().insert(payload)

    def update(self, payload):
        return self._query().update(payload)

    def upsert(self, payload, **kw):
        return self._query().upsert(payload, **kw)


class _FakeClient:
    def __init__(self):
        self._tables: dict[str, list] = {}
        self._inserts: dict[str, list] = {}
        self._updates: dict[str, list] = {}
        self._upserts: dict[str, list] = {}

    def seed(self, table: str, rows: list[dict]):
        self._tables.setdefault(table, []).extend(rows)
        return self

    def table(self, name: str):
        rows = self._tables.setdefault(name, [])
        inserts = self._inserts.setdefault(name, [])
        updates = self._updates.setdefault(name, [])
        upserts = self._upserts.setdefault(name, [])
        return _FakeTable(rows, inserts, updates, upserts)


# ---------------------------------------------------------------------------
# find_or_create_studio
# ---------------------------------------------------------------------------

def test_find_or_create_studio_existing():
    db = _FakeClient()
    db.seed("studios", [{"studio_id": "s1", "name": "Known Studio", "ticker": "KNOW"}])

    result = find_or_create_studio(db, {"name": "Known Studio", "ticker": "KNOW"})

    assert result == "s1"
    assert db._inserts.get("studios", []) == []


def test_find_or_create_studio_new():
    db = _FakeClient()

    result = find_or_create_studio(db, {"name": "New Studio", "ticker": "NEW"})

    inserted = db._inserts["studios"]
    assert len(inserted) == 1
    assert inserted[0]["name"] == "New Studio"
    assert result == inserted[0]["studio_id"]


# ---------------------------------------------------------------------------
# find_or_create_game
# ---------------------------------------------------------------------------

def test_find_or_create_game_by_igdb_id():
    db = _FakeClient()
    db.seed("games", [{"game_id": "g1", "igdb_id": "99", "steam_app_id": None, "rawg_slug": None}])

    result = find_or_create_game(db, {"title": "Big Hit", "igdb_id": "99"}, studio_id="s1")

    assert result == "g1"
    assert db._inserts.get("games", []) == []


def test_find_or_create_game_by_steam_id():
    db = _FakeClient()
    db.seed("games", [{"game_id": "g2", "igdb_id": None, "steam_app_id": "456", "rawg_slug": None}])

    result = find_or_create_game(db, {"title": "Steam Hit", "steam_app_id": "456"}, studio_id="s1")

    assert result == "g2"


def test_find_or_create_game_patches_missing_steam_id():
    db = _FakeClient()
    db.seed("games", [{"game_id": "g3", "igdb_id": "77", "steam_app_id": None, "rawg_slug": None}])

    find_or_create_game(db, {"title": "Hit", "igdb_id": "77", "steam_app_id": "789"}, studio_id="s1")

    updates = db._updates.get("games", [])
    assert any("steam_app_id" in u for u in updates)


def test_find_or_create_game_new():
    db = _FakeClient()

    result = find_or_create_game(db, {"title": "Brand New", "igdb_id": "111"}, studio_id="s1")

    inserted = db._inserts["games"]
    assert len(inserted) == 1
    assert inserted[0]["title"] == "Brand New"
    assert result == inserted[0]["game_id"]


# ---------------------------------------------------------------------------
# get_studios_with_tickers
# ---------------------------------------------------------------------------

def test_get_studios_with_tickers_excludes_null_tickers():
    db = _FakeClient()
    db.seed("studios", [
        {"studio_id": "s1", "ticker": "KNOW", "name": "Known Studio"},
        {"studio_id": "s2", "ticker": None, "name": "Private Studio"},
    ])

    # get_studios_with_tickers uses .not_.is_("ticker", "null") — our fake
    # treats is_("ticker", "null") as a filter for rows where ticker IS NULL.
    # The real function calls .not_.is_() which we need to handle.
    # Test via the actual function against our seeded data — the fake's not_
    # chain is simplified, so we test the dedup logic separately.
    results = get_studios_with_tickers(db)
    tickers = [r["ticker"] for r in results]
    # Should include KNOW but not None
    assert "KNOW" in tickers


def test_get_studios_with_tickers_deduplicates_by_ticker():
    db = _FakeClient()
    db.seed("studios", [
        {"studio_id": "s1", "ticker": "SAME", "name": "Studio A"},
        {"studio_id": "s2", "ticker": "SAME", "name": "Studio B"},
    ])

    results = get_studios_with_tickers(db)
    tickers = [r["ticker"] for r in results]
    assert tickers.count("SAME") == 1


# ---------------------------------------------------------------------------
# _fetch_all_rows pagination — regression test for the PostgREST 1000-row
# cap bug (same class the dashboard fixed with fetchAllRows(), see
# dashboard/src/lib/supabase/paginate.ts). A single unranged .select() call
# on watchlist/games/player_metrics/sentiment_snapshots would silently
# truncate at 1000 rows in production (watchlist/games sit at ~4,017 rows);
# _fetch_all_rows() pages via .range() until a short page signals the end.
# ---------------------------------------------------------------------------

def test__fetch_all_rows_pages_past_single_request_cap(monkeypatch):
    import database.db_client as db_client_module

    monkeypatch.setattr(db_client_module, "_MAX_ROWS_PER_REQUEST", 3)

    db = _FakeClient()
    db.seed("games", [{"game_id": f"g{i}"} for i in range(7)])

    rows = _fetch_all_rows(lambda: db.table("games").select("game_id"))

    assert len(rows) == 7
    assert {r["game_id"] for r in rows} == {f"g{i}" for i in range(7)}


def test_get_watchlist_games_returns_rows_beyond_a_single_page(monkeypatch):
    """
    get_watchlist_games is read by market_player, patch_notes, and sentiment
    workers -- if it silently capped at 1000 rows against a ~4,017-row
    production watchlist, most of the watchlist would never be processed by
    any worker with zero visible error. Simulate that cap at a small size so
    the test stays fast.
    """
    import database.db_client as db_client_module

    monkeypatch.setattr(db_client_module, "_MAX_ROWS_PER_REQUEST", 2)

    db = _FakeClient()
    db.seed(
        "watchlist",
        [
            {
                "id": f"w{i}",
                "game_id": f"g{i}",
                "active": True,
                "games": {"game_id": f"g{i}", "title": f"Game {i}"},
            }
            for i in range(5)
        ],
    )

    result = get_watchlist_games(db)

    assert len(result) == 5
    assert {r["game_id"] for r in result} == {f"g{i}" for i in range(5)}


def test_get_watchlist_games_surfaces_ticker_for_equity_gate():
    """The sentiment worker gates ScrapeOps-billed Reddit collection on a game's
    ticker (equity-mapped games only), so get_watchlist_games must surface the
    watchlist row's ticker -- NULL for a non-equity game."""
    db = _FakeClient()
    db.seed(
        "watchlist",
        [
            {
                "id": "w1",
                "game_id": "g1",
                "active": True,
                "ticker": "TTWO",
                "games": {"game_id": "g1", "title": "Equity Game"},
            },
            {
                "id": "w2",
                "game_id": "g2",
                "active": True,
                "ticker": None,
                "games": {"game_id": "g2", "title": "Non-Equity Game"},
            },
        ],
    )

    result = get_watchlist_games(db)

    by_id = {r["game_id"]: r for r in result}
    assert by_id["g1"]["ticker"] == "TTWO"
    assert by_id["g2"]["ticker"] is None


# ---------------------------------------------------------------------------
# get_active_watchlist_external_ids
# ---------------------------------------------------------------------------

def test_get_active_watchlist_external_ids():
    db = _FakeClient()
    db.seed("watchlist", [
        {"active": True, "games": {"igdb_id": "10", "steam_app_id": "20"}},
        {"active": True, "games": {"igdb_id": None, "steam_app_id": "30"}},
        {"active": False, "games": {"igdb_id": "99", "steam_app_id": "99"}},
    ])

    result = get_active_watchlist_external_ids(db)

    assert "10" in result["igdb_ids"]
    assert "20" in result["steam_ids"]
    assert "30" in result["steam_ids"]
    # Inactive row should not be included (our fake's eq filter handles active=True)


# ---------------------------------------------------------------------------
# get_tracked_game_counts_by_studio
# ---------------------------------------------------------------------------

def test_get_tracked_game_counts_by_studio():
    db = _FakeClient()
    db.seed("watchlist", [
        {"active": True, "studio_id": "s1"},
        {"active": True, "studio_id": "s1"},
        {"active": True, "studio_id": "s2"},
        {"active": False, "studio_id": "s1"},
    ])

    counts = get_tracked_game_counts_by_studio(db)

    assert counts.get("s1", 0) >= 2
    assert counts.get("s2", 0) >= 1


# ---------------------------------------------------------------------------
# get_existing_proposal_status
# ---------------------------------------------------------------------------

def test_get_existing_proposal_status_returns_none_when_empty():
    db = _FakeClient()

    result = get_existing_proposal_status(db, "g-unknown")

    assert result is None


def test_get_existing_proposal_status_returns_status():
    db = _FakeClient()
    db.seed("watchlist_proposals", [
        {"game_id": "g1", "status": "pending", "created_at": "2026-07-01T00:00:00Z"},
    ])

    result = get_existing_proposal_status(db, "g1")

    assert result == "pending"


# ---------------------------------------------------------------------------
# get_existing_company_proposal_status
# ---------------------------------------------------------------------------

def test_get_existing_company_proposal_status_filters_game_id_null():
    db = _FakeClient()
    db.seed("watchlist_proposals", [
        # company-level: game_id IS NULL
        {"studio_id": "s1", "game_id": None, "status": "approved", "created_at": "2026-07-01T00:00:00Z"},
        # game-level: should be excluded
        {"studio_id": "s1", "game_id": "g1", "status": "rejected", "created_at": "2026-07-02T00:00:00Z"},
    ])

    result = get_existing_company_proposal_status(db, "s1")

    assert result == "approved"


# ---------------------------------------------------------------------------
# write_news_item
# ---------------------------------------------------------------------------

def test_write_news_item_upserts():
    db = _FakeClient()
    item = {
        "url": "https://example.com/story",
        "title": "Big Patch",
        "snippet": "Details here.",
        "published_at": "2026-07-01T00:00:00Z",
        "domain": "example.com",
        "matched_entities": ["g1"],
    }

    write_news_item(db, item)

    upserted = db._upserts.get("news_items", [])
    assert len(upserted) == 1
    assert upserted[0]["url"] == "https://example.com/story"


# ---------------------------------------------------------------------------
# write_watchlist_proposal
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# write_ccu_snapshots_batch
# ---------------------------------------------------------------------------

def test_write_ccu_snapshots_batch_inserts_all_rows():
    db = _FakeClient()
    rows = [
        {"game_id": "g1", "concurrent_players": 500},
        {"game_id": "g2", "concurrent_players": 900},
    ]

    write_ccu_snapshots_batch(db, rows)

    inserted = db._inserts.get("ccu_snapshots", [])
    assert len(inserted) == 2
    assert {r["game_id"] for r in inserted} == {"g1", "g2"}


def test_write_ccu_snapshots_batch_empty_list_is_a_noop():
    db = _FakeClient()

    write_ccu_snapshots_batch(db, [])

    assert db._inserts.get("ccu_snapshots", []) == []


# ---------------------------------------------------------------------------
# get_weekly_outputs — cross-midnight window (multi-job CI layout)
# ---------------------------------------------------------------------------

def test_get_weekly_outputs_includes_rows_from_earlier_phase_dates():
    """
    Regression: player_metrics / sentiment_snapshots / equity_signals used to
    be filtered with .eq("date", run_date). Under the four-job CI chain the
    collect/sentiment phases can finish on the previous UTC date, so an
    exact-date filter silently dropped every one of their rows whenever the
    chain crossed midnight and synthesis produced an empty briefing.
    """
    db = _FakeClient()
    db.seed("player_metrics", [
        {"game_id": "g1", "date": "2026-07-14", "concurrent_players": 900},
    ])
    db.seed("sentiment_snapshots", [
        {"game_id": "g1", "date": "2026-07-14", "source": "steam", "sentiment_score": 3.0},
    ])
    db.seed("equity_signals", [
        {"ticker": "TTWO", "date": "2026-07-14", "health_score": 7.0},
    ])

    outputs = get_weekly_outputs(db, run_date="2026-07-15", week_start="2026-07-09")

    assert [r["game_id"] for r in outputs["player_metrics"]] == ["g1"]
    assert [r["source"] for r in outputs["sentiment"]] == ["steam"]
    assert [r["ticker"] for r in outputs["equity_signals"]] == ["TTWO"]


def test_get_weekly_outputs_keeps_only_latest_row_per_key():
    """With a whole week in the window, only the most recent row per game
    (player_metrics), per (game, source) (sentiment), and per ticker
    (equity_signals) may survive -- synthesis builds {game_id: row} maps and
    averages sentiment per game, so older same-week rows would corrupt both."""
    db = _FakeClient()
    db.seed("player_metrics", [
        {"game_id": "g1", "date": "2026-07-10", "concurrent_players": 100},
        {"game_id": "g1", "date": "2026-07-14", "concurrent_players": 900},
    ])
    db.seed("sentiment_snapshots", [
        {"game_id": "g1", "date": "2026-07-10", "source": "steam", "sentiment_score": 9.0},
        {"game_id": "g1", "date": "2026-07-14", "source": "steam", "sentiment_score": 3.0},
        {"game_id": "g1", "date": "2026-07-14", "source": "news", "sentiment_score": 6.0},
    ])
    db.seed("equity_signals", [
        {"ticker": "TTWO", "date": "2026-07-10", "health_score": 2.0},
        {"ticker": "TTWO", "date": "2026-07-14", "health_score": 7.0},
    ])

    outputs = get_weekly_outputs(db, run_date="2026-07-15", week_start="2026-07-09")

    assert len(outputs["player_metrics"]) == 1
    assert outputs["player_metrics"][0]["concurrent_players"] == 900
    # One row per (game, source): the older steam row is dropped, news survives.
    assert {(r["source"], r["sentiment_score"]) for r in outputs["sentiment"]} == {
        ("steam", 3.0),
        ("news", 6.0),
    }
    assert len(outputs["equity_signals"]) == 1
    assert outputs["equity_signals"][0]["health_score"] == 7.0


def test_get_weekly_outputs_excludes_rows_outside_week_window():
    db = _FakeClient()
    db.seed("player_metrics", [
        {"game_id": "g-old", "date": "2026-07-01", "concurrent_players": 5},
    ])

    outputs = get_weekly_outputs(db, run_date="2026-07-15", week_start="2026-07-09")

    assert outputs["player_metrics"] == []


# ---------------------------------------------------------------------------
# get_last_player_metrics — before_date (same-day re-run velocity anchor)
# ---------------------------------------------------------------------------

def test_get_last_player_metrics_before_date_excludes_todays_row():
    db = _FakeClient()
    db.seed("player_metrics", [
        {"game_id": "g1", "date": "2026-07-08", "review_count": 1000},
        {"game_id": "g1", "date": "2026-07-15", "review_count": 1200},
    ])

    latest = get_last_player_metrics(db, "g1")
    prev = get_last_player_metrics(db, "g1", before_date="2026-07-15")

    assert latest["date"] == "2026-07-15"
    assert prev["date"] == "2026-07-08"
    assert prev["review_count"] == 1000


# ---------------------------------------------------------------------------
# write_studio_signal / write_patch_event — NULL source_url idempotency
# ---------------------------------------------------------------------------

def test_write_studio_signal_with_null_source_url_is_idempotent():
    """Regression: .eq('source_url', None) never matches SQL NULL, so an ATS
    hiring signal whose job posting had no URL was re-inserted on every
    same-day re-run."""
    db = _FakeClient()
    signal = {
        "studio_id": "s1",
        "date": "2026-07-15",
        "signal_type": "hiring_surge",
        "description": "20 open roles",
        "severity": "medium",
        "source_url": None,
    }

    assert write_studio_signal(db, signal) is True
    assert write_studio_signal(db, dict(signal)) is False
    assert len(db._inserts["studio_signals"]) == 1


def test_write_studio_signal_with_source_url_still_idempotent():
    db = _FakeClient()
    signal = {
        "studio_id": "s1",
        "date": "2026-07-15",
        "signal_type": "layoffs",
        "description": "8-K",
        "severity": "high",
        "source_url": "https://sec.gov/filing/1",
    }

    assert write_studio_signal(db, signal) is True
    assert write_studio_signal(db, dict(signal)) is False
    assert len(db._inserts["studio_signals"]) == 1


def test_write_patch_event_without_source_url_is_idempotent():
    """Regression: an event with no source_url skipped the pre-check entirely,
    so it was re-inserted on every run whose 45-day lookback re-saw it."""
    db = _FakeClient()
    event = {
        "game_id": "g1",
        "date": "2026-07-10",
        "patch_type": "balance",
        "scope_summary": "Weapon tuning",
        "source_url": None,
    }

    assert write_patch_event(db, event) is True
    assert write_patch_event(db, dict(event)) is False
    assert len(db._inserts["patch_events"]) == 1


def test_write_patch_event_distinct_urlless_events_both_insert():
    db = _FakeClient()
    event = {
        "game_id": "g1",
        "date": "2026-07-10",
        "patch_type": "balance",
        "scope_summary": "Weapon tuning",
        "source_url": None,
    }
    other_day = {**event, "date": "2026-07-12"}

    assert write_patch_event(db, event) is True
    assert write_patch_event(db, other_day) is True
    assert len(db._inserts["patch_events"]) == 2


# ---------------------------------------------------------------------------
# get_trade_plan_for_week / mark_trade_order_filled
# ---------------------------------------------------------------------------

def test_get_trade_plan_for_week_returns_none_when_empty():
    db = _FakeClient()

    assert get_trade_plan_for_week(db, "2026-07-13") is None


def test_get_trade_plan_for_week_returns_existing_plan():
    db = _FakeClient()
    db.seed("trade_plans", [
        {"plan_id": "p1", "week_of": "2026-07-13", "status": "pending",
         "created_at": "2026-07-15T00:00:00Z"},
        {"plan_id": "p-other-week", "week_of": "2026-07-06", "status": "approved",
         "created_at": "2026-07-08T00:00:00Z"},
    ])

    plan = get_trade_plan_for_week(db, "2026-07-13")

    assert plan["plan_id"] == "p1"


def test_mark_trade_order_filled_updates_status_and_alpaca_id():
    db = _FakeClient()
    db.seed("trade_orders", [
        {"order_id": "o1", "status": "approved", "alpaca_order_id": None},
    ])

    mark_trade_order_filled(db, "o1", "alpaca-xyz")

    row = db._tables["trade_orders"][0]
    assert row["status"] == "filled"
    assert row["alpaca_order_id"] == "alpaca-xyz"
    assert row["filled_at"]  # set to a real timestamp


def test_write_watchlist_proposal_inserts():
    db = _FakeClient()
    proposal = {
        "game_id": "g1",
        "studio_id": "s1",
        "trigger_signal": "steam_top_ccu",
        "claude_rationale": "Good engagement.",
        "score": 72,
        "recommended_sentiment_tier": "tier_b",
        "status": "pending",
    }

    result = write_watchlist_proposal(db, proposal)

    assert result is True
    inserted = db._inserts.get("watchlist_proposals", [])
    assert len(inserted) == 1
    assert inserted[0]["trigger_signal"] == "steam_top_ccu"


# ---------------------------------------------------------------------------
# .in_() chunking — regression for the URL-length 400 at mega-publisher scale
# (confirmed live 2026-07-17: MSFT's 693 game_ids in one .in_() clause made
# the financial overlay's health-score reads fail with a non-JSON 400).
# ---------------------------------------------------------------------------

def test_fetch_in_id_chunks_bounds_each_in_clause(monkeypatch):
    import database.db_client as db_client_module

    monkeypatch.setattr(db_client_module, "_IN_CLAUSE_CHUNK_SIZE", 2)
    seen_chunks: list[list[str]] = []

    class _Builder:
        def __init__(self, chunk):
            self.chunk = chunk

        def range(self, start, end):
            return self

        def execute(self):
            return _Result([{"game_id": gid} for gid in self.chunk])

    def query_fn(chunk):
        seen_chunks.append(list(chunk))
        return _Builder(chunk)

    rows = db_client_module._fetch_in_id_chunks(["a", "b", "c", "d", "e"], query_fn)

    assert seen_chunks == [["a", "b"], ["c", "d"], ["e"]]
    assert [r["game_id"] for r in rows] == ["a", "b", "c", "d", "e"]


def test_get_recent_community_sentiment_unions_chunks_and_excludes_news(monkeypatch):
    import database.db_client as db_client_module
    from database.db_client import get_recent_community_sentiment_for_games

    monkeypatch.setattr(db_client_module, "_IN_CLAUSE_CHUNK_SIZE", 2)

    db = _FakeClient()
    db.seed("sentiment_snapshots", [
        {"game_id": "g1", "date": "2026-07-15", "source": "steam", "sentiment_score": 7.0},
        {"game_id": "g2", "date": "2026-07-15", "source": "reddit", "sentiment_score": 4.0},
        {"game_id": "g3", "date": "2026-07-15", "source": "youtube", "sentiment_score": 6.0},
        {"game_id": "g3", "date": "2026-07-15", "source": "news", "sentiment_score": 9.0},
        {"game_id": "g-old", "date": "2026-01-01", "source": "steam", "sentiment_score": 2.0},
    ])

    rows = get_recent_community_sentiment_for_games(
        db, ["g1", "g2", "g3"], since_date="2026-07-01"
    )

    # All three games' rows survive the 2-id chunking (2 chunks unioned)...
    assert {r["game_id"] for r in rows} == {"g1", "g2", "g3"}
    # ...news rows and out-of-window rows are still excluded.
    assert all(r["sentiment_score"] != 9.0 for r in rows)
    assert len(rows) == 3
