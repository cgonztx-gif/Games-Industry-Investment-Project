"""
Unit tests for scripts/deactivate_dead_games.py.

No live network calls: compute_dead_plan operates on plain in-memory dicts. The
tests pin the safety contract — only no-coverage, no-steam_app_id, non-canonical
rows are auto-deactivated; canonicals and app_id-bearing anomalies are held for
review.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.deactivate_dead_games import compute_dead_plan


def _game(gid, title, app_id=None, canonical=None):
    return {
        "game_id": gid,
        "title": title,
        "steam_app_id": app_id,
        "canonical_game_id": canonical,
    }


def _active(gid, active=True):
    return {"id": f"wl-{gid}", "game_id": gid, "active": active}


def test_dead_no_coverage_no_appid_is_auto():
    games = [_game("d", "The Sims 4: Comfy Gamer Kit")]
    watchlist = [_active("d")]
    auto, canon, has_app = compute_dead_plan(games, watchlist, covered=set())
    assert [g["game_id"] for g in auto] == ["d"]
    assert canon == [] and has_app == []


def test_covered_game_is_spared():
    games = [_game("live", "Elden Ring", app_id="1245620")]
    watchlist = [_active("live")]
    auto, canon, has_app = compute_dead_plan(games, watchlist, covered={"live"})
    assert auto == []


def test_inactive_row_ignored():
    games = [_game("d", "Dead DLC")]
    watchlist = [_active("d", active=False)]
    auto, canon, has_app = compute_dead_plan(games, watchlist, covered=set())
    assert auto == []


def test_canonical_dead_row_is_review_not_auto():
    # "base" is dead, but an edition points to it via canonical_game_id
    games = [
        _game("base", "World of Warcraft: Shadowlands"),
        _game("edi", "WoW: Shadowlands - Deluxe", canonical="base"),
    ]
    # only the base is active; the edition was already deactivated by dedup
    watchlist = [_active("base"), _active("edi", active=False)]
    auto, canon, has_app = compute_dead_plan(games, watchlist, covered=set())
    assert auto == []
    assert [g["game_id"] for g in canon] == ["base"]


def test_dead_with_appid_is_review_not_auto():
    games = [_game("anom", "Delisted Game", app_id="999999")]
    watchlist = [_active("anom")]
    auto, canon, has_app = compute_dead_plan(games, watchlist, covered=set())
    assert auto == []
    assert [g["game_id"] for g in has_app] == ["anom"]


def test_mixed_set_partitions_correctly():
    games = [
        _game("a", "Auto Dead Kit"),                         # -> auto
        _game("b", "Live Game", app_id="111"),               # covered -> spared
        _game("c", "Base Canon"),                            # -> canonical review
        _game("c_edi", "Base Canon: Deluxe", canonical="c"),
        _game("d", "Has App No Data", app_id="222"),         # -> app_id review
    ]
    watchlist = [_active("a"), _active("b"), _active("c"),
                 _active("c_edi", active=False), _active("d")]
    auto, canon, has_app = compute_dead_plan(games, watchlist, covered={"b"})
    assert [g["game_id"] for g in auto] == ["a"]
    assert [g["game_id"] for g in canon] == ["c"]
    assert [g["game_id"] for g in has_app] == ["d"]
