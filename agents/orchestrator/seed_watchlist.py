"""
One-time watchlist seeding agent.

Usage:
    python agents/orchestrator/seed_watchlist.py           # full seed
    python agents/orchestrator/seed_watchlist.py --dry-run # print plan, no DB writes
    python agents/orchestrator/seed_watchlist.py --limit 20 # cap at N games (dev/test)
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

# Ensure project root is on sys.path regardless of where the script is invoked from
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")

from agents.workers.market_player.igdb_client import (
    get_access_token,
    get_company_id,
    get_games_by_company,
    get_recent_releases,
)
from agents.workers.market_player.steam_client import (
    get_top_ccu_games,
    get_live_service_candidates,
    normalize_title,
    resolve_app_id,
)
from database.db_client import (
    get_client,
    find_or_create_studio,
    find_or_create_game,
    insert_watchlist_entry,
    get_seeded_external_ids,
)

# ---------------------------------------------------------------------------
# Studio seed list: canonical name → ticker → IGDB search name
# ---------------------------------------------------------------------------

STUDIO_SEEDS = [
    {"name": "Electronic Arts", "ticker": "EA", "parent_name": "Electronic Arts", "igdb_name": "Electronic Arts"},
    {"name": "Take-Two Interactive", "ticker": "TTWO", "parent_name": "Take-Two Interactive", "igdb_name": "Take-Two Interactive"},
    {"name": "2K Games", "ticker": "TTWO", "parent_name": "Take-Two Interactive", "igdb_name": "2K Games"},
    {"name": "Rockstar Games", "ticker": "TTWO", "parent_name": "Take-Two Interactive", "igdb_name": "Rockstar Games"},
    {"name": "Xbox Game Studios", "ticker": "MSFT", "parent_name": "Microsoft", "igdb_name": "Xbox Game Studios"},
    {"name": "Bethesda Softworks", "ticker": "MSFT", "parent_name": "Microsoft", "igdb_name": "Bethesda Softworks"},
    {"name": "Blizzard Entertainment", "ticker": "MSFT", "parent_name": "Microsoft", "igdb_name": "Blizzard Entertainment"},
    {"name": "Activision", "ticker": "MSFT", "parent_name": "Microsoft", "igdb_name": "Activision"},
    {"name": "Sony Interactive Entertainment", "ticker": "SONY", "parent_name": "Sony", "igdb_name": "Sony Interactive Entertainment"},
    {"name": "Bungie", "ticker": "SONY", "parent_name": "Sony", "igdb_name": "Bungie"},
    {"name": "Nintendo", "ticker": "NTDOY", "parent_name": "Nintendo", "igdb_name": "Nintendo"},
    {"name": "Ubisoft", "ticker": "UBSFT", "parent_name": "Ubisoft", "igdb_name": "Ubisoft"},
    {"name": "Square Enix", "ticker": "SQNNY", "parent_name": "Square Enix Holdings", "igdb_name": "Square Enix"},
    {"name": "Capcom", "ticker": "CCOEY", "parent_name": "Capcom", "igdb_name": "Capcom"},
    {"name": "Sega", "ticker": "SGAMY", "parent_name": "Sega Sammy Holdings", "igdb_name": "Sega"},
    {"name": "Bandai Namco Entertainment", "ticker": "NCBDY", "parent_name": "Bandai Namco Holdings", "igdb_name": "Bandai Namco Entertainment"},
    {"name": "FromSoftware", "ticker": "KDKWY", "parent_name": "Kadokawa", "igdb_name": "FromSoftware"},
    {"name": "Konami Digital Entertainment", "ticker": "KNMCY", "parent_name": "Konami", "igdb_name": "Konami Digital Entertainment"},
    {"name": "CD Projekt RED", "ticker": "OTGLY", "parent_name": "CD Projekt", "igdb_name": "CD Projekt RED"},
    {"name": "Riot Games", "ticker": "TCEHY", "parent_name": "Tencent", "igdb_name": "Riot Games"},
    {"name": "Valve", "ticker": None, "parent_name": None, "igdb_name": "Valve"},
    {"name": "Epic Games", "ticker": None, "parent_name": None, "igdb_name": "Epic Games"},
    {"name": "Paradox Interactive", "ticker": None, "parent_name": "Paradox Interactive", "igdb_name": "Paradox Interactive"},
    {"name": "Gearbox Software", "ticker": None, "parent_name": "Embracer Group", "igdb_name": "Gearbox Software"},
    {"name": "THQ Nordic", "ticker": None, "parent_name": "Embracer Group", "igdb_name": "THQ Nordic"},
]

# ---------------------------------------------------------------------------
# Investment relevance filter
# ---------------------------------------------------------------------------

def is_investment_relevant(game: dict) -> bool:
    if game.get("ticker"):
        return True
    if (game.get("ccu") or 0) >= 10_000:
        return True
    if game.get("is_live_service") and (game.get("ccu") or 0) >= 5_000:
        return True
    return False


# ---------------------------------------------------------------------------
# Cross-source merge helpers
# ---------------------------------------------------------------------------

def _canonical_key(game: dict) -> str:
    if game.get("igdb_id"):
        return f"igdb:{game['igdb_id']}"
    if game.get("steam_app_id"):
        return f"steam:{game['steam_app_id']}"
    return f"title:{normalize_title(game['title'])}"


def _build_title_index(games: list[dict]) -> dict[str, dict]:
    """Map normalized title → game record for fast lookup."""
    index: dict[str, dict] = {}
    for g in games:
        key = normalize_title(g["title"])
        if key and key not in index:
            index[key] = g
    return index


def merge_sources(
    igdb_games: list[dict],
    steam_games: list[dict],
) -> list[dict]:
    """
    Merge IGDB and Steam game lists.
    IGDB records are authoritative; Steam records enrich CCU and steam_app_id.
    Steam-only titles (no IGDB match) are appended as separate entries.
    """
    merged: dict[str, dict] = {}

    # Index IGDB games by canonical key
    for g in igdb_games:
        merged[_canonical_key(g)] = g

    # Build title lookup for cross-source matching
    title_index = _build_title_index(igdb_games)

    for sg in steam_games:
        norm = normalize_title(sg["title"])
        if norm in title_index:
            # Match found: enrich the IGDB record with Steam data
            existing = title_index[norm]
            key = _canonical_key(existing)
            merged[key]["steam_app_id"] = sg["steam_app_id"]
            if sg.get("ccu"):
                merged[key]["ccu"] = max(merged[key].get("ccu") or 0, sg["ccu"])
            if sg.get("is_live_service"):
                merged[key]["is_live_service"] = True
            if sg.get("studio_name") and not merged[key].get("studio_name"):
                merged[key]["studio_name"] = sg["studio_name"]
        else:
            # No IGDB match — add as Steam-only entry (dedup by steam_app_id)
            key = _canonical_key(sg)
            if key not in merged:
                merged[key] = sg

    return list(merged.values())


# ---------------------------------------------------------------------------
# Studio resolution for Steam-only games
# ---------------------------------------------------------------------------

def _build_studio_name_map(seeds: list[dict]) -> dict[str, dict]:
    """Map normalized studio name → seed record for Steam developer matching."""
    return {normalize_title(s["igdb_name"]): s for s in seeds}


def resolve_studio_for_game(game: dict, studio_name_map: dict[str, dict]) -> dict:
    """
    Return the best studio seed for a game. Falls back to an anonymous studio
    record if the developer doesn't match any known seed.
    """
    dev = game.get("studio_name") or ""
    norm = normalize_title(dev)
    if norm in studio_name_map:
        return studio_name_map[norm]
    return {"name": dev or "Unknown", "ticker": None, "parent_name": None, "igdb_name": dev}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(dry_run: bool = False, limit: int = 0) -> None:
    twitch_id = os.environ["TWITCH_CLIENT_ID"]
    twitch_secret = os.environ["TWITCH_CLIENT_SECRET"]

    # --- Supabase setup ---
    if not dry_run:
        db = get_client()
        seeded_ids = get_seeded_external_ids(db)
        n_seeded = len(seeded_ids["igdb_ids"]) + len(seeded_ids["steam_ids"])
        print(f"[db] {n_seeded} external ID(s) already seeded — will skip with 0 DB calls.")
    else:
        db = None
        seeded_ids = {"igdb_ids": set(), "steam_ids": set()}
        print("[dry-run] No DB writes will be made.")

    # --- IGDB authentication ---
    print("\n[igdb] Authenticating …")
    token = get_access_token(twitch_id, twitch_secret)
    print("[igdb] Token acquired.")

    # --- Fetch IGDB games per studio ---
    all_igdb: list[dict] = []
    studio_name_map: dict[str, dict] = _build_studio_name_map(STUDIO_SEEDS)

    print(f"\n[igdb] Fetching game catalogs for {len(STUDIO_SEEDS)} studios …")
    for seed in STUDIO_SEEDS:
        igdb_name = seed["igdb_name"]
        company_id = get_company_id(twitch_id, token, igdb_name)
        if not company_id:
            print(f"  [igdb] WARNING: company not found for '{igdb_name}' — skipping")
            continue
        games = get_games_by_company(twitch_id, token, company_id)
        for g in games:
            g["studio_name"] = seed["name"]
            g["ticker"] = seed["ticker"]
        all_igdb.extend(games)
        print(f"  {igdb_name}: {len(games)} game(s)")

    # --- Fetch IGDB recent releases (catch titles not under seeded studios) ---
    print("\n[igdb] Fetching recent releases (past 24 months) …")
    recent = get_recent_releases(twitch_id, token, days_back=730)
    print(f"  {len(recent)} recent releases fetched.")
    all_igdb.extend(recent)

    # --- Deduplicate IGDB games by igdb_id ---
    seen_igdb: set[str] = set()
    unique_igdb: list[dict] = []
    for g in all_igdb:
        if g["igdb_id"] not in seen_igdb:
            seen_igdb.add(g["igdb_id"])
            unique_igdb.append(g)
    print(f"\n[igdb] {len(unique_igdb)} unique IGDB game(s) after dedup.")

    # --- Fetch SteamSpy data ---
    print("\n[steam] Fetching top CCU games …")
    top_ccu = get_top_ccu_games(min_ccu=1_000)
    print(f"  {len(top_ccu)} top-CCU game(s).")

    print("[steam] Fetching live-service candidates …")
    live_svc = get_live_service_candidates(min_ccu=5_000)
    print(f"  {len(live_svc)} live-service candidate(s).")

    all_steam = top_ccu + live_svc

    # --- Merge ---
    print("\n[merge] Cross-referencing IGDB and Steam …")
    merged = merge_sources(unique_igdb, all_steam)
    print(f"[merge] {len(merged)} unique game(s) in working set.")

    # --- Investment relevance filter ---
    relevant = [g for g in merged if is_investment_relevant(g)]
    print(f"[filter] {len(relevant)} game(s) pass investment relevance filter.")

    if limit:
        relevant = relevant[:limit]
        print(f"[limit] Capped at {limit} game(s) for this run.")

    # --- RAWG enrichment + write ---
    studios_written = 0
    games_written = 0
    watchlist_added = 0
    skipped_seeded = 0

    print(f"\n[seed] Processing {len(relevant)} game(s) …\n")

    new_game_count = 0  # tracks new games written; used to pace reconnects

    for i, game in enumerate(relevant, 1):
        title = game["title"]

        # Early skip — zero DB calls for already-seeded games
        if (
            game.get("igdb_id") in seeded_ids["igdb_ids"]
            or game.get("steam_app_id") in seeded_ids["steam_ids"]
        ):
            skipped_seeded += 1
            continue

        # Determine studio for this game
        if game.get("ticker"):
            seed_match = next(
                (s for s in STUDIO_SEEDS if s["name"] == game.get("studio_name")), None
            )
            studio_rec = seed_match or resolve_studio_for_game(game, studio_name_map)
        else:
            studio_rec = resolve_studio_for_game(game, studio_name_map)

        print(
            f"  [{i}/{len(relevant)}] {title}"
            f" | CCU={game.get('ccu') or '?'}"
            f" | ticker={game.get('ticker') or studio_rec.get('ticker') or '—'}"
            f" | studio={studio_rec['name']}"
        )

        if dry_run:
            continue

        # Reconnect Supabase every 1 000 new games to stay under HTTP/2 stream limit
        if new_game_count > 0 and new_game_count % 1000 == 0:
            db = get_client()
            print(f"  [db] reconnected after {new_game_count} games")

        # Write studio
        studio_id = find_or_create_studio(db, studio_rec)
        studios_written += 1

        # Write game
        game_id = find_or_create_game(db, game, studio_id)

        # Write watchlist entry
        ticker = game.get("ticker") or studio_rec.get("ticker")
        added = insert_watchlist_entry(db, game_id, studio_id, ticker)
        if added:
            watchlist_added += 1
        games_written += 1
        new_game_count += 1

    # --- Summary ---
    print("\n" + "=" * 60)
    if dry_run:
        print(f"DRY-RUN complete. Would process {len(relevant)} game(s).")
    else:
        print(
            f"Seed complete.\n"
            f"  Studios upserted : {studios_written}\n"
            f"  Games upserted   : {games_written}\n"
            f"  Watchlist added  : {watchlist_added}\n"
            f"  Already seeded   : {skipped_seeded}"
        )
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed the games watchlist.")
    parser.add_argument("--dry-run", action="store_true", help="Print plan; no DB writes.")
    parser.add_argument("--limit", type=int, default=0, metavar="N", help="Cap games at N (dev/test).")
    args = parser.parse_args()
    main(dry_run=args.dry_run, limit=args.limit)
