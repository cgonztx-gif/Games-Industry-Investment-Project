"""
Unit tests for scripts/fix_mismapped_app_ids.py.

No live network calls: the Steam name lookup is injected as a plain dict-backed
fake, so resolve_mismap runs fully offline. Tests pin the two safety-critical
behaviors — (1) clean base+DLC clusters are separated from genuinely foreign
ones, and (2) the true owner is chosen by matching the Steam app's real name,
with non-owners (incl. shorter generic-prefix titles) treated as foreign.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.fix_mismapped_app_ids import (
    is_dlc_of,
    classify_clusters,
    resolve_mismap,
    _match_owner,
)


def _game(gid, title, app_id):
    return {"game_id": gid, "title": title, "steam_app_id": app_id,
            "canonical_game_id": None, "created_at": "2025-01-01"}


# --------------------------------------------------------------------------- #
# is_dlc_of
# --------------------------------------------------------------------------- #

def test_is_dlc_of_separator_only():
    assert is_dlc_of("Borderlands 3", "Borderlands 3: Psycho Krieg DLC")
    assert is_dlc_of("Borderlands 3", "Borderlands 3 - Season Pass")
    assert is_dlc_of("Borderlands 3", "Borderlands 3")            # itself
    # a different numbered game is NOT DLC (no separator after the prefix)
    assert not is_dlc_of("Final Fantasy", "Final Fantasy VII Remake")
    assert not is_dlc_of("Modern Warfare", "Modern Warfare III")


# --------------------------------------------------------------------------- #
# classify_clusters
# --------------------------------------------------------------------------- #

def test_clean_cluster_is_base_plus_dlc():
    games = [
        _game("b", "Capcom Arcade Stadium", "1515950"),
        _game("d1", "Capcom Arcade Stadium: Final Fight", "1515950"),
        _game("d2", "Capcom Arcade Stadium: Strider", "1515950"),
    ]
    clean, foreign = classify_clusters(games, covered=set())
    assert foreign == []
    assert len(clean) == 1
    owner, dlc = clean[0]
    assert owner["game_id"] == "b"
    assert {g["game_id"] for g in dlc} == {"d1", "d2"}


def test_foreign_cluster_flagged_when_member_not_dlc():
    games = [
        _game("re4", "Resident Evil 4", "2050650"),
        _game("re4d", "Resident Evil 4: The Mercenaries", "2050650"),
        _game("gb4", "Gundam Breaker 4", "2050650"),     # foreign -> not owner's DLC
    ]
    clean, foreign = classify_clusters(games, covered=set())
    assert clean == []
    assert len(foreign) == 1
    assert foreign[0][0] == "2050650"


def test_singleton_appid_ignored():
    games = [_game("solo", "Elden Ring", "1245620")]
    clean, foreign = classify_clusters(games, covered=set())
    assert clean == [] and foreign == []


# --------------------------------------------------------------------------- #
# _match_owner
# --------------------------------------------------------------------------- #

def test_match_owner_picks_name_closest_member():
    members = [
        _game("ff", "Final Fantasy", "1462040"),                       # generic prefix
        _game("ffr", "Final Fantasy VII Remake", "1462040"),
        _game("ffri", "Final Fantasy VII Remake Intergrade", "1462040"),
        _game("card", "Final Fantasy Digital Card Game", "1462040"),
    ]
    owner = _match_owner(members, "FINAL FANTASY VII REMAKE INTERGRADE", covered=set())
    assert owner["game_id"] == "ffri"


def test_match_owner_prefers_base_over_longer_dlc():
    members = [
        _game("re4", "Resident Evil 4", "2050650"),
        _game("merc", "Resident Evil 4: The Mercenaries", "2050650"),
    ]
    owner = _match_owner(members, "Resident Evil 4", covered=set())
    assert owner["game_id"] == "re4"


def test_match_owner_none_when_no_member_matches():
    members = [
        _game("hd", "Hero Dice", "1656780"),
        _game("mkt", "Mario Kart Tour: Cat Tour", "1656780"),
    ]
    owner = _match_owner(members, "Hero's Hour", covered=set())
    assert owner is None


# --------------------------------------------------------------------------- #
# resolve_mismap
# --------------------------------------------------------------------------- #

def test_resolve_mismap_owner_found_partitions_dlc_and_foreign():
    foreign = [(
        "2050650",
        [
            _game("re4", "Resident Evil 4", "2050650"),
            _game("merc", "Resident Evil 4: The Mercenaries", "2050650"),
            _game("gb4", "Gundam Breaker 4", "2050650"),
        ],
    )]
    names = {"2050650": "Resident Evil 4"}
    resolved, unresolved = resolve_mismap(foreign, lambda a: names.get(a), covered=set())
    assert unresolved == []
    app_id, name, owner, dlc, foreign_members = resolved[0]
    assert owner["game_id"] == "re4"
    assert [g["game_id"] for g in dlc] == ["merc"]
    assert [g["game_id"] for g in foreign_members] == ["gb4"]


def test_resolve_mismap_no_owner_all_foreign():
    foreign = [(
        "1656780",
        [
            _game("hd", "Hero Dice", "1656780"),
            _game("mkt", "Mario Kart Tour: Cat Tour", "1656780"),
        ],
    )]
    names = {"1656780": "Hero's Hour"}
    resolved, unresolved = resolve_mismap(foreign, lambda a: names.get(a), covered=set())
    assert unresolved == []
    app_id, name, owner, dlc, foreign_members = resolved[0]
    assert owner is None
    assert dlc == []
    assert {g["game_id"] for g in foreign_members} == {"hd", "mkt"}


def test_resolve_mismap_no_steam_name_is_unresolved_not_deactivated():
    # a missing Steam name must SKIP the cluster, never deactivate its rows
    foreign = [("999", [_game("a", "Some Game", "999"), _game("b", "Other: DLC", "999")])]
    resolved, unresolved = resolve_mismap(foreign, lambda a: None, covered=set())
    assert resolved == []
    assert len(unresolved) == 1
    assert unresolved[0][0] == "999"
