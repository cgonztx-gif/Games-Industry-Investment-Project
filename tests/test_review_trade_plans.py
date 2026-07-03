"""
Unit tests for scripts/review_trade_plans.py.

No live Supabase client: every DB touch point (get_pending_trade_plans,
get_trade_orders_for_plan, get_trade_order, set_trade_plan_status,
set_trade_order_status) is injectable, so these tests use fakes with scripted
behavior -- same dependency-injection style as tests/test_portfolio_manager.py
and tests/test_synthesis_deep_dive_dispatch.py. The `db` argument itself is
never a real Supabase client here; it's a sentinel passed through to fakes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.review_trade_plans import (
    ReviewError,
    approve_order,
    approve_plan,
    build_arg_parser,
    build_listing,
    list_pending,
    reject_order,
    reject_plan,
)

_FAKE_DB = "fake-db-sentinel"


def _plan(plan_id="plan-1", week_of="2026-06-29", rationale="Buy TTWO on convergence."):
    return {
        "plan_id": plan_id,
        "week_of": week_of,
        "claude_rationale": rationale,
        "status": "pending",
    }


def _order(order_id, plan_id="plan-1", ticker="TTWO", action="buy", size_usd=3000.0, status="pending"):
    return {
        "order_id": order_id,
        "plan_id": plan_id,
        "ticker": ticker,
        "action": action,
        "size_usd": size_usd,
        "status": status,
    }


class FakeStore:
    """Records status-setting calls and serves plan/order fetches from in-memory state."""

    def __init__(self, plans=None, orders=None):
        self.plans = {p["plan_id"]: dict(p) for p in (plans or [])}
        self.orders = {o["order_id"]: dict(o) for o in (orders or [])}
        self.plan_status_calls: list[tuple] = []
        self.order_status_calls: list[tuple] = []

    def get_pending_trade_plans(self, db):
        return [dict(p) for p in self.plans.values() if p["status"] == "pending"]

    def get_trade_orders_for_plan(self, db, plan_id):
        return [dict(o) for o in self.orders.values() if o["plan_id"] == plan_id]

    def get_trade_order(self, db, order_id):
        order = self.orders.get(order_id)
        return dict(order) if order else None

    def set_trade_plan_status(self, db, plan_id, status):
        self.plan_status_calls.append((plan_id, status))
        self.plans[plan_id]["status"] = status

    def set_trade_order_status(self, db, order_id, status):
        self.order_status_calls.append((order_id, status))
        self.orders[order_id]["status"] = status


# ---------------------------------------------------------------------------
# argparse: --plan / --order mutual exclusivity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("command", ["approve", "reject"])
def test_plan_and_order_are_mutually_exclusive(command):
    parser = build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([command, "--plan", "plan-1", "--order", "order-1"])


@pytest.mark.parametrize("command", ["approve", "reject"])
def test_plan_or_order_is_required(command):
    parser = build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([command])


@pytest.mark.parametrize("command", ["approve", "reject"])
def test_plan_only_parses_cleanly(command):
    parser = build_arg_parser()
    args = parser.parse_args([command, "--plan", "plan-1"])
    assert args.plan == "plan-1"
    assert args.order is None


@pytest.mark.parametrize("command", ["approve", "reject"])
def test_order_only_parses_cleanly(command):
    parser = build_arg_parser()
    args = parser.parse_args([command, "--order", "order-1"])
    assert args.order == "order-1"
    assert args.plan is None


def test_list_command_parses():
    parser = build_arg_parser()
    args = parser.parse_args(["list"])
    assert args.command == "list"


# ---------------------------------------------------------------------------
# approve_plan / reject_plan: cascade to pending orders only
# ---------------------------------------------------------------------------

def test_approve_plan_cascades_to_all_pending_orders():
    store = FakeStore(
        plans=[_plan("plan-1")],
        orders=[
            _order("order-1", plan_id="plan-1", status="pending"),
            _order("order-2", plan_id="plan-1", status="pending"),
        ],
    )

    result = approve_plan(
        _FAKE_DB,
        "plan-1",
        get_pending_plans_fn=store.get_pending_trade_plans,
        get_orders_fn=store.get_trade_orders_for_plan,
        set_plan_status_fn=store.set_trade_plan_status,
        set_order_status_fn=store.set_trade_order_status,
    )

    assert result == {"plan_id": "plan-1", "status": "approved", "orders_updated": 2}
    assert store.plan_status_calls == [("plan-1", "approved")]
    assert set(store.order_status_calls) == {("order-1", "approved"), ("order-2", "approved")}
    assert store.plans["plan-1"]["status"] == "approved"
    assert store.orders["order-1"]["status"] == "approved"
    assert store.orders["order-2"]["status"] == "approved"


def test_approve_plan_does_not_touch_already_reviewed_orders():
    """An order already approved/rejected out-of-band shouldn't be re-touched by a plan cascade."""
    store = FakeStore(
        plans=[_plan("plan-1")],
        orders=[
            _order("order-1", plan_id="plan-1", status="pending"),
            _order("order-2", plan_id="plan-1", status="rejected"),
        ],
    )

    result = approve_plan(
        _FAKE_DB,
        "plan-1",
        get_pending_plans_fn=store.get_pending_trade_plans,
        get_orders_fn=store.get_trade_orders_for_plan,
        set_plan_status_fn=store.set_trade_plan_status,
        set_order_status_fn=store.set_trade_order_status,
    )

    assert result["orders_updated"] == 1
    assert store.order_status_calls == [("order-1", "approved")]
    assert store.orders["order-2"]["status"] == "rejected"  # untouched


