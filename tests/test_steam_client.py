"""
Unit tests for agents/workers/market_player/steam_client.py.

No live network calls: requests.get is monkeypatched with a fake that returns
pre-scripted responses. time.sleep is also patched to avoid real delays.

Covers the IStoreService/GetAppList/v1 pagination fix for the removed
ISteamApps/GetAppList/v2 endpoint (404 confirmed live 2026-07-09).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agents.workers.market_player.steam_client as steam_client


class _FakeResp:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


def _app_list_page(apps, have_more, last_appid=None):
    return _FakeResp(
        {
            "response": {
                "apps": apps,
                "have_more_results": have_more,
                "last_appid": last_appid,
            }
        }
    )


def _charts_resp(ranks):
    return _FakeResp({"response": {"ranks": ranks}})


def test_app_name_map_single_page(monkeypatch):
    responses = iter([_app_list_page([{"appid": 570, "name": "Dota 2"}], have_more=False)])
    monkeypatch.setattr(steam_client.requests, "get", lambda *a, **kw: next(responses))
    monkeypatch.setattr(steam_client.time, "sleep", lambda _: None)

    names = steam_client._app_name_map()

    assert names == {"570": "Dota 2"}


def test_app_name_map_paginates_until_have_more_results_false(monkeypatch):
    calls = []
    responses = iter(
        [
            _app_list_page([{"appid": 10, "name": "Counter-Strike"}], have_more=True, last_appid=10),
            _app_list_page([{"appid": 20, "name": "Team Fortress Classic"}], have_more=False),
        ]
    )

    def fake_get(url, params=None, timeout=None):
        calls.append(params)
        return next(responses)

    monkeypatch.setattr(steam_client.requests, "get", fake_get)
    monkeypatch.setattr(steam_client.time, "sleep", lambda _: None)

    names = steam_client._app_name_map()

    assert names == {"10": "Counter-Strike", "20": "Team Fortress Classic"}
    assert len(calls) == 2
    assert "last_appid" not in calls[0]
    assert calls[1]["last_appid"] == 10


def test_app_name_map_stops_on_empty_page(monkeypatch):
    responses = iter([_app_list_page([], have_more=True, last_appid=999)])
    monkeypatch.setattr(steam_client.requests, "get", lambda *a, **kw: next(responses))
    monkeypatch.setattr(steam_client.time, "sleep", lambda _: None)

    names = steam_client._app_name_map()

    assert names == {}


def test_app_name_map_uses_correct_endpoint_and_key(monkeypatch):
    monkeypatch.setenv("STEAM_API_KEY", "test-key-123")
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _app_list_page([], have_more=False)

    monkeypatch.setattr(steam_client.requests, "get", fake_get)
    monkeypatch.setattr(steam_client.time, "sleep", lambda _: None)

    steam_client._app_name_map()

    assert captured["url"] == "https://api.steampowered.com/IStoreService/GetAppList/v1/"
    assert captured["params"]["key"] == "test-key-123"
    assert captured["params"]["max_results"] == steam_client._APP_LIST_PAGE_SIZE


def test_get_top_ccu_games_filters_by_min_ccu_and_resolves_names(monkeypatch):
    responses = iter(
        [
            _app_list_page(
                [{"appid": 730, "name": "Counter-Strike 2"}, {"appid": 570, "name": "Dota 2"}],
                have_more=False,
            ),
            _charts_resp(
                [
                    {"appid": 730, "current_in_game": 10000},
                    {"appid": 570, "current_in_game": 100},
                ]
            ),
        ]
    )
    monkeypatch.setattr(steam_client.requests, "get", lambda *a, **kw: next(responses))
    monkeypatch.setattr(steam_client.time, "sleep", lambda _: None)

    results = steam_client.get_top_ccu_games(min_ccu=5000)

    assert len(results) == 1
    assert results[0]["steam_app_id"] == "730"
    assert results[0]["title"] == "Counter-Strike 2"
    assert results[0]["ccu"] == 10000
