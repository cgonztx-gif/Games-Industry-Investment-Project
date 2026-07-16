"""
Unit tests for agents/workers/studio_intel/worker.py::run().

No live network / Supabase calls -- every external touch point (DB client,
EDGAR client, ATS clients) is monkeypatched on the worker module's own
namespace, matching the convention tests/test_news_worker.py establishes.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agents.workers.studio_intel.worker as studio_worker

_FAKE_DB = object()  # sentinel -- no real Supabase calls


def _studio(ticker="EA", studio_id="s1", name="Electronic Arts"):
    return {"ticker": ticker, "studio_id": studio_id, "name": name}


def _filing(date="2026-07-01", items_raw="2.05", source_url="https://sec.gov/a"):
    return {"date": date, "items_raw": items_raw, "source_url": source_url}


def _run(
    studios=None,
    cik_map=None,
    cik_map_raises=False,
    ats_board_map=None,
    filings_by_ticker=None,
    filings_raises_for=None,
    jobs_by_studio=None,
    jobs_raises_for=None,
    prior_occurrences=0,
    written=None,
    monkeypatch=None,
):
    if studios is None:
        studios = [_studio()]
    if cik_map is None:
        cik_map = {"EA": 12345}
    if ats_board_map is None:
        ats_board_map = {}
    if filings_by_ticker is None:
        filings_by_ticker = {}
    if filings_raises_for is None:
        filings_raises_for = set()
    if jobs_by_studio is None:
        jobs_by_studio = {}
    if jobs_raises_for is None:
        jobs_raises_for = set()
    if written is None:
        written = []

    monkeypatch.setattr(studio_worker, "get_client", lambda: _FAKE_DB)
    monkeypatch.setattr(studio_worker, "get_studios_with_tickers", lambda db: studios)
    monkeypatch.setattr(studio_worker, "time", studio_worker.time)
    monkeypatch.setattr(studio_worker.time, "sleep", lambda *_: None)

    if cik_map_raises:
        def _boom_cik():
            raise RuntimeError("EDGAR CIK fetch down")

        monkeypatch.setattr(studio_worker, "load_cik_map", _boom_cik)
    else:
        monkeypatch.setattr(studio_worker, "load_cik_map", lambda: cik_map)

    monkeypatch.setattr(studio_worker, "load_ats_board_map", lambda: ats_board_map)

    def _fake_get_filings(cik, days_back=60):
        for ticker, c in cik_map.items():
            if c == cik and ticker in filings_raises_for:
                raise RuntimeError(f"EDGAR filings fetch failed for {ticker}")
        for ticker, c in cik_map.items():
            if c == cik:
                return filings_by_ticker.get(ticker, [])
        return []

    monkeypatch.setattr(studio_worker, "get_recent_8k_filings", _fake_get_filings)
    monkeypatch.setattr(
        studio_worker, "count_recent_studio_signals", lambda db, sid, sig, since: prior_occurrences
    )

    def _fake_fetch_jobs(config):
        name = config.get("_studio_name")
        if name in jobs_raises_for:
            raise RuntimeError(f"ATS fetch failed for {name}")
        return jobs_by_studio.get(name, [])

    monkeypatch.setattr(studio_worker, "fetch_configured_jobs", _fake_fetch_jobs)

    def _fake_write(db, signal):
        written.append(signal)
        return True

    monkeypatch.setattr(studio_worker, "write_studio_signal", _fake_write)

    return studio_worker.run()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_happy_path_writes_signal_for_8k_filing(monkeypatch):
    studios = [_studio("EA", "s1", "Electronic Arts")]
    filings = {"EA": [_filing(items_raw="2.01")]}  # acquisition, high severity
    written = []

    result = _run(studios=studios, filings_by_ticker=filings, written=written, monkeypatch=monkeypatch)

    assert result["studios_checked"] == 1
    assert result["signals_written"] == 1
    assert result["skipped_no_cik"] == 0
    assert written[0]["signal_type"] == "acquisition"
    assert written[0]["severity"] == "high"


def test_no_filings_this_window_writes_nothing(monkeypatch):
    studios = [_studio("EA", "s1", "Electronic Arts")]
    written = []

    result = _run(studios=studios, filings_by_ticker={"EA": []}, written=written, monkeypatch=monkeypatch)

    assert result["studios_checked"] == 1
    assert result["signals_written"] == 0
    assert written == []


def test_distress_escalation_applies_when_prior_occurrences_exist(monkeypatch):
    studios = [_studio("EA", "s1", "Electronic Arts")]
    filings = {"EA": [_filing(items_raw="5.02")]}  # exec_departure, medium normally
    written = []

    result = _run(
        studios=studios,
        filings_by_ticker=filings,
        prior_occurrences=5,
        written=written,
        monkeypatch=monkeypatch,
    )

    # 5.02 (exec_departure) is not in DISTRESS_ESCALATION_SIGNAL_TYPES, so no
    # escalation should occur regardless of prior_occurrences -- confirms the
    # escalation gate is signal-type-scoped, not blanket.
    assert written[0]["severity"] == "medium"
    assert result["distress_escalations"] == 0


def test_layoffs_start_at_medium_not_high(monkeypatch):
    """
    A single layoffs filing with no prior occurrences must land at 'medium',
    not 'high' -- org-health-signal-analysis SKILL.md only grades a single
    8-K layoffs item (no headcount/magnitude available) up to 'high' for
    "above 15%... or repeated rounds". Regression guard for a real bug: 2.05
    was previously hardcoded straight to 'high', which made every layoffs
    filing start at escalate_for_repeat_distress()'s ceiling and permanently
    disabled escalation for repeat layoffs (see test below).
    """
    studios = [_studio("EA", "s1", "Electronic Arts")]
    filings = {"EA": [_filing(items_raw="2.05")]}
    written = []

    result = _run(
        studios=studios,
        filings_by_ticker=filings,
        prior_occurrences=0,
        written=written,
        monkeypatch=monkeypatch,
    )

    assert written[0]["severity"] == "medium"
    assert result["distress_escalations"] == 0


def test_layoffs_escalate_to_high_on_repeat_within_lookback(monkeypatch):
    studios = [_studio("EA", "s1", "Electronic Arts")]
    filings = {"EA": [_filing(items_raw="2.05")]}
    written = []

    result = _run(
        studios=studios,
        filings_by_ticker=filings,
        prior_occurrences=2,
        written=written,
        monkeypatch=monkeypatch,
    )

    assert written[0]["severity"] == "high"
    assert "escalated" in written[0]["description"]
    assert result["distress_escalations"] == 1


def test_impairment_stays_high_and_never_reports_as_escalated(monkeypatch):
    """
    Impairment (2.06) is unconditionally 'high' per SKILL.md ("Going-concern,
    impairment, or covenant language | High", no graduated tier) -- a repeat
    occurrence has nothing to escalate to, so escalate_for_repeat_distress()
    correctly no-ops even with prior_occurrences > 0.
    """
    studios = [_studio("EA", "s1", "Electronic Arts")]
    filings = {"EA": [_filing(items_raw="2.06")]}
    written = []

    result = _run(
        studios=studios,
        filings_by_ticker=filings,
        prior_occurrences=3,
        written=written,
        monkeypatch=monkeypatch,
    )

    assert written[0]["severity"] == "high"
    assert "escalated" not in written[0]["description"]
    assert result["distress_escalations"] == 0


# ---------------------------------------------------------------------------
# Degraded paths
# ---------------------------------------------------------------------------

def test_studio_with_no_cik_is_skipped_not_errored(monkeypatch):
    studios = [_studio("ZZZZ", "s1", "Unknown Co")]

    result = _run(studios=studios, cik_map={}, monkeypatch=monkeypatch)

    assert result["skipped_no_cik"] == 1
    assert result["studios_checked"] == 0
    assert result["error_count"] == 0


def test_cik_map_fetch_failure_degrades_all_studios_to_skipped(monkeypatch):
    studios = [_studio("EA", "s1", "Electronic Arts"), _studio("TTWO", "s2", "Take-Two")]

    result = _run(studios=studios, cik_map_raises=True, monkeypatch=monkeypatch)

    assert result["skipped_no_cik"] == 2
    assert result["studios_checked"] == 0
    assert result["error_count"] == 0


def test_edgar_filing_fetch_error_is_isolated_per_studio(monkeypatch):
    studios = [_studio("EA", "s1", "Electronic Arts"), _studio("TTWO", "s2", "Take-Two")]
    cik_map = {"EA": 1, "TTWO": 2}
    filings = {"TTWO": [_filing(items_raw="2.05")]}
    written = []

    result = _run(
        studios=studios,
        cik_map=cik_map,
        filings_by_ticker=filings,
        filings_raises_for={"EA"},
        written=written,
        monkeypatch=monkeypatch,
    )

    assert result["error_count"] == 1
    assert result["errors"][0]["ticker"] == "EA"
    assert result["errors"][0]["source"] == "edgar"
    # TTWO still gets checked despite EA's failure
    assert result["signals_written"] == 1
    assert written[0]["studio_id"] == "s2"


def test_ats_error_does_not_block_edgar_signal_for_same_studio(monkeypatch):
    studios = [_studio("EA", "s1", "Electronic Arts")]
    filings = {"EA": [_filing(items_raw="2.05")]}
    ats_board_map = {"Electronic Arts": {"greenhouse": "ea", "_studio_name": "Electronic Arts"}}
    written = []

    result = _run(
        studios=studios,
        filings_by_ticker=filings,
        ats_board_map=ats_board_map,
        jobs_raises_for={"Electronic Arts"},
        written=written,
        monkeypatch=monkeypatch,
    )

    assert result["error_count"] == 1
    assert result["errors"][0]["source"] == "ats"
    # EDGAR signal still written despite the ATS failure
    assert result["signals_written"] == 1


def test_empty_studio_list_returns_zeroed_stats(monkeypatch):
    result = _run(studios=[], monkeypatch=monkeypatch)

    assert result["studios_checked"] == 0
    assert result["signals_written"] == 0
    assert result["error_count"] == 0
    assert result["skipped_no_cik"] == 0
