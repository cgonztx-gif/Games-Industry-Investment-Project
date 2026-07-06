"""
Unit tests for agents/portfolio/execution_agent.py (Agent SDK + guarded MCP tool).

No live network / Alpaca / Supabase / CLI calls. run() takes injectable ``db`` /
``get_approved_fn`` / ``place_fn``, so every test drives the deterministic dispatch
loop and the guarded MCP tool boundary with fakes. One test uses the *real*
``place_approved_order`` (with a patched Supabase read) to prove the
``status='approved'`` guard fires through the MCP tool and rejects a non-approved
order without ever reaching Alpaca.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agents.portfolio.alpaca_trading_client as trading_client
from agents.portfolio import execution_agent
from agents.portfolio.alpaca_trading_client import (
    TradeNotApproved,
    place_approved_order,
)

_FAKE_DB = "fake-db-sentinel"  # only passed through to injected fakes


def _approved(*order_ids):
    return lambda db: [{"order_id": oid} for oid in order_ids]


def test_approved_orders_get_placed():
    placed_ids = []

    def fake_place(db, order_id):
        placed_ids.append(order_id)
        return {"id": f"alpaca-{order_id}"}

    result = execution_agent.run(
        db=_FAKE_DB,
        get_approved_fn=_approved("o1", "o2"),
        place_fn=fake_place,
    )

    assert placed_ids == ["o1", "o2"]
    assert result == {
        "orders_checked": 2,
        "orders_placed": 2,
        "error_count": 0,
        "placed": [
            {"order_id": "o1", "alpaca_order_id": "alpaca-o1"},
            {"order_id": "o2", "alpaca_order_id": "alpaca-o2"},
        ],
        "errors": [],
    }


def test_no_approved_orders_is_a_clean_noop():
    result = execution_agent.run(
        db=_FAKE_DB,
        get_approved_fn=_approved(),  # nothing approved
        place_fn=lambda db, oid: (_ for _ in ()).throw(
            AssertionError("place must not be called")
        ),
    )
    assert result == {
        "orders_checked": 0,
        "orders_placed": 0,
        "error_count": 0,
        "placed": [],
        "errors": [],
    }


def test_individual_failure_does_not_stop_batch():
    def fake_place(db, order_id):
        if order_id == "o1":
            raise RuntimeError("alpaca 500")
        return {"id": f"alpaca-{order_id}"}

    result = execution_agent.run(
        db=_FAKE_DB,
        get_approved_fn=_approved("o1", "o2"),
        place_fn=fake_place,
    )

    assert result["orders_checked"] == 2
    assert result["orders_placed"] == 1
    assert result["error_count"] == 1
    assert result["placed"] == [{"order_id": "o2", "alpaca_order_id": "alpaca-o2"}]
    assert result["errors"][0]["order_id"] == "o1"
    assert "alpaca 500" in result["errors"][0]["error"]


def test_guard_rejects_non_approved_order(monkeypatch):
    """
    Route the REAL place_approved_order through the execution agent's guarded MCP
    tool, but have the Supabase read return a 'pending' order. The in-tool guard
    must raise TradeNotApproved (before any Alpaca call), and the agent must
    aggregate it as an error -- proving status='approved' is enforced end-to-end.
    """
    monkeypatch.setattr(
        trading_client,
        "get_trade_order",
        lambda db, order_id: {
            "order_id": order_id,
            "status": "pending",  # NOT approved
            "action": "buy",
            "ticker": "TTWO",
            "size_usd": 100,
        },
    )
    # Guard rejects before any network call; if it ever reached Alpaca this would blow up.
    monkeypatch.setattr(
        trading_client.requests,
        "post",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("guard bypassed: reached Alpaca for a non-approved order")
        ),
    )

    result = execution_agent.run(
        db=_FAKE_DB,
        get_approved_fn=_approved("o1"),
        place_fn=place_approved_order,  # the real guarded function
    )

    assert result["orders_placed"] == 0
    assert result["error_count"] == 1
    assert "expected 'approved'" in result["errors"][0]["error"]


def test_guard_raises_tradenotapproved_directly(monkeypatch):
    """Belt-and-suspenders: the guard itself raises TradeNotApproved for a
    non-approved order (independent of the agent aggregation)."""
    monkeypatch.setattr(
        trading_client,
        "get_trade_order",
        lambda db, order_id: {"order_id": order_id, "status": "pending", "action": "buy"},
    )
    try:
        place_approved_order(_FAKE_DB, "o1")
        assert False, "expected TradeNotApproved"
    except TradeNotApproved:
        pass
