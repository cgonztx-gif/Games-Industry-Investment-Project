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
            "games(game_id, title, steam_app_id, igdb_id)"
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
    for that ticker, not whichever studio happens to appear first.
    """
    resp = (
        client.table("watchlist")
        .select("ticker, studio_id")
        .not_.is_("ticker", "null")
        .eq("active", True)
        .execute()
    )
    grouped: dict[str, dict] = {}
    for row in resp.data:
        t = row["ticker"]
        studio_id = row.get("studio_id")
        grouped.setdefault(t, {"studio_counts": {}, "tracked_games": 0})
        grouped[t]["tracked_games"] += 1
        if studio_id:
            counts = grouped[t]["studio_counts"]
            counts[studio_id] = counts.get(studio_id, 0) + 1

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
            }
        )
    return result


def write_equity_metrics(client: Client, metrics: dict) -> None:
    """Upsert one equity_signals row. Safe to call multiple times per (ticker, date)."""
    client.table("equity_signals").upsert(metrics, on_conflict="ticker,date").execute()


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

def get_games_missing_rawg(client: Client, limit: int = 0, offset: int = 0) -> list[dict]:
    """Return games where rawg_slug IS NULL, ordered by title. Paginated via limit/offset."""
    q = (
        client.table("games")
        .select("game_id, title, release_date, steam_app_id")
        .is_("rawg_slug", "null")
        .order("title")
    )
    if offset:
        q = q.range(offset, offset + (limit or 10_000) - 1)
    elif limit:
        q = q.limit(limit)
    return q.execute().data


def update_game_rawg_data(client: Client, game_id: str, updates: dict) -> None:
    """Patch rawg_slug, steam_app_id, metacritic, or esrb_rating onto a games row."""
    client.table("games").update(updates).eq("game_id", game_id).execute()


# ---------------------------------------------------------------------------
# Sentiment worker helpers
# ---------------------------------------------------------------------------

def write_sentiment_snapshot(client: Client, snapshot: dict) -> None:
    """Upsert one sentiment_snapshots row. Requires uq_sentiment_game_date_source constraint."""
    client.table("sentiment_snapshots").upsert(
        snapshot, on_conflict="game_id,date,source"
    ).execute()


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