def test_reject_plan_cascades_rejected_status():
    store = FakeStore(
        plans=[_plan("plan-1")],
        orders=[_order("order-1", plan_id="plan-1", status="pending")],
    )

    result = reject_plan(
        _FAKE_DB,
        "plan-1",
        get_pending_plans_fn=store.get_pending_trade_plans,
        get_orders_fn=store.get_trade_orders_for_plan,
        set_plan_status_fn=store.set_trade_plan_status,
        set_order_status_fn=store.set_trade_order_status,
    )

    assert result == {"plan_id": "plan-1", "status": "rejected", "orders_updated": 1}
    assert store.plan_status_calls == [("plan-1", "rejected")]
    assert store.order_status_calls == [("order-1", "rejected")]


def test_approve_plan_not_found_raises_review_error():
    store = FakeStore(plans=[], orders=[])

    with pytest.raises(ReviewError):
        approve_plan(
            _FAKE_DB,
            "does-not-exist",
            get_pending_plans_fn=store.get_pending_trade_plans,
            get_orders_fn=store.get_trade_orders_for_plan,
            set_plan_status_fn=store.set_trade_plan_status,
            set_order_status_fn=store.set_trade_order_status,
        )
    assert store.plan_status_calls == []
    assert store.order_status_calls == []


def test_approve_plan_already_reviewed_raises_review_error():
    """A plan that exists but is already approved/rejected is not in the pending list."""
    store = FakeStore(plans=[_plan("plan-1")], orders=[])
    store.plans["plan-1"]["status"] = "approved"  # simulate already-reviewed

    with pytest.raises(ReviewError):
        approve_plan(
            _FAKE_DB,
            "plan-1",
            get_pending_plans_fn=store.get_pending_trade_plans,
            get_orders_fn=store.get_trade_orders_for_plan,
            set_plan_status_fn=store.set_trade_plan_status,
            set_order_status_fn=store.set_trade_order_status,
        )


# ---------------------------------------------------------------------------
# approve_order / reject_order: single order only, plan untouched
# ---------------------------------------------------------------------------

