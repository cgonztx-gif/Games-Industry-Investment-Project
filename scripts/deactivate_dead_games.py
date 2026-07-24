"""
Soft-deactivate DEAD watchlist rows — active games that have produced no signal
from any worker and have no data path to ever produce one (the "remove/
deactivate dead entries" half of the tasks.md "Reduce watchlist/game-list bloat"
work; the other half, edition/DLC dedup, is scripts/dedup_watchlist.py).

A row is "dead" when its game has:
  * ZERO player_metrics rows (no Steam/RAWG CCU or review history), AND
  * ZERO sentiment_snapshots rows (no community AND no source='news' sentiment), AND
  * ZERO patch_events rows (no patch-cadence signal), AND
  * NO steam_app_id (no path to ever get Steam CCU).
Such a row is pure per-run cost (a Steam CCU attempt, a subreddit-resolution
attempt) with no signal and no way to produce one — overwhelmingly seed-era
console/DLC/kit SKUs (The Sims 4 kits, FIFA editions, Battlefield map-pack DLC).

Safety model (mirrors scripts/dedup_watchlist.py):
  * DRY-RUN BY DEFAULT — prints the plan; pass --apply-dead to write.
  * SOFT deactivation only — sets watchlist.active=false + a
    watchlist.deactivated_reason='dead_no_coverage' audit tag (migration 018).
    Never deletes a row, so historical references stay intact and a pass is
    reversible via `update watchlist set active=true where
    deactivated_reason='dead_no_coverage'`.
  * A dead row that is itself a CANONICAL for an edition cluster (some other
    row's games.canonical_game_id points to it — e.g. "World of Warcraft:
    Shadowlands", a base row that absorbed editions in the dedup pass) is NOT
    auto-deactivated. Darkening a base-game row is more consequential than
    dropping a bare edition, so these are printed for manual review instead.
  * A dead row that unexpectedly HAS a steam_app_id (should have gotten CCU —
    possibly delisted or a bad app_id) is likewise review-only, not auto.

Usage:
    python scripts/deactivate_dead_games.py                # dry-run: print the plan
    python scripts/deactivate_dead_games.py --show-review  # also list review tiers in full
    python scripts/deactivate_dead_games.py --apply-dead   # deactivate the auto tier
"""

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")

from database.db_client import get_client, _fetch_all_rows

_DEAD_REASON = "dead_no_coverage"


def _load_state(client):
    """Pull the read-only snapshot needed to compute the dead-row plan.

    Selects watchlist WITHOUT deactivated_reason so the dry-run works before
    migration 018 is applied (the column is only written, never read here).
    """
    games = _fetch_all_rows(
        lambda: client.table("games").select(
            "game_id, title, steam_app_id, canonical_game_id"
        )
    )
    watchlist = _fetch_all_rows(
        lambda: client.table("watchlist").select("id, game_id, active")
    )
    covered = set()
    for table in ("player_metrics", "sentiment_snapshots", "patch_events"):
        covered |= {
            r["game_id"]
            for r in _fetch_all_rows(lambda t=table: client.table(t).select("game_id"))
        }
    return games, watchlist, covered


def compute_dead_plan(games, watchlist, covered):
    """Return (dead_auto, review_canonical, review_has_appid).

    dead_auto:        active dead games with no steam_app_id and not a canonical
                      — safe to soft-deactivate.
    review_canonical: active dead games that ARE a canonical for some edition
                      cluster — review only (darkening a base row is bigger).
    review_has_appid: active dead games that unexpectedly have a steam_app_id
                      — review only (delisted / bad app_id anomaly).
    """
    active_game_ids = {w["game_id"] for w in watchlist if w.get("active")}
    canonical_ids = {
        g["canonical_game_id"] for g in games if g.get("canonical_game_id")
    }

    dead_auto = []
    review_canonical = []
    review_has_appid = []
    for g in games:
        if g["game_id"] not in active_game_ids:
            continue
        if g["game_id"] in covered:
            continue
        if g.get("steam_app_id"):
            review_has_appid.append(g)
        elif g["game_id"] in canonical_ids:
            review_canonical.append(g)
        else:
            dead_auto.append(g)
    return dead_auto, review_canonical, review_has_appid


def _print_plan(dead_auto, review_canonical, review_has_appid, show_review):
    print("=" * 78)
    print("DEAD AUTO TIER (auto-apply with --apply-dead): no coverage, no steam_app_id, "
          "not a canonical")
    print("=" * 78)
    print(f"{len(dead_auto)} watchlist row(s) to soft-deactivate.\n")
    sample = sorted(dead_auto, key=lambda g: g["title"])
    for g in (sample if show_review else sample[:40]):
        print(f"    x-  {g['title']}")
    if not show_review and len(sample) > 40:
        print(f"    ... and {len(sample) - 40} more (--show-review for all)")
    print()

    print("-" * 78)
    print(f"REVIEW ONLY — dead but a CANONICAL for an edition cluster "
          f"({len(review_canonical)} row(s), darkening a base row — NOT auto-applied)")
    print("-" * 78)
    for g in sorted(review_canonical, key=lambda g: g["title"]):
        print(f"    ?   {g['title']}")
    print()

    print("-" * 78)
    print(f"REVIEW ONLY — dead but HAS a steam_app_id "
          f"({len(review_has_appid)} row(s), delisted/bad-app_id anomaly — NOT auto-applied)")
    print("-" * 78)
    if show_review:
        for g in sorted(review_has_appid, key=lambda g: g["title"]):
            print(f"    ?   app={g['steam_app_id']}  {g['title']}")
    elif review_has_appid:
        print("  (re-run with --show-review to list these in full)")
    print()


def _apply(client, dead_auto, watchlist):
    """Set watchlist.active=false + deactivated_reason for each dead game."""
    wl_by_game = {}
    for w in watchlist:
        wl_by_game.setdefault(w["game_id"], []).append(w)

    deactivated = 0
    for g in dead_auto:
        for w in wl_by_game.get(g["game_id"], []):
            if w.get("active"):
                client.table("watchlist").update(
                    {"active": False, "deactivated_reason": _DEAD_REASON}
                ).eq("id", w["id"]).execute()
                deactivated += 1
    return deactivated


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply-dead",
        action="store_true",
        help="Soft-deactivate the dead auto tier (default is dry-run).",
    )
    parser.add_argument(
        "--show-review",
        action="store_true",
        help="List the full auto tier and the review tiers.",
    )
    args = parser.parse_args(argv)

    client = get_client()
    games, watchlist, covered = _load_state(client)

    active_wl = sum(1 for w in watchlist if w.get("active"))
    print(f"Loaded {len(games)} games, {active_wl} active watchlist rows, "
          f"{len(covered)} games with any worker coverage.\n")

    dead_auto, review_canonical, review_has_appid = compute_dead_plan(
        games, watchlist, covered
    )
    _print_plan(dead_auto, review_canonical, review_has_appid, args.show_review)

    if not args.apply_dead:
        print("DRY-RUN — no writes. Re-run with --apply-dead to soft-deactivate "
              "the dead auto tier above.")
        return

    print("APPLYING dead-row deactivations...")
    deactivated = _apply(client, dead_auto, watchlist)
    print(f"Done: deactivated {deactivated} watchlist row(s) "
          f"(deactivated_reason='{_DEAD_REASON}').")


if __name__ == "__main__":
    main()
