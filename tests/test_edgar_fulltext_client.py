"""
Unit tests for agents/workers/discovery/edgar_fulltext_client.py.

No live network calls: requests.get is monkeypatched with a fake that returns
pre-scripted responses. time.sleep is also patched to avoid real delays.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agents.workers.discovery.edgar_fulltext_client as edgar_mod
from agents.workers.discovery.edgar_fulltext_client import search_new_gaming_filings


class _FakeResp:
    def __init__(self, status_code=200, body=None, json_error=False):
        self.status_code = status_code
        self._body = body or {"hits": {"hits": []}}
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("not JSON")
        return self._body


def _hit(display_name: str, form: str = "S-1", items=None, file_date="2026-07-01", cik="0001234567"):
    return {
        "_source": {
            "display_names": [display_name],
            "form": form,
            "items": items or [],
            "file_date": file_date,
            "ciks": [cik],
        }
    }


def _resp_with_hits(hits):
    return _FakeResp(body={"hits": {"hits": hits}})


# ---------------------------------------------------------------------------
# S-1 happy path
# ---------------------------------------------------------------------------

def test_s1_filing_parsed(monkeypatch):
    responses = iter([
        # keyword 1: S-1, 8-K; keyword 2: S-1, 8-K
        _resp_with_hits([_hit("GameStart Inc.  (GSTR)  (CIK 0001234567)")]),
        _resp_with_hits([]),
        _resp_with_hits([]),
        _resp_with_hits([]),
    ])
    monkeypatch.setattr(edgar_mod.requests, "get", lambda *a, **kw: next(responses))
    monkeypatch.setattr(edgar_mod.time, "sleep", lambda _: None)

    results = search_new_gaming_filings(known_tickers=set())

    assert len(results) == 1
    assert results[0]["ticker"] == "GSTR"
    assert results[0]["company_name"] == "GameStart Inc."
    assert results[0]["form"] == "S-1"
    assert results[0]["trigger_signal"] == "edgar_new_ipo_filing"


# ---------------------------------------------------------------------------
# 8-K acquisition items filtering
# ---------------------------------------------------------------------------

def test_8k_includes_only_acquisition_items(monkeypatch):
    # Item 7.01 (earnings-release exhibit) must be excluded; 2.01 must be included
    responses = iter([
        _resp_with_hits([]),  # S-1 keyword 1
        _resp_with_hits([
            _hit("BadFit Corp  (BFIT)  (CIK 0009)", form="8-K", items=["7.01", "9.01"]),
            _hit("AcquiCo Inc  (ACQI)  (CIK 0010)", form="8-K", items=["2.01"]),
        ]),
        _resp_with_hits([]),  # S-1 keyword 2
        _resp_with_hits([]),  # 8-K keyword 2
    ])
    monkeypatch.setattr(edgar_mod.requests, "get", lambda *a, **kw: next(responses))
    monkeypatch.setattr(edgar_mod.time, "sleep", lambda _: None)

    results = search_new_gaming_filings(known_tickers=set())

    tickers = [r["ticker"] for r in results]
    assert "ACQI" in tickers
    assert "BFIT" not in tickers


# ---------------------------------------------------------------------------
# known ticker excluded
# ---------------------------------------------------------------------------

def test_known_ticker_excluded(monkeypatch):
    responses = iter([
        _resp_with_hits([_hit("KnownCo  (KNOW)  (CIK 0001)")]),
        _resp_with_hits([]),
        _resp_with_hits([]),
        _resp_with_hits([]),
    ])
    monkeypatch.setattr(edgar_mod.requests, "get", lambda *a, **kw: next(responses))
    monkeypatch.setattr(edgar_mod.time, "sleep", lambda _: None)

    results = search_new_gaming_filings(known_tickers={"KNOW"})

    assert results == []


# ---------------------------------------------------------------------------
# deduplication by ticker — later form wins (setdefault keeps first seen)
# ---------------------------------------------------------------------------

def test_dedup_by_ticker_first_seen_wins(monkeypatch):
    # S-1 for DUPL comes first; an 8-K for the same ticker comes later —
    # setdefault means the S-1 entry is kept.
    responses = iter([
        _resp_with_hits([_hit("DuplCo  (DUPL)  (CIK 0001)", form="S-1")]),  # kw1 S-1
        _resp_with_hits([_hit("DuplCo  (DUPL)  (CIK 0001)", form="8-K", items=["2.01"])]),  # kw1 8-K
        _resp_with_hits([]),
        _resp_with_hits([]),
    ])
    monkeypatch.setattr(edgar_mod.requests, "get", lambda *a, **kw: next(responses))
    monkeypatch.setattr(edgar_mod.time, "sleep", lambda _: None)

    results = search_new_gaming_filings(known_tickers=set())

    assert len(results) == 1
    assert results[0]["ticker"] == "DUPL"
    assert results[0]["form"] == "S-1"


# ---------------------------------------------------------------------------
# unparseable display_name skipped
# ---------------------------------------------------------------------------

def test_unparseable_display_name_skipped(monkeypatch):
    # Private filer with no ticker bracket → _parse_hit returns None → skipped
    responses = iter([
        _resp_with_hits([_hit("Private Filer LLC (no ticker here)", form="S-1")]),
        _resp_with_hits([]),
        _resp_with_hits([]),
        _resp_with_hits([]),
    ])
    monkeypatch.setattr(edgar_mod.requests, "get", lambda *a, **kw: next(responses))
    monkeypatch.setattr(edgar_mod.time, "sleep", lambda _: None)

    results = search_new_gaming_filings(known_tickers=set())

    assert results == []


# ---------------------------------------------------------------------------
# network / non-200 → graceful empty list
# ---------------------------------------------------------------------------

def test_network_error_propagates(monkeypatch):
    # _search only catches EdgarFulltextBlocked, not raw ConnectionError —
    # a network error propagates from search_new_gaming_filings.
    import requests as req_lib

    def _boom(*a, **kw):
        raise req_lib.ConnectionError("boom")

    monkeypatch.setattr(edgar_mod.requests, "get", _boom)
    monkeypatch.setattr(edgar_mod.time, "sleep", lambda _: None)

    try:
        search_new_gaming_filings(known_tickers=set())
        assert False, "expected ConnectionError"
    except req_lib.ConnectionError:
        pass


def test_non_200_returns_empty_list(monkeypatch):
    monkeypatch.setattr(edgar_mod.requests, "get", lambda *a, **kw: _FakeResp(status_code=403))
    monkeypatch.setattr(edgar_mod.time, "sleep", lambda _: None)

    results = search_new_gaming_filings(known_tickers=set())

    assert results == []


# ---------------------------------------------------------------------------
# max_results cap
# ---------------------------------------------------------------------------

def test_max_results_respected(monkeypatch):
    # Ticker must match [A-Z.\-]+ — use alphabetic-only tickers
    letters = "ABCDEFGHIJ"
    many_hits = [_hit(f"Co{i}  ({letters[i]}CO)  (CIK 000{i})", form="S-1") for i in range(10)]
    responses = iter([
        _resp_with_hits(many_hits),
        _resp_with_hits([]),
        _resp_with_hits([]),
        _resp_with_hits([]),
    ])
    monkeypatch.setattr(edgar_mod.requests, "get", lambda *a, **kw: next(responses))
    monkeypatch.setattr(edgar_mod.time, "sleep", lambda _: None)

    results = search_new_gaming_filings(known_tickers=set(), max_results=3)

    assert len(results) == 3
