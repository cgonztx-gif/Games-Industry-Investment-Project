"""
Unit tests for agents/workers/financial_overlay/alpaca_data_client.py.

No live network calls: requests.get is monkeypatched. Exercises the
unconfigured-credentials short-circuit and both documented bars shapes
(nested-per-symbol vs. flat) plus missing/empty payloads.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agents.workers.financial_overlay.alpaca_data_client as adc
from agents.workers.financial_overlay.alpaca_data_client import (
    get_historical_close,
    get_latest_price,
)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _with_creds(monkeypatch, payload):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.setattr(adc.requests, "get", lambda *a, **kw: _FakeResponse(payload))


def _without_creds(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)

    def _boom(*a, **kw):
        raise AssertionError("network must not be touched without credentials")

    monkeypatch.setattr(adc.requests, "get", _boom)


def test_latest_price_returns_none_without_credentials(monkeypatch):
    _without_creds(monkeypatch)
    assert get_latest_price("SPY") is None


def test_latest_price_parses_trade(monkeypatch):
    _with_creds(monkeypatch, {"trade": {"p": "512.34"}})
    assert get_latest_price("SPY") == 512.34


def test_latest_price_missing_trade_returns_none(monkeypatch):
    _with_creds(monkeypatch, {"trade": None})
    assert get_latest_price("SPY") is None


def test_historical_close_returns_none_without_credentials(monkeypatch):
    _without_creds(monkeypatch)
    assert get_historical_close("SPY", "2026-07-10") is None


def test_historical_close_nested_per_symbol_shape(monkeypatch):
    _with_creds(monkeypatch, {"bars": {"SPY": [{"c": 501.5}]}})
    assert get_historical_close("SPY", "2026-07-10") == 501.5


def test_historical_close_flat_list_shape(monkeypatch):
    _with_creds(monkeypatch, {"bars": [{"c": 499.0}]})
    assert get_historical_close("SPY", "2026-07-10") == 499.0


def test_historical_close_empty_or_missing_bars_returns_none(monkeypatch):
    _with_creds(monkeypatch, {"bars": {}})
    assert get_historical_close("SPY", "2026-07-10") is None
    _with_creds(monkeypatch, {"bars": {"SPY": []}})
    assert get_historical_close("SPY", "2026-07-10") is None
    _with_creds(monkeypatch, {})
    assert get_historical_close("SPY", "2026-07-10") is None


def test_historical_close_null_close_returns_none(monkeypatch):
    _with_creds(monkeypatch, {"bars": {"SPY": [{"c": None}]}})
    assert get_historical_close("SPY", "2026-07-10") is None