def test_approve_order_does_not_touch_plan_status():
    store = FakeStore(
        plans=[_plan("plan-1")],
        orders=[_order("order-1", plan_id="plan-1", status="pending")],
    )

    result = approve_order(
        _FAKE_DB,
        "order-1",
        get_order_fn=store.get_trade_order,
        set_order_status_fn=store.set_trade_order_status,
    )

    assert result == {"order_id": "order-1", "status": "approved"}
    assert store.order_status_calls == [("order-1", "approved")]
    assert store.plan_status_calls == []  # plan itself is never touched
    assert store.plans["plan-1"]["status"] == "pending"  # unchanged


def test_reject_order_does_not_touch_plan_status():
    store = FakeStore(
        plans=[_plan("plan-1")],
        orders=[_order("order-1", plan_id="plan-1", status="pending")],
    )

    result = reject_order(
        _FAKE_DB,
        "order-1",
        get_order_fn=store.get_trade_order,
        set_order_status_fn=store.set_trade_order_status,
    )

    assert result == {"order_id": "order-1", "status": "rejected"}
    assert store.plan_status_calls == []
    assert store.plans["plan-1"]["status"] == "pending"


def test_approve_order_not_found_raises_review_error():
    store = FakeStore(plans=[], orders=[])

    with pytest.raises(ReviewError):
        approve_order(
            _FAKE_DB,
            "does-not-exist",
            get_order_fn=store.get_trade_order,
            set_order_status_fn=store.set_trade_order_status,
        )
    assert store.order_status_calls == []


def test_approve_order_get_order_fn_exception_raises_review_error_not_raw_traceback():
    """
    A malformed order_id (e.g. not a well-formed UUID) makes the real
    Supabase/PostgREST query raise a raw APIError rather than returning None.
    That must still surface as a clean ReviewError, not propagate.
    """
    store = FakeStore(plans=[], orders=[])

    def _boom(db, order_id):
        raise RuntimeError("invalid input syntax for type uuid")

    with pytest.raises(ReviewError):
        approve_order(
            _FAKE_DB,
            "not-a-real-id",
            get_order_fn=_boom,
            set_order_status_fn=store.set_trade_order_status,
        )
    assert store.order_status_calls == []


def test_approve_order_already_reviewed_raises_review_error():
    store = FakeStore(
        plans=[_plan("plan-1")],
        orders=[_order("order-1", plan_id="plan-1", status="approved")],
    )

    with pytest.raises(ReviewError):
        approve_order(
            _FAKE_DB,
            "order-1",
            get_order_fn=store.get_trade_order,
            set_order_status_fn=store.set_trade_order_status,
        )
    assert store.order_status_calls == []


# ---------------------------------------------------------------------------
# list_pending / build_listing
# ---------------------------------------------------------------------------

def test_build_listing_empty():
    assert build_listing([], {}) == "No pending trade plans."


def test_build_listing_includes_plan_and_pending_orders_only():
    plans = [_plan("plan-1", rationale="Short rationale.")]
    orders_by_plan = {
        "plan-1": [
            _order("order-1", status="pending"),
            _order("order-2", status="approved"),  # already reviewed -- should not show
        ]
    }

    text = build_listing(plans, orders_by_plan)

    assert "plan-1" in text
    assert "order-1" in text
    assert "order-2" not in text  # not pending, so excluded from the listing


def test_build_listing_truncates_long_rationale():
    long_rationale = "x" * 500
    plans = [_plan("plan-1", rationale=long_rationale)]

    text = build_listing(plans, {})

    assert "..." in text
    assert "x" * 500 not in text


def test_list_pending_wires_plans_to_their_orders():
    store = FakeStore(
        plans=[_plan("plan-1"), _plan("plan-2")],
        orders=[
            _order("order-1", plan_id="plan-1", status="pending"),
            _order("order-2", plan_id="plan-2", status="pending"),
        ],
    )

    text = list_pending(
        _FAKE_DB,
        get_pending_plans_fn=store.get_pending_trade_plans,
        get_orders_fn=store.get_trade_orders_for_plan,
    )

    assert "plan-1" in text
    assert "order-1" in text
    assert "plan-2" in text
    assert "order-2" in text
