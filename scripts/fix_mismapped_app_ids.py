"""
Clean up shared-steam_app_id clusters — the "mis-mapping" half of the tasks.md
"Reduce watchlist/game-list bloat" work, after dedup (scripts/dedup_watchlist.py)
and the dead-row pass (scripts/deactivate_dead_games.py).

A steam_app_id maps to exactly ONE Steam app, yet the seed/RAWG-backfill era left
many app_ids shared across multiple active games. Two distinct shapes:

  1. DLC-OF-BASE (clean): a base game + its own DLC/packs, all correctly sharing
     the base's app_id — "Capcom Arcade Stadium" + 28 game packs, "Dragon Ball
     FighterZ" + character DLC. The app_id is right; the DLC just shouldn't be
     separate active rows. Fix = collapse the DLC into the base (canonical_game_id
     + soft-deactivate), same model as dedup_watchlist.py. No Steam call needed —
     a member is DLC when its title continues with a subtitle separator
     (":"/"-"/"(") after the base's full title.

  2. MIS-MAPPED (foreign): genuinely different games wrongly sharing one app_id,
     so every non-owner reports the WRONG game's CCU/reviews — "Resident Evil 4"
     + "Gundam Breaker 4"; "FF VII Remake" + "Final Fantasy Digital Card Game".
     The true owner can't be told from titles alone, so this tier queries Steam's
     storefront appdetails for the app's REAL name, keeps the member that matches
     it (collapsing that owner's DLC in), and soft-deactivates + NULLs the
     steam_app_id on every other (foreign) row so it stops pulling the wrong CCU.
     If NO member matches the real name (the app belongs to a game not in the
     cluster at all — e.g. app 1656780 is really "Hero's Hour", not the Mario
     Kart Tour rows mapped onto it), all members are deactivated + nulled.

Safety model (mirrors the other bloat scripts):
  * DRY-RUN BY DEFAULT; --apply-dlc and --apply-mismap are separate opt-ins.
  * SOFT deactivation only (watchlist.active=false + deactivated_reason). Never
    deletes a row. DLC collapse keeps the (correct) app_id and records
    canonical_game_id; the mismap tier NULLs the wrong app_id so a later
    rawg/steam backfill can re-resolve the correct one.
  * Steam names are fetched once and cached to a local JSON file, so the dry-run
    you review and the subsequent --apply-mismap act on identical data.

Usage:
    python scripts/fix_mismapped_app_ids.py                 # dry-run both tiers
    python scripts/fix_mismapped_app_ids.py --show-review   # list every cluster
    python scripts/fix_mismapped_app_ids.py --apply-dlc     # collapse clean DLC clusters
    python scripts/fix_mismapped_app_ids.py --apply-mismap  # Steam-verify + fix foreign clusters
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

_SEPARATORS = (":", "-", "–", "—", "(")
_DLC_REASON = "dlc_dedup"
_MISMAP_REASON = "mismapped_app_id"


def _norm(s: str) -> str:
    """Lowercase, strip punctuation to spaces, collapse — for title/name compare."""
    s = re.sub(r"['’]", "", s.lower())
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def is_dlc_of(owner_title: str, title: str) -> bool:
    """True if `title` is `owner_title` followed by a subtitle separator (its DLC)."""
    if title == owner_title:
        return True
    if not title.startswith(owner_title):
        return False
    rem = title[len(owner_title):].lstrip()
    return bool(rem) and rem[0] in _SEPARATORS


def _owner_by_shortest(group, covered):
    """The cluster's base-game candidate: shortest title (coverage/age tiebreak)."""
    return sorted(
        group,
        key=lambda g: (len(g["title"]), 0 if g["game_id"] in covered else 1,
                       g.get("created_at") or ""),
    )[0]


def classify_clusters(active_games, covered):
    """Split active shared-app_id (multi-row) clusters into clean vs. foreign.

    clean:   [(owner, [dlc...])]      — owner + only separator-DLC (app_id correct)
    foreign: [(app_id, [members...])] — has a member that isn't the owner's DLC
                                        (genuine mis-mapping; needs Steam verify)
    """
    by_appid = defaultdict(list)
    for g in active_games:
        if g.get("steam_app_id"):
            by_appid[str(g["steam_app_id"])].append(g)

    clean, foreign = [], []
    for app_id, group in by_appid.items():
        if len(group) < 2:
            continue
        owner = _owner_by_shortest(group, covered)
        others = [g for g in group if g["game_id"] != owner["game_id"]]
        non_dlc = [g for g in others if not is_dlc_of(owner["title"], g["title"])]
        if non_dlc:
            foreign.append((app_id, group))
        else:
            clean.append((owner, others))
    return clean, foreign


