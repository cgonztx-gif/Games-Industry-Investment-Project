"""
De-duplicate watchlist edition/season/DLC variant rows down to one canonical
base-game row (the "Reduce watchlist/game-list bloat" ASAP task).

The seeded/discovered games set ingested every Steam store SKU as a distinct
games row: base game + editions + season passes + cosmetic packs. Six separate
"Destiny 2" rows (Deluxe / Launch / Collector's / ...) all share steam_app_id
1085660 and therefore report the identical CCU. This script collapses those
variants to a single canonical row and soft-deactivates the rest
(watchlist.active=false), recording the mapping in games.canonical_game_id
(migration 017) so the operation is auditable and reversible.

Safety model (mirrors scripts/rawg_backfill.py):
  * DRY-RUN BY DEFAULT — prints exactly what it would do; pass --apply to write.
  * SOFT deactivation only — sets watchlist.active=false + games.canonical_game_id.
    Never deletes a games/watchlist row, so historical player_metrics /
    sentiment_snapshots / patch_events that reference a variant's game_id stay
    intact and the change is fully reversible.
  * Only the CONSERVATIVE tier auto-applies: rows that share a steam_app_id AND
    normalize to the same base title. Two adjacent-but-riskier tiers are printed
    for human review and NEVER auto-deactivated:
      - base-title-only matches (no shared app_id) — could be a distinct
        remaster/spinoff (e.g. "Days Gone Remastered" vs "Days Gone").
      - shared app_id but DIFFERENT base title — likely a mis-mapped steam_app_id
        grouping genuinely different games (e.g. FF VII Remake / FF VIII
        Remastered both mapped to app 1462040); collapsing would deactivate a
        real distinct game.

Usage:
    python scripts/dedup_watchlist.py                # dry-run: print the plan
    python scripts/dedup_watchlist.py --show-review  # also list the review-only tiers in full
    python scripts/dedup_watchlist.py --apply        # perform the conservative-tier deactivations
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

# Trailing tokens that mark an edition / bundle / season / DLC suffix rather
# than part of the game's real name. Stripped iteratively from the END of a
# title only, so an interior number that IS the name ("Battlefield 6",
# "Tekken 8", "Modern Warfare III") is preserved -- only a trailing
# "... Season 3" / "... Deluxe Edition" style suffix is removed.
_EDITION_TOKENS = {
    "anniversary",
    "bundle",
    "collection",
    "collectors",
    "collector",
    "collectorz",
    "complete",
    "definitive",
    "deluxe",
    "digital",
    "dlc",
    "edition",
    "enhanced",
    "expansion",
    "gold",
    "goty",
    "launch",
    "legacy",
    "limited",
    "operator",
    "pack",
    "pass",
    "premium",
    "remaster",
    "remastered",
    "special",
    "standard",
    "starter",
    "ultimate",
    "upgrade",
}

# A trailing pure-number token is only a suffix (droppable) when it follows one
# of these "unit" markers -- "Season 3", "Operation 2", "Chapter 4". Otherwise a
# trailing number is part of the name ("Battlefield 6") and is kept.
_NUMBER_UNIT_MARKERS = {
    "season",
    "operation",
    "chapter",
    "part",
    "volume",
    "vol",
    "act",
    "episode",
}


def normalize_base_title(title: str) -> str:
    """Reduce a store title to its canonical base-game form.

    Lowercases, strips punctuation to spaces, then iteratively removes trailing
    edition/season/DLC suffix tokens. Returns "" for a title that is *only*
    suffix tokens (shouldn't happen for real games).

    Examples:
        "Destiny 2: Digital Deluxe Edition"        -> "destiny 2"
        "Halo Infinite: Operation - Legacy"        -> "halo infinite"
        "Sea of Thieves: Season 3"                 -> "sea of thieves"
        "Tekken 8: Season 3 Pass"                  -> "tekken 8"
        "Call of Duty: Modern Warfare III"         -> "call of duty modern warfare iii"
        "Battlefield 6 Season 1"                   -> "battlefield 6"
    """
    t = title.lower()
    t = re.sub(r"['’]", "", t)          # drop apostrophes so collector's == collectors
    t = re.sub(r"[^a-z0-9]+", " ", t)   # everything else -> space
    toks = t.split()

    changed = True
    while changed and toks:
        changed = False
        last = toks[-1]
        if last in _EDITION_TOKENS:
            toks.pop()
            changed = True
        elif last.isdigit() and len(toks) >= 2 and toks[-2] in _NUMBER_UNIT_MARKERS:
            toks.pop()  # drop the number
            toks.pop()  # and its unit marker ("season")
            changed = True
        elif last in _NUMBER_UNIT_MARKERS:
            # a dangling "season"/"operation" left after its number was already
            # consumed, or a bare trailing marker
            toks.pop()
            changed = True

    return " ".join(toks)


def _load_state(client):
    """Pull the read-only snapshot needed to compute the dedup plan.

    Selects games *without* canonical_game_id so the dry-run works before
    migration 017 is applied (the column won't exist yet). The column is only
    written, never read, by this script, so the plan doesn't need it.
    """
    games = _fetch_all_rows(
        lambda: client.table("games").select(
            "game_id, title, steam_app_id, created_at"
        )
    )
    watchlist = _fetch_all_rows(
        lambda: client.table("watchlist").select("id, game_id, active")
    )
    pm_ids = {
        r["game_id"]
        for r in _fetch_all_rows(lambda: client.table("player_metrics").select("game_id"))
    }
    ss_ids = {
        r["game_id"]
        for r in _fetch_all_rows(
            lambda: client.table("sentiment_snapshots").select("game_id")
        )
    }
    return games, watchlist, pm_ids, ss_ids


def _pick_canonical(rows, covered):
    """Choose the canonical row from a same-base-title group.

    Preference order:
      1. the row whose full title IS the bare base (no suffix) -- the base game
      2. a row that has data coverage (player_metrics or sentiment history)
      3. shortest title, then oldest created_at (stable tiebreak)
    """

    def sort_key(g):
        is_bare = normalize_base_title(g["title"]) == _norm_full(g["title"])
        return (
            0 if is_bare else 1,
            0 if g["game_id"] in covered else 1,
            len(g["title"]),
            g.get("created_at") or "",
        )

    return sorted(rows, key=sort_key)[0]


def _norm_full(title: str) -> str:
    """Full title normalized WITHOUT suffix stripping (for the bare-match test)."""
    t = re.sub(r"['’]", "", title.lower())
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())


# A suffix containing any of these denotes a genuinely DIFFERENT product (a
# remaster / re-release / remake with its own player base and store presence),
# NOT a repackaged edition of the base game. A base-title cluster member whose
# suffix-over-base contains one of these is never auto-collapsed by the edition
# promotion -- it stays active and is reported for manual review instead. This
# is the line between "Tekken 8: Deluxe Edition" (collapse) and "Horizon Zero
# Dawn Remastered" / "Grand Theft Auto V Enhanced" (keep).
_REMASTER_CLASS_TOKENS = {
    "remaster",
    "remastered",
    "remake",
    "remakes",
    "enhanced",
    "definitive",
    "legacy",
    "anniversary",
    "redux",
    "reloaded",
    "reforged",
    "reawakened",
    "reborn",
}


def _suffix_tokens(title, base):
    """Tokens present in the full normalized title but not in its base title.

    e.g. ("Tekken 8: Deluxe Edition", "tekken 8") -> {"deluxe", "edition"}.
    Used to classify a variant as a pure edition vs. a remaster/re-release.
    """
    base_toks = set(base.split())
    return {t for t in _norm_full(title).split() if t not in base_toks}


def compute_plan(games, watchlist, covered):
    """Return (auto_map, review_appid, review_base).

    auto_map:      list of (canonical_game, variant_game) — conservative tier
                   (shared steam_app_id AND same base title) to auto-deactivate.
    review_appid:  clusters that share a steam_app_id but split into >1 base
                   title (possible mis-mapping) — human review only.
    review_base:   clusters that share a base title but NOT a steam_app_id
                   (possible distinct remaster/spinoff) — human review only.
    """
    active_game_ids = {w["game_id"] for w in watchlist if w.get("active")}
    active_games = [g for g in games if g["game_id"] in active_game_ids]

    # --- Conservative tier + app_id-mismatch review, grouped by steam_app_id ---
    by_appid = defaultdict(list)
    for g in active_games:
        if g.get("steam_app_id"):
            by_appid[str(g["steam_app_id"])].append(g)

    auto_map = []
    review_appid = []
    for app_id, group in by_appid.items():
        by_base = defaultdict(list)
        for g in group:
            by_base[normalize_base_title(g["title"])].append(g)

        # A shared app_id that splits into multiple distinct base titles is a
        # mis-mapping candidate -- flag the whole cluster for review.
        if len(by_base) > 1:
            review_appid.append((app_id, group, dict(by_base)))

        for base, rows in by_base.items():
            if len(rows) < 2:
                continue
            canonical = _pick_canonical(rows, covered)
            for g in rows:
                if g["game_id"] != canonical["game_id"]:
                    auto_map.append((canonical, g))

    # --- Base-title-only review tier (same base title, no shared app_id) ---
    auto_variant_ids = {v["game_id"] for _, v in auto_map}
    by_base_global = defaultdict(list)
    for g in active_games:
        by_base_global[normalize_base_title(g["title"])].append(g)

    review_base = []
    for base, rows in by_base_global.items():
        if len(rows) < 2:
            continue
        app_ids = {str(g["steam_app_id"]) for g in rows if g.get("steam_app_id")}
        # Rows already handled by the conservative (shared-app_id) tier, or that
        # all share one app_id, aren't "base-only". We want groups whose members
        # are NOT fully covered by the auto tier.
        remaining = [g for g in rows if g["game_id"] not in auto_variant_ids]
        if len(remaining) < 2:
            continue
        distinct_app_ids = {
            str(g["steam_app_id"]) for g in remaining if g.get("steam_app_id")
        }
        # base-title match where the remaining members do NOT all share one app_id
        if len(distinct_app_ids) != 1 or any(
            not g.get("steam_app_id") for g in remaining
        ):
            review_base.append((base, remaining))

    return auto_map, review_appid, review_base


def compute_edition_promotions(review_base, covered):
    """Refine the base-title-only review tier into promotable editions vs. leftovers.

    Returns (edition_map, leftover_base).

    edition_map:   list of (canonical, variant) — pure editions/seasons of a base
                   game that IS present in the cluster, safe to collapse even
                   though they don't share the base's steam_app_id (a different
                   store SKU of the same underlying game).
    leftover_base: [(base, rows)] clusters left untouched — either no bare base
                   row is present (all members are DLC/editions with no base to
                   anchor on), or the remaining members are remaster-class
                   re-releases (distinct products).

    Safety rules:
      * only act on a cluster that contains a BARE base row (a member whose full
        normalized title == the base title) — that's the strong signal this is a
        real base game with editions, not a pile of standalone DLC.
      * a variant is promoted only if its suffix-over-base contains NO
        remaster-class token; otherwise it's kept active and reported.
    """
    edition_map = []
    leftover_base = []
    for base, rows in review_base:
        bare_rows = [g for g in rows if _norm_full(g["title"]) == base]
        if not bare_rows:
            leftover_base.append((base, rows))  # no base game to anchor on
            continue
        canonical = _pick_canonical(bare_rows, covered)
        promoted_any = False
        kept = []
        for g in rows:
            if g["game_id"] == canonical["game_id"]:
                continue
            if _suffix_tokens(g["title"], base) & _REMASTER_CLASS_TOKENS:
                kept.append(g)  # remaster/re-release — distinct product
            else:
                edition_map.append((canonical, g))
                promoted_any = True
        # surface the canonical + any kept remaster rows so a reviewer sees what
        # was left behind in a cluster we partially touched
        if kept:
            leftover_base.append((base, [canonical] + kept))
        elif not promoted_any:
            leftover_base.append((base, rows))
    return edition_map, leftover_base


def _print_tier(pairs, label):
    """Print an (canonical, variant) collapse tier grouped by canonical."""
    by_canonical = defaultdict(list)
    for canonical, variant in pairs:
        by_canonical[canonical["game_id"]].append((canonical, variant))
    print(
        f"{len(by_canonical)} canonical game(s) absorb {len(pairs)} variant row(s) "
        f"to deactivate.\n"
    )
    for gid, entries in sorted(by_canonical.items(), key=lambda kv: -len(kv[1])):
        canonical = entries[0][0]
        print(f"  KEEP  {canonical['title']}  (app {canonical.get('steam_app_id')})")
        for _, variant in entries:
            print(f"    x-  {variant['title']}")
    print()


def _print_plan(auto_map, review_appid, edition_map, leftover_base, show_review):
    print("=" * 78)
    print("CONSERVATIVE TIER (auto-apply with --apply): shared steam_app_id + same base title")
    print("=" * 78)
    _print_tier(auto_map, "conservative")

    print("=" * 78)
    print("EDITION-PROMOTION TIER (auto-apply with --apply-editions): same base title, "
          "pure edition/season of a base row present in the cluster")
    print("=" * 78)
    _print_tier(edition_map, "edition")

    print("-" * 78)
    print(f"REVIEW ONLY — shared steam_app_id but MULTIPLE base titles "
          f"({len(review_appid)} cluster(s), possible mis-mapping — NOT auto-applied)")
    print("-" * 78)
    if show_review:
        for app_id, group, by_base in sorted(review_appid, key=lambda x: -len(x[1])):
            print(f"  app {app_id}: {len(group)} rows across {len(by_base)} base titles")
            for base, rows in by_base.items():
                print(f"      [{base}] {[g['title'] for g in rows][:6]}")
    else:
        print("  (re-run with --show-review to list these in full)")
    print()

    print("-" * 78)
    print(f"REVIEW ONLY — base-title clusters left untouched "
          f"({len(leftover_base)} cluster(s): remaster/re-release, or no base row "
          f"present — NOT auto-applied)")
    print("-" * 78)
    if show_review:
        for base, rows in sorted(leftover_base, key=lambda x: -len(x[1])):
            print(f"  [{base}] {len(rows)} rows: {[g['title'] for g in rows][:6]}")
    else:
        print("  (re-run with --show-review to list these in full)")
    print()


def _apply(client, auto_map, watchlist):
    """Set games.canonical_game_id + watchlist.active=false for each variant."""
    wl_by_game = defaultdict(list)
    for w in watchlist:
        wl_by_game[w["game_id"]].append(w)

    deactivated = 0
    mapped = 0
    for canonical, variant in auto_map:
        client.table("games").update(
            {"canonical_game_id": canonical["game_id"]}
        ).eq("game_id", variant["game_id"]).execute()
        mapped += 1
        for w in wl_by_game.get(variant["game_id"], []):
            if w.get("active"):
                client.table("watchlist").update({"active": False}).eq(
                    "id", w["id"]
                ).execute()
                deactivated += 1
    return mapped, deactivated


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform the CONSERVATIVE-tier deactivations (default is dry-run).",
    )
    parser.add_argument(
        "--apply-editions",
        action="store_true",
        help="Perform the EDITION-PROMOTION-tier deactivations (base-title-only "
             "editions of a present base game). Separate opt-in from --apply.",
    )
    parser.add_argument(
        "--show-review",
        action="store_true",
        help="List the review-only tiers in full.",
    )
    args = parser.parse_args(argv)

    client = get_client()
    games, watchlist, pm_ids, ss_ids = _load_state(client)
    covered = pm_ids | ss_ids

    active_wl = sum(1 for w in watchlist if w.get("active"))
    print(f"Loaded {len(games)} games, {active_wl} active watchlist rows, "
          f"{len(covered)} games with data coverage.\n")

    auto_map, review_appid, review_base = compute_plan(games, watchlist, covered)
    edition_map, leftover_base = compute_edition_promotions(review_base, covered)
    _print_plan(auto_map, review_appid, edition_map, leftover_base, args.show_review)

    if not args.apply and not args.apply_editions:
        print("DRY-RUN — no writes. Re-run with --apply (conservative tier) "
              "and/or --apply-editions (edition-promotion tier) to deactivate.")
        return

    if args.apply:
        print("APPLYING conservative-tier deactivations...")
        mapped, deactivated = _apply(client, auto_map, watchlist)
        print(f"  conservative: set canonical_game_id on {mapped} game(s), "
              f"deactivated {deactivated} watchlist row(s).")

    if args.apply_editions:
        print("APPLYING edition-promotion-tier deactivations...")
        mapped, deactivated = _apply(client, edition_map, watchlist)
        print(f"  editions: set canonical_game_id on {mapped} game(s), "
              f"deactivated {deactivated} watchlist row(s).")


if __name__ == "__main__":
    main()
