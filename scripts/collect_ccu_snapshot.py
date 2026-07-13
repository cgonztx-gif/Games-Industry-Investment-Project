"""
Hourly CCU Snapshot Collector.

Steam's official API only ever returns the *current* concurrent-player
value for an app (no history), so higher-resolution CCU data requires the
platform to poll on its own schedule. This script is that poll: for every
active watchlist game with a steam_app_id, fetch the current player count
and write one timestamped row into ccu_snapshots.

Deliberately standalone -- not routed through agents/workers/market_player's
full worker.run() (which also fetches review summaries and the 24h-peak
chart) or run_weekly.py, so hourly cadence stays cheap and independent of
the weekly pipeline. Triggered by .github/workflows/ccu_hourly.yml.

Usage:
    python scripts/collect_ccu_snapshot.py
"""

import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

from agents.workers.market_player.steam_client import get_current_players
from database.db_client import get_client, get_watchlist_games, write_ccu_snapshots_batch

_REQUEST_DELAY = 0.3


def run(
    db=None,
    get_watchlist_games_fn=get_watchlist_games,
    get_current_players_fn=get_current_players,
    write_fn=write_ccu_snapshots_batch,
    sleep_fn=time.sleep,
) -> dict:
    """
    Fetch current CCU for every watchlist game with a Steam ID and batch-write
    one ccu_snapshots row each.

    Injectable ``db`` / ``get_watchlist_games_fn`` / ``get_current_players_fn`` /
    ``write_fn`` / ``sleep_fn`` (defaults are the real Supabase client and
    Steam client) let tests exercise the per-game loop and error isolation
    with no live network/DB calls.
    """
    db = db or get_client()

    all_games = get_watchlist_games_fn(db)
    steam_games = [g for g in all_games if g.get("steam_app_id")]
    skipped = len(all_games) - len(steam_games)

    print(f"[ccu_snapshot] {len(steam_games)} games with Steam IDs | {skipped} skipped (no Steam ID)")

    rows: list[dict] = []
    errors: list[dict] = []

    for i, game in enumerate(steam_games, 1):
        steam_id = game["steam_app_id"]
        try:
            ccu = get_current_players_fn(steam_id)
            rows.append({"game_id": game["game_id"], "concurrent_players": ccu})
        except Exception as exc:
            errors.append({"title": game.get("title", "unknown"), "steam_app_id": steam_id, "error": str(exc)})

        sleep_fn(_REQUEST_DELAY)

        if i % 50 == 0:
            print(f"  [{i}/{len(steam_games)}] processed …")

    write_fn(db, rows)

    print(f"[ccu_snapshot] Complete — {len(rows)} written, {len(errors)} errors.")

    return {
        "games_processed": len(rows),
        "games_skipped_no_steam_id": skipped,
        "error_count": len(errors),
        "errors": errors,
    }


if __name__ == "__main__":
    run()
