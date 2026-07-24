"""
Unit tests for scripts/rebackfill_nulled_app_ids.py.

No live network calls: the Steam catalog is a plain in-memory dict. Tests pin the
precision gates — only an exact, unique, non-colliding catalog match with no
active sibling is recovered; everything else is left inactive with a reason.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.rebackfill_nulled_app_ids import build_name_index, compute_recoveries


def _game(gid, title):
    return {"game_id": gid, "title": title, "steam_app_id": None, "rawg_slug": "bad"}


def test_exact_unique_match_is_recovered():
    catalog = {"2054970": "Dragon's Dogma 2", "999": "Other Game"}
    idx = build_name_index(catalog)
    rec, skips = compute_recoveries([_game("dd2", "Dragon's Dogma 2")], idx,
                                    active_app_ids=set(), active_bases=set())
    assert [(g["game_id"], a) for g, a in rec] == [("dd2", "2054970")]
    assert skips == []


def test_no_catalog_match_skipped():
    idx = build_name_index({"1": "Some Steam Game"})
    rec, skips = compute_recoveries([_game("m", "Chunithm X-Verse-X")], idx,
                                    active_app_ids=set(), active_bases=set())
    assert rec == []
    assert skips[0][1] == "no_catalog_match"


def test_ambiguous_catalog_skipped():
    # two different app_ids share a normalized name -> can't safely pick one
    catalog = {"10": "Resident Evil 4", "20": "RESIDENT EVIL 4"}
    idx = build_name_index(catalog)
    rec, skips = compute_recoveries([_game("re4", "Resident Evil 4")], idx,
                                    active_app_ids=set(), active_bases=set())
    assert rec == []
    assert skips[0][1] == "ambiguous_catalog"


def test_app_id_already_active_skipped():
    catalog = {"271590": "Grand Theft Auto V"}
    idx = build_name_index(catalog)
    rec, skips = compute_recoveries([_game("gta", "Grand Theft Auto V")], idx,
                                    active_app_ids={"271590"}, active_bases=set())
    assert rec == []
    assert skips[0][1] == "app_id_already_active"


def test_active_sibling_skipped():
    catalog = {"1259420": "Days Gone Remastered"}
    idx = build_name_index(catalog)
    # an active "Days Gone" row already covers base title "days gone"
    rec, skips = compute_recoveries([_game("dgr", "Days Gone Remastered")], idx,
                                    active_app_ids=set(), active_bases={"days gone"})
    assert rec == []
    assert skips[0][1] == "sibling_active"


def test_duplicate_appid_same_name_is_not_ambiguous():
    # same app_id listed twice for one name is fine (set collapses it)
    idx = build_name_index({"42": "Lost Judgment"})
    idx["lost judgment"].append("42")
    rec, skips = compute_recoveries([_game("lj", "Lost Judgment")], idx,
                                    active_app_ids=set(), active_bases=set())
    assert [a for _, a in rec] == ["42"]
