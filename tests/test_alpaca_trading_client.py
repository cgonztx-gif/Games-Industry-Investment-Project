"""
Unit tests for agents/portfolio/alpaca_trading_client.py -- specifically the
re-run idempotency of place_approved_order().

Regression context: place_approved_order() used to place the order at Alpaca
and attach the returned alpaca_order_id, but never moved the row's status off
'approved'. Under the multi-job CI layout a re-dispatched synthesize phase
re-fetches the same still-'approved' orders and would have placed every one of
them at Alpaca a second time (a real double trade). The fix marks the row
status='filled' (+ filled_at) immediately after a successful placement, adds
an alpaca_order_id guard, and sends order_id as Alpaca's client_order_id so
even a crash between placement and the status update cannot double-execute.

No live network / Supabase calls: get_trade_order / mark_trade_order_filled /
requests.post are monkeypatched with in-memory fakes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agents.portfolio.alpaca_trading_client as trading_client
from agents.portfolio.alpaca_trading_client import TradeNotApproved, place_approved_order

_FAKE_DB = "fake-db-sentinel"


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _StatefulOrderStore:
    """In-memory stand-in for the trade_orders row that both get_trade_order
    and mark_trade_order_filled read/write, so a second placement attempt sees
    exactly what a re-run against Supabase would see."""

    def __init__(self, order: dict):
        self.order = dict(order)
        self.mark_calls: list[tuple] = []

    def get_trade_order(self, db, order_id):
        return dict(self.order)

    def mark_trade_order_filled(self, db, order_id, alpaca_order_id=None):
        self.mark_calls.append((order_id, alpaca_order_id))
        self.order["status"] = "filled"
        if alpaca_order_id:
            self.order["alpaca_order_id"] = alpaca_order_id


def _approved_order(order_id="o1"):
    return {
        "order_id": order_id,
        "status": "approved",
        "action": "buy",
        "ticker": "TTWO",
        "size_usd": 1000,
        "alpaca_order_id": None,
    }


def _wire(monkeypatch, store, post_calls):
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
    monkeypatch.setattr(trading_client, "get_trade_order", store.get_trade_order)
    monkeypatch.setattr(trading_client, "mark_trade_order_filled", store.mark_trade_order_filled)

    def fake_post(url, headers=None, json=None, timeout=None):
        post_calls.append({"url": url, "json": json})
        return _FakeResponse({"id": f"alpaca-{json['client_order_id']}"})

    monkeypatch.setattr(trading_client.requests, "post", fake_post)


def test_successful_placement_marks_order_filled(monkeypatch):
    store = _StatefulOrderStore(_approved_order())
    post_calls: list[dict] = []
    _wire(monkeypatch, store, post_calls)

    result = place_approved_order(_FAKE_DB, "o1")

    assert result["id"] == "alpaca-o1"
    assert len(post_calls) == 1
    # The row must leave the 'approved' pool the moment placement succeeds.
    assert store.mark_calls == [("o1", "alpaca-o1")]
    assert store.order["status"] == "filled"


def test_rerun_does_not_double_place(monkeypatch):
    """The exact re-run scenario: place once, then attempt the same order again
    against the post-placement DB state -- the guard must refuse before any
    second Alpaca call."""
    store = _StatefulOrderStore(_approved_order())
    post_calls: list[dict] = []
    _wire(monkeypatch, store, post_calls)

    place_approved_order(_FAKE_DB, "o1")
    with pytest.raises(TradeNotApproved):
        place_approved_order(_FAKE_DB, "o1")

    assert len(post_calls) == 1  # Alpaca was reached exactly once


def test_already_placed_order_is_refused_even_if_still_approved(monkeypatch):
    """If the status update was somehow lost but alpaca_order_id was recorded,
    the alpaca_order_id guard must still refuse a second placement."""
    order = _approved_order()
    order["alpaca_order_id"] = "alpaca-earlier"
    store = _StatefulOrderStore(order)
    post_calls: list[dict] = []
    _wire(monkeypatch, store, post_calls)

    with pytest.raises(TradeNotApproved, match="already placed"):
        place_approved_order(_FAKE_DB, "o1")

    assert post_calls == []
    assert store.mark_calls == []


def test_placement_payload_carries_client_order_id(monkeypatch):
    """order_id doubles as Alpaca's unique client_order_id, so Alpaca itself
    rejects a duplicate even if a crash lands between placement and the status
    update."""
    store = _StatefulOrderStore(_approved_order())
    post_calls: list[dict] = []
    _wire(monkeypatch, store, post_calls)

    place_approved_order(_FAKE_DB, "o1")

    assert post_calls[0]["json"]["client_order_id"] == "o1"


def test_hold_order_places_nothing_and_stays_unfilled(monkeypatch):
    order = _approved_order()
    order["action"] = "hold"
    store = _StatefulOrderStore(order)
    post_calls: list[dict] = []
    _wire(monkeypatch, store, post_calls)

    result = place_approved_order(_FAKE_DB, "o1")

    assert result == {"status": "no_action", "reason": "hold orders are not placed"}
    assert post_calls == []
    assert store.mark_calls == []


def test_failed_placement_leaves_order_approved(monkeypatch):
    """A non-2xx Alpaca response must NOT mark the order filled -- the row has
    to stay 'approved' so a later run can retry it."""
    store = _StatefulOrderStore(_approved_order())
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
    monkeypatch.setattr(trading_client, "get_trade_order", store.get_trade_order)
    monkeypatch.setattr(trading_client, "mark_trade_order_filled", store.mark_trade_order_filled)

    class _FailingResponse:
        def raise_for_status(self):
            raise RuntimeError("alpaca 500")

    monkeypatch.setattr(trading_client.requests, "post", lambda *a, **k: _FailingResponse())

    with pytest.raises(RuntimeError, match="alpaca 500"):
        place_approved_order(_FAKE_DB, "o1")

    assert store.mark_calls == []
    assert store.order["status"] == "approved"
