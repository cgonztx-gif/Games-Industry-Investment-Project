import json
import os
from datetime import datetime, timezone
from typing import Optional

from supabase import create_client, Client


def get_client() -> Client:
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


def find_or_create_studio(client: Client, studio: dict) -> str:
    """Return studio_id, creating the row if it doesn't exist yet."""
    resp = (
        client.table("studios")
        .select("studio_id")
        .eq("name", studio["name"])
        .execute()
    )
    if resp.data:
        return resp.data[0]["studio_id"]

    resp = (
        client.table("studios")
        .insert(
            {
                "name": studio["name"],
                "ticker": studio.get("ticker"),
                "parent_name": studio.get("parent_name"),
            }
        )
        .execute()
    )
    return resp.data[0]["studio_id"]


def find_or_create_game(client: Client, game: dict, studio_id: str) -> str:
    """Return game_id, creating (or enriching) the row as needed."""
    existing_id: Optional[str] = None

    if game.get("igdb_id"):
        resp = (
            client.table("games")
            .select("game_id")
            .eq("igdb_id", game["igdb_id"])
            .execute()
        )
        if resp.data:
            existing_id = resp.data[0]["game_id"]

    if not existing_id and game.get("steam_app_id"):
        resp = (
            client.table("games")
            .select("game_id")
            .eq("steam_app_id", game["steam_app_id"])
            .execute()
        )
        if resp.data:
            existing_id = resp.data[0]["game_id"]

    if existing_id:
        # Patch in any identifiers we now know that weren't there before
        patch: dict = {}
        if game.get("steam_app_id"):
            patch["steam_app_id"] = game["steam_app_id"]
        if game.get("rawg_slug"):
            patch["rawg_slug"] = game["rawg_slug"]
        if patch:
            client.table("games").update(patch).eq("game_id", existing_id).execute()
        return existing_id

    resp = (
        client.table("games")
        .insert(
            {
                "title": game["title"],
                "studio_id": studio_id,
                "genre": ", ".join(game.get("genres") or []) or None,
                "release_date": game.get("release_date"),
                "is_live_service": game.get("is_live_service", False),
                "steam_app_id": game.get("steam_app_id"),
                "igdb_id": game.get("igdb_id"),
                "rawg_slug": game.get("rawg_slug"),
            }
        )
        .execute()
    )
    return resp.data[0]["game_id"]


def insert_watchlist_entry(
    client: Client,
    game_id: str,
    studio_id: str,
    ticker: Optional[str],
    sentiment_tier: str = "listing_only",
) -> bool:
    """Insert watchlist entry. Returns True if inserted, False if already existed."""
    existing = (
        client.table("watchlist")
        .select("id")
        .eq("game_id", game_id)
        .eq("added_by", "seed")
        .execute()
    )
    if existing.data:
        return False

    client.table("watchlist").insert(
        {
            "game_id": game_id,
            "studio_id": studio_id,
            "ticker": ticker,
            "active": True,
            "added_by": "seed",
            "sentiment_tier": sentiment_tier,
        }
    ).execute()
    return True


def get_seeded_game_ids(client: Client) -> set[str]:
    """Return game_ids already in watchlist with added_by='seed' (idempotency check)."""
    resp = (
        client.table("watchlist")
        .select("game_id")
        .eq("added_by", "seed")
        .execute()
    )
    return {row["game_id"] for row in resp.data}


def get_seeded_external_ids(client: Client) -> dict[str, set[str]]:
    """
    Return pre-loaded sets of igdb_ids and steam_app_ids already in the watchlist.
    Used for O(1) early-skip in the seed loop — avoids any Supabase calls for
    games we've already processed, preventing HTTP/2 stream exhaustion on re-runs.
    """
    resp = (
        client.table("watchlist")
        .select("games(igdb_id, steam_app_id)")
        .eq("added_by", "seed")
        .execute()
    )
    igdb_ids: set[str] = set()
    steam_ids: set[str] = set()
    for row in resp.data:
        game = row.get("games") or {}
        if game.get("igdb_id"):
            igdb_ids.add(game["igdb_id"])
        if game.get("steam_app_id"):
            steam_ids.add(game["steam_app_id"])
    return {"igdb_ids": igdb_ids, "steam_ids": steam_ids}


