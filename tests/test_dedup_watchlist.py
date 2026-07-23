"""
Unit tests for scripts/dedup_watchlist.py.

No live network calls: compute_plan/normalize_base_title operate on plain in-
memory dicts. The plan tests are the important ones — they pin the conservative-
tier safety contract: only rows that share a steam_app_id AND normalize to the
same base title are auto-collapsed; mis-mapped-app_id and base-title-only
clusters are surfaced for review, never auto-deactivated.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.dedup_watchlist import normalize_base_title, compute_plan


# --------------------------------------------------------------------------- #
# normalize_base_title
# --------------------------------------------------------------------------- #

def test_strips_edition_suffixes():
    assert normalize_base_title("Destiny 2: Digital Deluxe Edition") == "destiny 2"
    assert normalize_base_title("Destiny 2: Collector's Edition") == "destiny 2"
    assert normalize_base_title("Destiny 2: Launch Edition") == "destiny 2"
    assert normalize_base_title("Tekken 8: Ultimate Edition") == "tekken 8"


def test_strips_season_and_operation_suffixes():
    assert normalize_base_title("Sea of Thieves: Season 3") == "sea of thieves"
    assert normalize_base_title("Battlefield 6 Season 1") == "battlefield 6"
    assert normalize_base_title("Tekken 8: Season 3 Pass") == "tekken 8"
    assert normalize_base_title("Halo Infinite: Operation - Legacy") == "halo infinite"
    assert normalize_base_title("Halo Infinite: Operation 2") == "halo infinite"


def test_preserves_interior_and_name_numbers():
    # a trailing number that is part of the NAME must survive
    assert normalize_base_title("Battlefield 6") == "battlefield 6"
    assert normalize_base_title("Tekken 8") == "tekken 8"
    # roman-numeral sequel token is not an edition marker
    assert (
        normalize_base_title("Call of Duty: Modern Warfare III")
        == "call of duty modern warfare iii"
    )


def test_bare_title_unchanged():
    assert normalize_base_title("Sea of Thieves") == "sea of thieves"
    assert normalize_base_title("Elden Ring") == "elden ring"


# --------------------------------------------------------------------------- #
# compute_plan
# --------------------------------------------------------------------------- #

def _game(gid, title, app_id=None, created="2025-01-01"):
    return {
        "game_id": gid,
        "title": title,
        "steam_app_id": app_id,
        "created_at": created,
    }


def _active(gid):
    return {"id": f"wl-{gid}", "game_id": gid, "active": True}


def test_conservative_tier_collapses_shared_appid_same_base():
    games = [
        _game("d2", "Destiny 2", "1085660"),
        _game("d2dd", "Destiny 2: Digital Deluxe Edition", "1085660"),
        _game("d2ce", "Destiny 2: Collector's Edition", "1085660"),
    ]
    watchlist = [_active(g["game_id"]) for g in games]
    covered = {"d2"}  # base game has data history

    auto, review_appid, review_base = compute_plan(games, watchlist, covered)

    variant_ids = {v["game_id"] for _, v in auto}
    canonical_ids = {c["game_id"] for c, _ in auto}
    assert variant_ids == {"d2dd", "d2ce"}
    assert canonical_ids == {"d2"}  # the bare, covered row is kept
    assert review_appid == []
    assert review_base == []


def test_mismatched_appid_is_review_not_auto():
    # two genuinely different games mis-mapped to one app_id must NOT collapse
    games = [
        _game("ff7", "Final Fantasy VII Remake Intergrade", "1462040"),
        _game("ff8", "Final Fantasy VIII Remastered", "1462040"),
    ]
    watchlist = [_active(g["game_id"]) for g in games]

    auto, review_appid, review_base = compute_plan(games, watchlist, covered=set())

    assert auto == []  # different base titles -> nothing auto-collapsed
    assert len(review_appid) == 1  # surfaced for human review instead


def test_base_title_only_match_is_review_not_auto():
    # same base title but DIFFERENT app_ids (e.g. a remaster) -> review only
    games = [
        _game("dg", "Days Gone", "1259420"),
        _game("dgr", "Days Gone Remastered", "3419430"),
    ]
    watchlist = [_active(g["game_id"]) for g in games]

    auto, review_appid, review_base = compute_plan(games, watchlist, covered=set())

    assert auto == []
    assert len(review_base) == 1
    assert review_base[0][0] == "days gone"


def test_inactive_rows_are_ignored():
    games = [
        _game("d2", "Destiny 2", "1085660"),
        _game("d2dd", "Destiny 2: Digital Deluxe Edition", "1085660"),
    ]
    watchlist = [
        _active("d2"),
        {"id": "wl-d2dd", "game_id": "d2dd", "active": False},  # already deactivated
    ]

    auto, review_appid, review_base = compute_plan(games, watchlist, covered=set())

    # only one active member in the cluster -> nothing to collapse
    assert auto == []


def test_prefers_bare_title_as_canonical():
    # canonical should be the bare base game, not an edition, regardless of order
    games = [
        _game("edi", "Street Fighter 6: Ultimate Edition", "1364780"),
        _game("bare", "Street Fighter 6", "1364780"),
    ]
    watchlist = [_active(g["game_id"]) for g in games]

    auto, _, _ = compute_plan(games, watchlist, covered=set())

    assert len(auto) == 1
    canonical, variant = auto[0]
    assert canonical["game_id"] == "bare"
    assert variant["game_id"] == "edi"