def _match_owner(members, steam_name, covered):
    """Pick the member whose title matches the Steam app's real name, or None.

    A member matches when the shorter of (member tokens, name tokens) — at least 2
    tokens — is a full token-prefix of the longer. Among matches, the member whose
    token count is CLOSEST to the Steam name's (i.e. the base game the app is
    named for, not one of its longer-titled DLC) wins.
    """
    n = _norm(steam_name).split()
    if not n:
        return None
    candidates = []
    for g in members:
        m = _norm(g["title"]).split()
        if not m:
            continue
        k = min(len(m), len(n))
        if k < 2:
            if m == n:
                candidates.append(g)
            continue
        if m[:k] == n[:k]:
            candidates.append(g)
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda g: (abs(len(_norm(g["title"]).split()) - len(n)),
                       len(g["title"]),
                       0 if g["game_id"] in covered else 1),
    )[0]


def resolve_mismap(foreign, fetch_name_fn, covered):
    """For each foreign cluster, resolve owner/dlc/foreign via the true Steam name.

    Returns (resolved, unresolved).

    resolved:   [(app_id, steam_name, owner_or_None, dlc_list, foreign_list)]
        owner:   kept active (its app_id is correct)
        dlc:     the owner's separator-DLC — collapse into owner (keep app_id)
        foreign: everything else — deactivate + NULL app_id (wrong game for this id)
        A None owner means Steam returned a real name but NO cluster member matches
        it (the app belongs to a game not in the cluster) -> every member foreign.
    unresolved: [(app_id, group)] where Steam returned no name at all (app absent
        from the catalog / delisted). SKIPPED entirely — acting on a missing name
        could wrongly deactivate a legitimate row, so these are report-only.
    """
    resolved, unresolved = [], []
    for app_id, group in foreign:
        steam_name = fetch_name_fn(app_id)
        if not steam_name:
            unresolved.append((app_id, group))
            continue
        owner = _match_owner(group, steam_name, covered)
        if owner is None:
            resolved.append((app_id, steam_name, None, [], list(group)))
            continue
        dlc, foreign_members = [], []
        for g in group:
            if g["game_id"] == owner["game_id"]:
                continue
            if is_dlc_of(owner["title"], g["title"]):
                dlc.append(g)
            else:
                foreign_members.append(g)
        resolved.append((app_id, steam_name, owner, dlc, foreign_members))
    return resolved, unresolved


# --------------------------------------------------------------------------- #
# Steam name fetch — one bulk catalog map (IStoreService/GetAppList), not
# per-app storefront appdetails (which tarpits after a short burst: ~2 lookups
# in 10 min at watchlist scale). The whole games catalog (~176k apps) builds in
# ~9s and resolves the large majority of app_ids; the rest are treated as
# unresolved (absent from the catalog / delisted) and skipped, never guessed.
# --------------------------------------------------------------------------- #

def make_catalog_name_fetcher():
    """Return fetch_name(app_id)->str|None backed by the bulk Steam catalog map."""
    from agents.workers.market_player.steam_client import _app_name_map

    name_map = _app_name_map()

    def fetch_name(app_id):
        return name_map.get(str(app_id)) or None

    return fetch_name


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

def _print_dlc(clean, show_review):
    total = sum(len(dlc) for _, dlc in clean)
    print("=" * 78)
    print("DLC-COLLAPSE TIER (auto-apply with --apply-dlc): base game + its own "
          "separator-DLC sharing the base's app_id")
    print("=" * 78)
    print(f"{len(clean)} cluster(s) -> collapse {total} DLC row(s) into their base.\n")
    for owner, dlc in sorted(clean, key=lambda x: -len(x[1]))[: None if show_review else 15]:
        print(f"  KEEP  {owner['title']}  (app {owner.get('steam_app_id')})  +{len(dlc)} DLC")
        if show_review:
            for g in dlc:
                print(f"    x-  {g['title']}")
    print()


def _print_mismap(resolved, unresolved, show_review):
    owner_found = [r for r in resolved if r[2] is not None]
    no_owner = [r for r in resolved if r[2] is None]
    n_foreign = sum(len(r[4]) for r in resolved)
    n_dlc = sum(len(r[3]) for r in resolved)
    print("=" * 78)
    print("MIS-MAP TIER (auto-apply with --apply-mismap): Steam-verified true owner; "
          "deactivate + NULL app_id on foreign rows")
    print("=" * 78)
    print(f"{len(resolved)} resolved cluster(s): {len(owner_found)} with a verified "
          f"owner, {len(no_owner)} where NO member owns the app_id. "
          f"{len(unresolved)} unresolved (no Steam name) -> SKIPPED.")
    print(f"  -> {n_foreign} foreign row(s) to deactivate+null, "
          f"{n_dlc} owner-DLC row(s) to collapse.\n")
    for app_id, name, owner, dlc, foreign in sorted(resolved, key=lambda r: -len(r[4])):
        if owner is not None:
            print(f"  app {app_id} = {name!r}")
            print(f"       KEEP  {owner['title']}  (+{len(dlc)} DLC)")
            for g in (foreign if show_review else foreign[:4]):
                print(f"       null  {g['title']}")
            if not show_review and len(foreign) > 4:
                print(f"       ... +{len(foreign) - 4} more foreign")
        else:
            print(f"  app {app_id} = {name!r}  -> NO owner in cluster; "
                  f"deactivate+null all {len(foreign)}")
            for g in (foreign if show_review else foreign[:4]):
                print(f"       null  {g['title']}")
            if not show_review and len(foreign) > 4:
                print(f"       ... +{len(foreign) - 4} more")
    if unresolved:
        print("\n  -- UNRESOLVED (no Steam name; skipped, review manually) --")
        for app_id, group in (unresolved if show_review else unresolved[:10]):
            print(f"     app {app_id}: {[g['title'] for g in group][:3]}")
        if not show_review and len(unresolved) > 10:
            print(f"     ... +{len(unresolved) - 10} more")
    print()


