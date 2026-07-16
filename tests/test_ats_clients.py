"""
Unit tests for agents/workers/studio_intel/ats_clients.py.

No live network calls: the module-level _get_json helper is monkeypatched, so
each fetcher's response *parsing* is exercised against realistic (including
malformed) ATS payloads. Covers the Lever `categories: null` crash regression.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agents.workers.studio_intel.ats_clients as ats
from agents.workers.studio_intel.ats_clients import (
    fetch_ashby_jobs,
    fetch_configured_jobs,
    fetch_greenhouse_jobs,
    fetch_lever_jobs,
    load_ats_board_map,
    summarize_hiring_signal,
)


# ---------------------------------------------------------------------------
# load_ats_board_map
# ---------------------------------------------------------------------------

def test_load_ats_board_map_missing_env_returns_empty(monkeypatch):
    monkeypatch.delenv("STUDIO_ATS_BOARDS", raising=False)
    assert load_ats_board_map() == {}


def test_load_ats_board_map_invalid_json_returns_empty(monkeypatch):
    monkeypatch.setenv("STUDIO_ATS_BOARDS", "{not json")
    assert load_ats_board_map() == {}


def test_load_ats_board_map_non_dict_returns_empty(monkeypatch):
    monkeypatch.setenv("STUDIO_ATS_BOARDS", '["a", "b"]')
    assert load_ats_board_map() == {}


def test_load_ats_board_map_valid(monkeypatch):
    monkeypatch.setenv("STUDIO_ATS_BOARDS", '{"Electronic Arts": {"greenhouse": "ea"}}')
    assert load_ats_board_map() == {"Electronic Arts": {"greenhouse": "ea"}}


# ---------------------------------------------------------------------------
# fetch_greenhouse_jobs / fetch_lever_jobs / fetch_ashby_jobs parsing
# ---------------------------------------------------------------------------

def test_greenhouse_parsing_tolerates_missing_fields(monkeypatch):
    payload = {
        "jobs": [
            {"title": "Backend Engineer", "departments": [{"name": "Live Ops"}],
             "location": {"name": "Remote"}, "absolute_url": "https://gh/1"},
            # departments empty, location missing, no absolute_url
            {"title": "QA Analyst", "departments": []},
        ]
    }
    monkeypatch.setattr(ats, "_get_json", lambda url, params=None: payload)

    jobs = fetch_greenhouse_jobs("ea")

    assert jobs[0] == {
        "title": "Backend Engineer", "department": "Live Ops",
        "location": "Remote", "url": "https://gh/1",
    }
    assert jobs[1]["title"] == "QA Analyst"
    assert jobs[1]["department"] == ""
    assert jobs[1]["url"] is None


def test_lever_parsing_tolerates_null_categories(monkeypatch):
    """Regression: `job.get("categories", {})` returned None (not {}) when the
    Lever payload carried an explicit "categories": null, crashing the fetch
    with TypeError and costing that studio its whole ATS check."""
    payload = [
        {"text": "Gameplay Engineer", "team": "Core", "categories": None,
         "hostedUrl": "https://lever/1"},
        {"text": "Monetization Designer", "team": "Economy",
         "categories": {"location": "Los Angeles"}, "hostedUrl": "https://lever/2"},
    ]
    monkeypatch.setattr(ats, "_get_json", lambda url, params=None: payload)

    jobs = fetch_lever_jobs("riotgames")

    assert jobs[0]["location"] == ""
    assert jobs[1]["location"] == "Los Angeles"


def test_ashby_parsing_tolerates_null_location(monkeypatch):
    payload = {"jobs": [{"title": "Producer", "department": "Prod", "location": None,
                         "jobUrl": None}]}
    monkeypatch.setattr(ats, "_get_json", lambda url, params=None: payload)

    jobs = fetch_ashby_jobs("token")

    assert jobs == [{"title": "Producer", "department": "Prod", "location": "", "url": None}]


def test_fetch_configured_jobs_empty_config_returns_empty():
    assert fetch_configured_jobs({}) == []


def test_fetch_configured_jobs_dispatches_greenhouse_first(monkeypatch):
    called = []
    monkeypatch.setattr(ats, "fetch_greenhouse_jobs", lambda token: called.append(("gh", token)) or [])
    fetch_configured_jobs({"greenhouse": "ea", "lever": "also-set"})
    assert called == [("gh", "ea")]


# ---------------------------------------------------------------------------
# summarize_hiring_signal
# ---------------------------------------------------------------------------

def test_summarize_no_jobs_returns_none():
    assert summarize_hiring_signal([]) is None


def test_summarize_live_service_roles_trigger_surge():
    jobs = [{"title": t} for t in (
        "Live Ops Engineer", "Backend Server Engineer", "Monetization Designer", "Artist",
    )]
    signal = summarize_hiring_signal(jobs)
    assert signal is not None
    assert signal[0] == "hiring_surge"
    assert "live-service" in signal[1]


def test_summarize_small_generic_board_returns_none():
    jobs = [{"title": "Character Artist"}, {"title": "Narrative Designer"}]
    assert summarize_hiring_signal(jobs) is None


def test_summarize_large_board_triggers_surge_on_volume():
    jobs = [{"title": f"Concept Artist {i}"} for i in range(20)]
    signal = summarize_hiring_signal(jobs)
    assert signal is not None
    assert "20 open roles" in signal[1]


def test_summarize_tolerates_jobs_missing_title_key():
    jobs = [{"department": "Art"} for _ in range(25)]
    signal = summarize_hiring_signal(jobs)
    assert signal is not None  # volume threshold still counts them
