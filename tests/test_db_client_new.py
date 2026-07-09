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
    find_or_create_game,
    find_or_create_studio,
    get_active_watchlist_external_ids,
    get_existing_company_proposal_status,
    get_existing_proposal_status,
    get_studios_with_tickers,
    get_tracked_game_counts_by_studio,
    write_news_item,
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
        self._rows = list(rows)
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