# --------------------------------------------------------------------------- #
# Apply
# --------------------------------------------------------------------------- #

def _deactivate(client, wl_by_game, game_id, reason):
    n = 0
    for w in wl_by_game.get(game_id, []):
        if w.get("active"):
            client.table("watchlist").update(
                {"active": False, "deactivated_reason": reason}
            ).eq("id", w["id"]).execute()
            n += 1
    return n


def apply_dlc(client, clean, watchlist):
    wl_by_game = defaultdict(list)
    for w in watchlist:
        wl_by_game[w["game_id"]].append(w)
    collapsed = 0
    for owner, dlc in clean:
        for g in dlc:
            client.table("games").update(
                {"canonical_game_id": owner["game_id"]}
            ).eq("game_id", g["game_id"]).execute()
            collapsed += _deactivate(client, wl_by_game, g["game_id"], _DLC_REASON)
    return collapsed


def apply_mismap(client, resolved, watchlist):
    wl_by_game = defaultdict(list)
    for w in watchlist:
        wl_by_game[w["game_id"]].append(w)
    nulled = collapsed = 0
    for app_id, name, owner, dlc, foreign in resolved:
        for g in dlc:
            client.table("games").update(
                {"canonical_game_id": owner["game_id"]}
            ).eq("game_id", g["game_id"]).execute()
            collapsed += _deactivate(client, wl_by_game, g["game_id"], _DLC_REASON)
        for g in foreign:
            client.table("games").update(
                {"steam_app_id": None}
            ).eq("game_id", g["game_id"]).execute()
            nulled += _deactivate(client, wl_by_game, g["game_id"], _MISMAP_REASON)
    return collapsed, nulled


def main(argv=None):
    # Steam names contain non-cp1252 glyphs (e.g. "Σ"); avoid a Windows console
    # UnicodeEncodeError killing the run mid-report.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply-dlc", action="store_true",
                        help="Collapse the clean DLC-of-base clusters.")
    parser.add_argument("--apply-mismap", action="store_true",
                        help="Steam-verify and fix the foreign (mis-mapped) clusters.")
    parser.add_argument("--show-review", action="store_true",
                        help="List every cluster/row in full.")
    args = parser.parse_args(argv)

    client = get_client()
    games = _fetch_all_rows(lambda: client.table("games").select(
        "game_id, title, steam_app_id, canonical_game_id, created_at"))
    watchlist = _fetch_all_rows(lambda: client.table("watchlist").select(
        "id, game_id, active"))
    active_ids = {w["game_id"] for w in watchlist if w.get("active")}
    active_games = [g for g in games if g["game_id"] in active_ids]
    covered = set()
    for t in ("player_metrics", "sentiment_snapshots", "patch_events"):
        covered |= {r["game_id"] for r in _fetch_all_rows(
            lambda tt=t: client.table(tt).select("game_id"))}

    print(f"Loaded {len(games)} games, {len(active_ids)} active watchlist rows.\n")

    clean, foreign = classify_clusters(active_games, covered)
    _print_dlc(clean, args.show_review)

    # The mismap tier needs the Steam catalog map; only build it when relevant.
    resolved = None
    if args.apply_mismap or not args.apply_dlc:
        print(f"Resolving {len(foreign)} foreign cluster(s) via the Steam catalog "
              f"name map...")
        fetch = make_catalog_name_fetcher()
        resolved, unresolved = resolve_mismap(foreign, fetch, covered)
        _print_mismap(resolved, unresolved, args.show_review)

    if not args.apply_dlc and not args.apply_mismap:
        print("DRY-RUN — no writes. Re-run with --apply-dlc and/or --apply-mismap.")
        return

    if args.apply_dlc:
        print("APPLYING DLC-collapse tier...")
        collapsed = apply_dlc(client, clean, watchlist)
        print(f"  dlc: collapsed {collapsed} DLC row(s) into their base.")

    if args.apply_mismap:
        print("APPLYING mis-map tier...")
        collapsed, nulled = apply_mismap(client, resolved, watchlist)
        print(f"  mismap: collapsed {collapsed} owner-DLC row(s), "
              f"deactivated+nulled {nulled} foreign row(s).")


if __name__ == "__main__":
    main()