# ---------------------------------------------------------------------------
# Market & Player Data worker helpers
# ---------------------------------------------------------------------------

def get_watchlist_games(client: Client) -> list[dict]:
    """Return all active watchlist games joined with their games row."""
    resp = (
        client.table("watchlist")
        .select(
            "id, game_id, sentiment_tier, subreddit, "
            "games(game_id, title, steam_app_id, igdb_id, genre, release_date, is_live_service)"
        )
        .eq("active", True)
        .execute()
    )
    result = []
    for row in resp.data:
        game = row.get("games")
        if game:
            result.append(
                {
                    **game,
                    "watchlist_id": row["id"],
                    "watchlist_game_id": row["game_id"],
                    "sentiment_tier": row.get("sentiment_tier") or "listing_only",
                    "subreddit": row.get("subreddit"),
                }
            )
    return result


def update_watchlist_subreddit(
    client: Client,
    watchlist_id: str,
    subreddit: Optional[str],
) -> None:
    """Persist subreddit resolution, including confirmed misses."""
    client.table("watchlist").update(
        {
            "subreddit": subreddit or "",
            "subreddit_resolved_at": datetime.now(timezone.utc).isoformat(),
        }
    ).eq("id", watchlist_id).execute()


def get_last_player_metrics(client: Client, game_id: str) -> Optional[dict]:
    """Most recent player_metrics row for a game (used for review_velocity calc)."""
    resp = (
        client.table("player_metrics")
        .select("review_count, date")
        .eq("game_id", game_id)
        .order("date", desc=True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def write_player_metrics(client: Client, metrics: dict) -> None:
    """Upsert one player_metrics row. Safe to call multiple times per (game_id, date)."""
    client.table("player_metrics").upsert(metrics, on_conflict="game_id,date").execute()


# ---------------------------------------------------------------------------
# Financial Overlay worker helpers
# ---------------------------------------------------------------------------

def get_watchlist_tickers(client: Client) -> list[dict]:
    """
    Return one row per ticker with simple materiality context.

    The representative studio_id is the studio with the most active tracked games
    for that ticker, not whichever studio happens to appear first. ``game_ids``
    and ``studio_ids`` (the full set, not just the representative) are included
    so callers can pull per-game/per-studio signals for a composite health score
    without a second round trip.
    """
    resp = (
        client.table("watchlist")
        .select("ticker, studio_id, game_id")
        .not_.is_("ticker", "null")
        .eq("active", True)
        .execute()
    )
    grouped: dict[str, dict] = {}
    for row in resp.data:
        t = row["ticker"]
        studio_id = row.get("studio_id")
        game_id = row.get("game_id")
        entry = grouped.setdefault(
            t, {"studio_counts": {}, "tracked_games": 0, "game_ids": [], "studio_ids": set()}
        )
        entry["tracked_games"] += 1
        if game_id:
            entry["game_ids"].append(game_id)
        if studio_id:
            entry["studio_counts"][studio_id] = entry["studio_counts"].get(studio_id, 0) + 1
            entry["studio_ids"].add(studio_id)

    result = []
    for ticker, item in grouped.items():
        studio_counts = item["studio_counts"]
        representative = None
        if studio_counts:
            representative = max(studio_counts.items(), key=lambda kv: kv[1])[0]
        result.append(
            {
                "ticker": ticker,
                "studio_id": representative,
                "tracked_games": item["tracked_games"],
                "mapped_studios": len(studio_counts),
                "game_ids": item["game_ids"],
                "studio_ids": sorted(item["studio_ids"]),
            }
        )
    return result


def write_equity_metrics(client: Client, metrics: dict) -> None:
    """Upsert one equity_signals row. Safe to call multiple times per (ticker, date)."""
    client.table("equity_signals").upsert(metrics, on_conflict="ticker,date").execute()


def get_recent_player_metrics_for_games(
    client: Client, game_ids: list[str], since_date: str
) -> list[dict]:
    """player_metrics rows for these games on/after since_date, for health-score momentum."""
    if not game_ids:
        return []
    resp = (
        client.table("player_metrics")
        .select("game_id, date, concurrent_players")
        .in_("game_id", game_ids)
        .gte("date", since_date)
        .execute()
    )
    return resp.data or []


def get_recent_community_sentiment_for_games(
    client: Client, game_ids: list[str], since_date: str
) -> list[dict]:
    """
    Community (non-news) sentiment_snapshots rows for these games on/after
    since_date, for health-score input. Excludes source='news' for the same
    reason agents/synthesis/agent.py::_sentiment_by_game() does -- media
    framing measures a different axis than community sentiment.
    """
    if not game_ids:
        return []
    resp = (
        client.table("sentiment_snapshots")
        .select("game_id, date, sentiment_score")
        .in_("game_id", game_ids)
        .neq("source", "news")
        .gte("date", since_date)
        .execute()
    )
    return resp.data or []


def get_recent_patch_events_for_games(
    client: Client, game_ids: list[str], since_date: str
) -> list[dict]:
    """patch_events rows for these games on/after since_date, for cadence-status input."""
    if not game_ids:
        return []
    resp = (
        client.table("patch_events")
        .select("game_id, date, cadence_status")
        .in_("game_id", game_ids)
        .gte("date", since_date)
        .execute()
    )
    return resp.data or []


def get_recent_studio_signals_for_studios(
    client: Client, studio_ids: list[str], since_date: str
) -> list[dict]:
    """studio_signals rows for these studios on/after since_date, for distress input."""
    if not studio_ids:
        return []
    resp = (
        client.table("studio_signals")
        .select("studio_id, date, severity")
        .in_("studio_id", studio_ids)
        .gte("date", since_date)
        .execute()
    )
    return resp.data or []


# ---------------------------------------------------------------------------
# Studio Intel worker helpers
# ---------------------------------------------------------------------------

def get_studios_with_tickers(client: Client) -> list[dict]:
    """
    Return distinct {ticker, studio_id, name} for studios with a public ticker.
    Deduplicates by ticker so each parent company appears once.
    """
    resp = (
        client.table("studios")
        .select("studio_id, ticker, name")
        .not_.is_("ticker", "null")
        .execute()
    )
    seen: dict[str, dict] = {}
    for row in resp.data:
        t = row["ticker"]
        if t not in seen:
            seen[t] = {"ticker": t, "studio_id": row["studio_id"], "name": row["name"]}
    return list(seen.values())


# ---------------------------------------------------------------------------
# RAWG backfill helpers
# ---------------------------------------------------------------------------

_RAWG_MISSING_PAGE_SIZE = 1000


def get_games_missing_rawg(client: Client, limit: int = 0, offset: int = 0) -> list[dict]:
    """
    Return games where rawg_slug IS NULL, ordered by title. Paginated via limit/offset.

    When limit=0 (the "full run" default), this pages through the backlog in
    chunks of _RAWG_MISSING_PAGE_SIZE rather than issuing a single unranged
    query, which Supabase/PostgREST would otherwise silently cap at its
    default page size instead of returning the full backlog.
    """
    base_query = lambda: (
        client.table("games")
        .select("game_id, title, release_date, steam_app_id")
        .is_("rawg_slug", "null")
        .order("title")
    )

    if limit:
        return base_query().range(offset, offset + limit - 1).execute().data

    results: list[dict] = []
    page_offset = offset
    while True:
        rows = base_query().range(page_offset, page_offset + _RAWG_MISSING_PAGE_SIZE - 1).execute().data or []
        results.extend(rows)
        if len(rows) < _RAWG_MISSING_PAGE_SIZE:
            break
        page_offset += _RAWG_MISSING_PAGE_SIZE
    return results


def update_game_rawg_data(client: Client, game_id: str, updates: dict) -> None:
    """Patch rawg_slug, steam_app_id, metacritic, or esrb_rating onto a games row."""
    client.table("games").update(updates).eq("game_id", game_id).execute()


# ---------------------------------------------------------------------------
# News worker helpers
# ---------------------------------------------------------------------------

def get_watchlist_entities_with_aliases(client: Client) -> list[dict]:
    """
    Return all active watchlist games joined with alias/ambiguity columns and
    studio name, for news-relevance matching (agents/workers/news/entity_matcher.py).
    """
    resp = (
        client.table("watchlist")
        .select(
            "id, game_id, "
            "games(game_id, title, aliases, title_is_ambiguous, studios(name))"
        )
        .eq("active", True)
        .execute()
    )
    result = []
    for row in resp.data:
        game = row.get("games")
        if not game:
            continue
        studio = game.get("studios") or {}
        result.append(
            {
                "game_id": game["game_id"],
                "title": game["title"],
                "aliases": game.get("aliases") or [],
                "title_is_ambiguous": bool(game.get("title_is_ambiguous")),
                "studio_name": studio.get("name"),
                "watchlist_id": row["id"],
            }
        )
    return result


def write_news_item(client: Client, item: dict) -> None:
    """Upsert one news_items row, keyed by url."""
    client.table("news_items").upsert(item, on_conflict="url").execute()


def get_recent_news_items(client: Client, game_id: str, since: str) -> list[dict]:
    """Return news_items rows matched to game_id, published on/after `since`.

    matched_entities is a jsonb array column. postgrest-py's .contains() only
    JSON-encodes dict values -- any other iterable (including our list of one
    id) gets rendered as a Postgres array literal (`{game_id}`), which is
    invalid syntax for a jsonb column. Building the `cs` filter manually with
    json.dumps keeps the value as a real JSON array (`["game_id"]`).
    """
    resp = (
        client.table("news_items")
        .select("*")
        .filter("matched_entities", "cs", json.dumps([game_id]))
        .gte("published_at", since)
        .execute()
    )
    return resp.data or []


# ---------------------------------------------------------------------------
# Sentiment worker helpers
# ---------------------------------------------------------------------------

def write_sentiment_snapshot(client: Client, snapshot: dict) -> None:
    """Upsert one sentiment_snapshots row. Requires uq_sentiment_game_date_source constraint."""
    client.table("sentiment_snapshots").upsert(
        snapshot, on_conflict="game_id,date,source"
    ).execute()


def count_recent_studio_signals(
    client: Client, studio_id: str, signal_type: str, since_date: str
) -> int:
    """Count studio_signals rows of a given type for a studio since a date (for repeat-distress escalation)."""
    resp = (
        client.table("studio_signals")
        .select("id", count="exact")
        .eq("studio_id", studio_id)
        .eq("signal_type", signal_type)
        .gte("date", since_date)
        .execute()
    )
    return resp.count or 0


def write_studio_signal(client: Client, signal: dict) -> bool:
    """
    Insert one studio_signals row. Returns True if inserted, False if already exists.
    Idempotency key: studio_id + date + signal_type + source_url.
    """
    existing = (
        client.table("studio_signals")
        .select("id")
        .eq("studio_id", signal["studio_id"])
        .eq("date", signal["date"])
        .eq("signal_type", signal["signal_type"])
        .eq("source_url", signal["source_url"])
        .execute()
    )
    if existing.data:
        return False

    client.table("studio_signals").insert(signal).execute()
    return True


# ---------------------------------------------------------------------------
# Patch notes worker helpers
# ---------------------------------------------------------------------------

def get_last_patch_event(client: Client, game_id: str) -> Optional[dict]:
    """Return the most recent patch_events row for cadence calculations."""
    resp = (
        client.table("patch_events")
        .select("date")
        .eq("game_id", game_id)
        .order("date", desc=True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def write_patch_event(client: Client, event: dict) -> bool:
    """
    Insert one patch_events row. Returns True if inserted, False if already exists.
    Prefers source_url idempotency when migration 004 has been applied.
    """
    source_url = event.get("source_url")
    if source_url:
        existing = (
            client.table("patch_events")
            .select("id")
            .eq("game_id", event["game_id"])
            .eq("source_url", source_url)
            .execute()
        )
        if existing.data:
            return False

    client.table("patch_events").insert(event).execute()
    return True


# ---------------------------------------------------------------------------
# Synthesis helpers
# ---------------------------------------------------------------------------

def get_weekly_outputs(client: Client, run_date: str, week_start: str) -> dict:
    """Read same-week worker outputs for synthesis."""
    player_metrics = (
        client.table("player_metrics")
        .select("*")
        .eq("date", run_date)
        .execute()
        .data
    )
    sentiment = (
        client.table("sentiment_snapshots")
        .select("*")
        .eq("date", run_date)
        .execute()
        .data
    )
    patch_events = (
        client.table("patch_events")
        .select("*")
        .gte("date", week_start)
        .lte("date", run_date)
        .execute()
        .data
    )
    studio_signals = (
        client.table("studio_signals")
        .select("*")
        .gte("date", week_start)
        .lte("date", run_date)
        .execute()
        .data
    )
    equity_signals = (
        client.table("equity_signals")
        .select("*")
        .eq("date", run_date)
        .execute()
        .data
    )
    return {
        "player_metrics": player_metrics or [],
        "sentiment": sentiment or [],
        "patch_events": patch_events or [],
        "studio_signals": studio_signals or [],
        "equity_signals": equity_signals or [],
    }


def write_weekly_briefing(client: Client, briefing: dict) -> None:
    """Upsert one weekly briefing row."""
    client.table("weekly_briefings").upsert(
        briefing,
        on_conflict="week_of",
    ).execute()


# ---------------------------------------------------------------------------
# Portfolio / execution helpers
# ---------------------------------------------------------------------------

def get_latest_weekly_briefing(client: Client) -> Optional[dict]:
    """Return the most recent weekly_briefings row (by week_of), or None if empty."""
    resp = (
        client.table("weekly_briefings")
        .select("*")
        .order("week_of", desc=True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def write_trade_plan(client: Client, plan: dict) -> str:
    """Insert one trade_plans row (status left at its schema default 'pending'). Returns plan_id."""
    resp = client.table("trade_plans").insert(plan).execute()
    return resp.data[0]["plan_id"]


def write_trade_order(client: Client, order: dict) -> None:
    """Insert one trade_orders row. Status must be left at its schema default 'pending'."""
    client.table("trade_orders").insert(order).execute()


def get_trade_order(client: Client, order_id: str) -> Optional[dict]:
    resp = (
        client.table("trade_orders")
        .select("*")
        .eq("order_id", order_id)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def get_approved_trade_orders(client: Client) -> list[dict]:
    return (
        client.table("trade_orders")
        .select("*")
        .eq("status", "approved")
        .execute()
        .data
        or []
    )


def attach_alpaca_order_id(client: Client, order_id: str, alpaca_order_id: str) -> None:
    client.table("trade_orders").update(
        {"alpaca_order_id": alpaca_order_id}
    ).eq("order_id", order_id).execute()


_REVIEW_STATUSES = ("approved", "rejected")


def get_pending_trade_plans(client: Client) -> list[dict]:
    """Return all trade_plans rows with status='pending', most recent week_of first."""
    resp = (
        client.table("trade_plans")
        .select("*")
        .eq("status", "pending")
        .order("week_of", desc=True)
        .execute()
    )
    return resp.data or []


def get_trade_orders_for_plan(client: Client, plan_id: str) -> list[dict]:
    """Return all trade_orders rows for a given plan_id."""
    resp = (
        client.table("trade_orders")
        .select("*")
        .eq("plan_id", plan_id)
        .execute()
    )
    return resp.data or []


def set_trade_plan_status(client: Client, plan_id: str, status: str) -> None:
    """
    Update a trade_plans row's status and reviewed_at. Only 'approved'/'rejected'
    are valid -- this must never be usable to reset a plan back to 'pending'.
    """
    if status not in _REVIEW_STATUSES:
        raise ValueError(f"status must be one of {_REVIEW_STATUSES}, got {status!r}")
    client.table("trade_plans").update(
        {"status": status, "reviewed_at": datetime.now(timezone.utc).isoformat()}
    ).eq("plan_id", plan_id).execute()


def set_trade_order_status(client: Client, order_id: str, status: str) -> None:
    """
    Update a single trade_orders row's status. Only 'approved'/'rejected' are
    valid -- this must never be usable to reset an order back to 'pending'.
    """
    if status not in _REVIEW_STATUSES:
        raise ValueError(f"status must be one of {_REVIEW_STATUSES}, got {status!r}")
    client.table("trade_orders").update({"status": status}).eq(
        "order_id", order_id
    ).execute()


# ---------------------------------------------------------------------------
# Returns Tracker helpers
# ---------------------------------------------------------------------------

def get_earliest_portfolio_snapshot(client: Client) -> Optional[dict]:
    """Return the earliest portfolio_snapshots row (by date), or None if the table is empty."""
    resp = (
        client.table("portfolio_snapshots")
        .select("*")
        .order("date", desc=False)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def write_portfolio_snapshot(client: Client, snapshot: dict) -> None:
    """Upsert one portfolio_snapshots row. Safe to call multiple times per date."""
    client.table("portfolio_snapshots").upsert(snapshot, on_conflict="date").execute()


def write_current_positions(client: Client, positions: list[dict]) -> None:
    """
    Replace the entire `positions` table with the given rows.

    `positions` has no unique constraint to upsert against (see schema.sql) and
    is meant to mirror Alpaca's *current* open positions, not accumulate a
    per-run history -- portfolio_snapshots already owns the value-over-time
    view. Delete-then-insert keeps it a clean live mirror: a position fully
    closed since the last run correctly disappears instead of lingering as a
    stale row.
    """
    client.table("positions").delete().gte("as_of", "1900-01-01").execute()
    if positions:
        client.table("positions").insert(positions).execute()


# ---------------------------------------------------------------------------
# Discovery worker helpers
# ---------------------------------------------------------------------------

def get_active_watchlist_external_ids(client: Client) -> dict[str, set[str]]:
    """
    Return {igdb_ids, steam_ids} for every *active* watchlist row, regardless of
    added_by. Unlike get_seeded_external_ids (seed-only, used for seed-script
    idempotency), Discovery needs to exclude anything already actively tracked
    no matter how it got there.
    """
    resp = (
        client.table("watchlist")
        .select("games(igdb_id, steam_app_id)")
        .eq("active", True)
        .execute()
    )
    igdb_ids: set[str] = set()
    steam_ids: set[str] = set()
    for row in resp.data:
        game = row.get("games") or {}
        if game.get("igdb_id"):
            igdb_ids.add(game["igdb_id"])
        if game.get("steam_app_id"):
            steam_ids.add(game["steam_app_id"])
    return {"igdb_ids": igdb_ids, "steam_ids": steam_ids}


def get_tracked_game_counts_by_studio(client: Client) -> dict[str, int]:
    """Count of active watchlist games per studio_id, for the uniqueness score component."""
    resp = (
        client.table("watchlist")
        .select("studio_id")
        .eq("active", True)
        .execute()
    )
    counts: dict[str, int] = {}
    for row in resp.data:
        studio_id = row.get("studio_id")
        if studio_id:
            counts[studio_id] = counts.get(studio_id, 0) + 1
    return counts


def get_existing_proposal_status(client: Client, game_id: str) -> Optional[str]:
    """Most recent watchlist_proposals status for a game, or None if never proposed."""
    resp = (
        client.table("watchlist_proposals")
        .select("status")
        .eq("game_id", game_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return resp.data[0]["status"] if resp.data else None


def get_existing_company_proposal_status(client: Client, studio_id: str) -> Optional[str]:
    """
    Most recent watchlist_proposals status for a company-level (game_id IS NULL)
    EDGAR proposal tied to this studio_id, or None if never proposed.
    """
    resp = (
        client.table("watchlist_proposals")
        .select("status")
        .eq("studio_id", studio_id)
        .is_("game_id", "null")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return resp.data[0]["status"] if resp.data else None


def write_watchlist_proposal(client: Client, proposal: dict) -> bool:
    """
    Insert one watchlist_proposals row. Returns True if inserted.

    No idempotency check here -- callers (discovery/worker.py) already gate on
    get_existing_proposal_status/get_existing_company_proposal_status before
    calling this, same division of responsibility as write_studio_signal.
    """
    client.table("watchlist_proposals").insert(proposal).execute()
    return True
