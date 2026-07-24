"""
Recover the real steam_app_id for rows the mis-map cleanup nulled
(scripts/fix_mismapped_app_ids.py, deactivated_reason='mismapped_app_id'), then
reactivate the ones that genuinely resolve.

Those rows had a WRONG steam_app_id (they were reporting another game's CCU) and
a matching WRONG rawg_slug (both come from one bad RAWG backfill match — e.g.
"Gundam Breaker 4" carried rawg_slug 'resident-evil-4-2023'). So the corrupt
rawg_slug can't be trusted to re-derive the app_id, and re-running RAWG's fuzzy
title match would risk reproducing the same error.

Instead this resolves each title DIRECTLY against Steam's authoritative catalog
(IStoreService/GetAppList via steam_client._app_name_map — the same bulk map the
mis-map fixer used), by EXACT normalized-name match. High precision by design:
  * exact normalized-name match only (no fuzzy) — a title that doesn't match a
    real Steam game name is left inactive (it's a bundle/mobile/arcade/DLC SKU
    with no Steam base, i.e. correctly dead).
  * the matched app_id must be UNIQUE in the catalog and NOT already used by an
    active row (never recreate a shared-app_id collision).
  * skip a title that already has an active same-base-title sibling (the nulled
    row is a redundant duplicate — e.g. nulled "Grand Theft Auto V" when "GTA V
    Legacy" is active).

On --apply a recovered row gets: games.steam_app_id = <correct id>, the corrupt
games.rawg_slug cleared to NULL, and watchlist.active=true /
deactivated_reason=NULL (reversing just that row's mis-map deactivation).

Usage:
    python scripts/rebackfill_nulled_app_ids.py                # dry-run
    python scripts/rebackfill_nulled_app_ids.py --show-skips   # list skip reasons
    python scripts/rebackfill_nulled_app_ids.py --apply        # recover + reactivate
"""

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")

from database.db_client import get_client, _fetch_all_rows
from scripts.dedup_watchlist import normalize_base_title

_MISMAP_REASON = "mismapped_app_id"


def _norm(s: str) -> str:
    s = re.sub(r"['’]", "", s.lower())
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def build_name_index(name_map):
    """Reverse a catalog {appid: name} into {normalized_name: [appid, ...]}."""
    idx = defaultdict(list)
    for appid, name in name_map.items():
        if name:
            idx[_norm(name)].append(str(appid))
    return idx


def compute_recoveries(nulled, name_index, active_app_ids, active_bases):
    """Split nulled rows into (recoveries, skips).

    recoveries: [(game, appid)] — a unique catalog match, app_id free, no active
                same-base sibling.
    skips:      [(game, reason)] — 'no_catalog_match' / 'ambiguous_catalog' /
                'app_id_already_active' / 'sibling_active'.
    """
    recoveries, skips = [], []
    for g in nulled:
        norm = _norm(g["title"])
        appids = name_index.get(norm, [])
        if not appids:
            skips.append((g, "no_catalog_match"))
            continue
        if len(set(appids)) > 1:
            skips.append((g, "ambiguous_catalog"))
            continue
        appid = appids[0]
        if appid in active_app_ids:
            skips.append((g, "app_id_already_active"))
            continue
        if normalize_base_title(g["title"]) in active_bases:
            skips.append((g, "sibling_active"))
            continue
        recoveries.append((g, appid))
    return recoveries, skips


def _print_plan(recoveries, skips, name_map, show_skips):
    print("=" * 78)
    print("RECOVERIES (auto-apply with --apply): exact Steam-catalog match, app_id "
          "free, no active sibling")
    print("=" * 78)
    print(f"{len(recoveries)} row(s) to re-point + reactivate.\n")
    for g, appid in sorted(recoveries, key=lambda x: x[0]["title"]):
        print(f"    {g['title']}  ->  app {appid}  ({name_map.get(appid)!r})")
    print()

    by_reason = defaultdict(list)
    for g, reason in skips:
        by_reason[reason].append(g)
    print("-" * 78)
    print(f"SKIPPED ({len(skips)} row(s) left inactive)")
    print("-" * 78)
    for reason, rows in sorted(by_reason.items(), key=lambda x: -len(x[1])):
        print(f"  {reason}: {len(rows)}")
        if show_skips:
            for g in sorted(rows, key=lambda x: x["title"]):
                print(f"      {g['title']}")
    print()


def _apply(client, recoveries, wl_by_game):
    repointed = reactivated = 0
    for g, appid in recoveries:
        client.table("games").update(
            {"steam_app_id": appid, "rawg_slug": None}
        ).eq("game_id", g["game_id"]).execute()
        repointed += 1
        for w in wl_by_game.get(g["game_id"], []):
            client.table("watchlist").update(
                {"active": True, "deactivated_reason": None}
            ).eq("id", w["id"]).execute()
            reactivated += 1
    return repointed, reactivated


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Re-point steam_app_id + reactivate the recoveries.")
    parser.add_argument("--show-skips", action="store_true",
                        help="List every skipped title under its reason.")
    args = parser.parse_args(argv)

    client = get_client()
    games = _fetch_all_rows(lambda: client.table("games").select(
        "game_id, title, steam_app_id, rawg_slug"))
    watchlist = _fetch_all_rows(lambda: client.table("watchlist").select(
        "id, game_id, active, deactivated_reason"))

    gid = {g["game_id"]: g for g in games}
    nulled = [gid[w["game_id"]] for w in watchlist
              if w.get("deactivated_reason") == _MISMAP_REASON and w["game_id"] in gid]

    active_app_ids = {str(gid[w["game_id"]]["steam_app_id"])
                      for w in watchlist if w.get("active")
                      and gid.get(w["game_id"], {}).get("steam_app_id")}
    active_bases = {normalize_base_title(gid[w["game_id"]]["title"])
                    for w in watchlist if w.get("active") and w["game_id"] in gid}

    print(f"Nulled (mismapped_app_id) rows: {len(nulled)}. "
          f"Building Steam catalog name index...")
    from agents.workers.market_player.steam_client import _app_name_map
    name_map = {str(k): v for k, v in _app_name_map().items()}
    name_index = build_name_index(name_map)
    print(f"Catalog: {len(name_map)} apps, {len(name_index)} distinct names.\n")

    recoveries, skips = compute_recoveries(nulled, name_index, active_app_ids, active_bases)
    _print_plan(recoveries, skips, name_map, args.show_skips)

    if not args.apply:
        print("DRY-RUN — no writes. Re-run with --apply to recover + reactivate.")
        return

    wl_by_game = defaultdict(list)
    for w in watchlist:
        wl_by_game[w["game_id"]].append(w)
    print("APPLYING recoveries...")
    repointed, reactivated = _apply(client, recoveries, wl_by_game)
    print(f"Done: re-pointed {repointed} game(s) to correct app_id, "
          f"reactivated {reactivated} watchlist row(s).")


if __name__ == "__main__":
    main()
