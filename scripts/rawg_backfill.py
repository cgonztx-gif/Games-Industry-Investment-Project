"""
RAWG Backfill — one-time script to populate games.rawg_slug and games.steam_app_id.

For every game in the DB where rawg_slug IS NULL, this script:
  1. Searches RAWG by title (3 s sleep — free tier is 20 req/min)
  2. On a match, fetches the game's /stores endpoint to extract the Steam app ID (another 3 s sleep)
  3. Updates the games row with rawg_slug, steam_app_id (if found), metacritic, esrb_rating

The script is fully resumable: re-running it only touches rows still missing rawg_slug.

Usage:
    python scripts/rawg_backfill.py                   # full run (rawg_slug IS NULL)
    python scripts/rawg_backfill.py --dry-run         # print plan, no DB writes
    python scripts/rawg_backfill.py --limit 50        # process at most N games
    python scripts/rawg_backfill.py --offset 200      # skip first N games (resume)
    python scripts/rawg_backfill.py --limit 50 --offset 200
    python scripts/rawg_backfill.py --fix-steam       # fix games that have rawg_slug but no steam_app_id
    python scripts/rawg_backfill.py --fix-steam --limit 100
"""

import argparse
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

from agents.workers.market_player.rawg_client import search_game, get_steam_app_id
from database.db_client import get_client, get_games_missing_rawg, update_game_rawg_data


def _release_year(release_date: str | None) -> int | None:
    if release_date and len(release_date) >= 4:
        try:
            return int(release_date[:4])
        except ValueError:
            pass
    return None


def _get_games_missing_steam(client, limit: int, offset: int) -> list[dict]:
    """Return games that have rawg_slug but are still missing steam_app_id."""
    q = (
        client.table("games")
        .select("game_id, title, rawg_slug")
        .not_.is_("rawg_slug", "null")
        .is_("steam_app_id", "null")
        .order("title")
    )
    if offset:
        q = q.range(offset, offset + (limit or 10_000) - 1)
    elif limit:
        q = q.limit(limit)
    return q.execute().data


def _fix_steam_pass(api_key: str, db, limit: int, offset: int, dry_run: bool) -> None:
    """Second-pass: fill steam_app_id for games that already have rawg_slug."""
    games = _get_games_missing_steam(db if not dry_run else get_client(), limit=limit, offset=offset)
    total = len(games)
    print(f"[rawg_backfill --fix-steam] {total} game(s) have rawg_slug but no steam_app_id.")
    if dry_run:
        for g in games[:20]:
            print(f"  {g['title'][:50]}  rawg_slug={g['rawg_slug']}")
        if total > 20:
            print(f"  ... and {total - 20} more")
        return

    found = 0
    errors = 0
    for i, game in enumerate(games, 1):
        slug = game["rawg_slug"]
        title = game["title"]
        print(f"  [{i}/{total}] {title[:50]}", end="", flush=True)
        try:
            steam_app_id = get_steam_app_id(api_key, slug)
        except Exception as exc:
            print(f"  | ERROR: {exc}")
            errors += 1
            continue
        if steam_app_id:
            update_game_rawg_data(db, game["game_id"], {"steam_app_id": steam_app_id})
            print(f"  | steam_app_id={steam_app_id}")
            found += 1
        else:
            print(f"  | no steam ID")
        if i % 25 == 0:
            print(f"\n  --- progress: {i}/{total} | found={found} errors={errors} ---\n")

    print("\n" + "=" * 60)
    print(f"Fix-steam complete.\n  Processed: {total}\n  Steam IDs found: {found}\n  Errors: {errors}")
    print("=" * 60)


def main(dry_run: bool, limit: int, offset: int, fix_steam: bool) -> None:
    api_key = os.environ.get("RAWG_API_KEY", "")
    if not api_key and not dry_run:
        print("ERROR: RAWG_API_KEY not set in .env")
        sys.exit(1)

    db = None if dry_run else get_client()

    if fix_steam:
        _fix_steam_pass(api_key, db, limit=limit, offset=offset, dry_run=dry_run)
        return

    games = get_games_missing_rawg(db if not dry_run else get_client(), limit=limit, offset=offset)

    total = len(games)
    print(f"[rawg_backfill] {total} game(s) missing rawg_slug (limit={limit or 'none'}, offset={offset})")
    if dry_run:
        print("[rawg_backfill] DRY-RUN — no DB writes will be made.\n")

    matched = 0
    steam_found = 0
    no_match = 0
    errors = 0

    for i, game in enumerate(games, 1):
        game_id = game["game_id"]
        title = game["title"]
        year = _release_year(game.get("release_date"))
        has_steam = bool(game.get("steam_app_id"))

        print(f"  [{i}/{total}] {title}", end="", flush=True)

        if dry_run:
            print(f"  | year={year or '?'}  steam_app_id={'already set' if has_steam else 'missing'}")
            continue

        # --- RAWG search ---
        try:
            result = search_game(api_key, title, year)
        except Exception as exc:
            print(f"  | ERROR during search: {exc}")
            errors += 1
            continue

        if not result or not result.get("rawg_slug"):
            print("  | no match")
            no_match += 1
            continue

        slug = result["rawg_slug"]
        updates: dict = {"rawg_slug": slug}

        # --- Steam app ID via detail endpoint (skip if already set) ---
        steam_app_id: str | None = None
        if not has_steam:
            try:
                steam_app_id = get_steam_app_id(api_key, slug)
            except Exception as exc:
                print(f"  | WARNING: detail fetch failed: {exc}", end="")
            if steam_app_id:
                updates["steam_app_id"] = steam_app_id
                steam_found += 1

        update_game_rawg_data(db, game_id, updates)
        matched += 1

        steam_note = f"  steam_app_id={steam_app_id}" if steam_app_id else ("  steam already set" if has_steam else "  no steam ID")
        print(f"  | slug={slug}{steam_note}")

        if i % 25 == 0:
            pct = i / total * 100
            print(f"\n  --- progress: {i}/{total} ({pct:.0f}%) | matched={matched} no_match={no_match} errors={errors} ---\n")

    print("\n" + "=" * 60)
    if dry_run:
        print(f"DRY-RUN complete. Would process {total} game(s).")
    else:
        print(
            f"Backfill complete.\n"
            f"  Total processed  : {total}\n"
            f"  RAWG matched     : {matched}\n"
            f"  Steam IDs found  : {steam_found}\n"
            f"  No RAWG match    : {no_match}\n"
            f"  Errors           : {errors}"
        )
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill rawg_slug and steam_app_id for games table.")
    parser.add_argument("--dry-run", action="store_true", help="Print plan; no DB writes.")
    parser.add_argument("--limit", type=int, default=0, metavar="N", help="Process at most N games.")
    parser.add_argument("--offset", type=int, default=0, metavar="N", help="Skip first N games (for resuming).")
    parser.add_argument("--fix-steam", action="store_true", help="Fix games that have rawg_slug but no steam_app_id.")
    args = parser.parse_args()
    main(dry_run=args.dry_run, limit=args.limit, offset=args.offset, fix_steam=args.fix_steam)
