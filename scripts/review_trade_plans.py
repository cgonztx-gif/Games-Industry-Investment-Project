"""
Trade Plan Review CLI - Phase 5

Human-in-the-loop approval step between agents/portfolio/manager.py (writes
`trade_plans` / `trade_orders` rows at status='pending') and
agents/portfolio/execution_agent.py (only acts on `trade_orders` rows where
status='approved', via database/db_client.get_approved_trade_orders() then
agents/portfolio/alpaca_trading_client.place_approved_order(), which re-checks
status == 'approved' itself as the hard safety guard).

This script only ever updates `status` in Supabase (approved/rejected). It
never calls Alpaca and never places an order -- that remains
execution_agent.py's job, unchanged.

Granularity:
  - `approve --plan <plan_id>` / `reject --plan <plan_id>` sets the plan's own
    status AND cascades the same status to every one of its currently-pending
    trade_orders rows (bulk approve/reject -- approving a plan means approving
    everything Claude proposed in it).
  - `approve --order <order_id>` / `reject --order <order_id>` sets a single
    order's status only, without touching the parent plan's own status
    (finer-grained per-order override).

Usage:
    python scripts/review_trade_plans.py list
    python scripts/review_trade_plans.py approve --plan <plan_id>
    python scripts/review_trade_plans.py reject --plan <plan_id>
    python scripts/review_trade_plans.py approve --order <order_id>
    python scripts/review_trade_plans.py reject --order <order_id>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

from database.db_client import (
    get_client,
    get_pending_trade_plans,
    get_trade_order,
    get_trade_orders_for_plan,
    set_trade_order_status,
    set_trade_plan_status,
)

_RATIONALE_TRUNCATE = 300


class ReviewError(Exception):
    """User-facing validation/not-found error -- printed cleanly, no raw traceback."""


def _truncate(text: str | None, limit: int = _RATIONALE_TRUNCATE) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


# ---------------------------------------------------------------------------
# `list` command
# ---------------------------------------------------------------------------

def build_listing(plans: list[dict], orders_by_plan: dict[str, list[dict]]) -> str:
    """Pure formatting -- no DB/IO -- so it's directly testable."""
    if not plans:
        return "No pending trade plans."

    lines: list[str] = []
    for plan in plans:
        plan_id = plan.get("plan_id")
        lines.append("=" * 70)
        lines.append(f"plan_id : {plan_id}")
        lines.append(f"week_of : {plan.get('week_of')}")
        lines.append(f"rationale: {_truncate(plan.get('claude_rationale'))}")

        pending_orders = [
            o for o in orders_by_plan.get(plan_id, []) if o.get("status") == "pending"
        ]
        if not pending_orders:
            lines.append("  (no pending orders under this plan)")
        else:
            lines.append("  pending orders:")
            for order in pending_orders:
                lines.append(
                    f"    order_id={order.get('order_id')}  ticker={order.get('ticker')}  "
                    f"action={order.get('action')}  size_usd={order.get('size_usd')}"
                )
    lines.append("=" * 70)
    return "\n".join(lines)


def list_pending(
    db,
    get_pending_plans_fn=get_pending_trade_plans,
    get_orders_fn=get_trade_orders_for_plan,
) -> str:
    plans = get_pending_plans_fn(db)
    orders_by_plan = {
        plan["plan_id"]: get_orders_fn(db, plan["plan_id"]) for plan in plans
    }
    return build_listing(plans, orders_by_plan)


# ---------------------------------------------------------------------------
# `approve` / `reject` orchestration
# ---------------------------------------------------------------------------

def _apply_plan_status(
    db,
    plan_id: str,
    status: str,
    get_pending_plans_fn=get_pending_trade_plans,
    get_orders_fn=get_trade_orders_for_plan,
    set_plan_status_fn=set_trade_plan_status,
    set_order_status_fn=set_trade_order_status,
) -> dict:
    """
    Set one plan's status and cascade to all its currently-pending orders.
    Raises ReviewError if the plan doesn't exist or isn't currently pending.
    """
    plans = get_pending_plans_fn(db)
    match = next((p for p in plans if p.get("plan_id") == plan_id), None)
    if match is None:
        raise ReviewError(f"trade plan {plan_id!r} not found or not pending")

    orders = get_orders_fn(db, plan_id)
    pending_orders = [o for o in orders if o.get("status") == "pending"]

    set_plan_status_fn(db, plan_id, status)
    for order in pending_orders:
        set_order_status_fn(db, order["order_id"], status)

    return {"plan_id": plan_id, "status": status, "orders_updated": len(pending_orders)}


def approve_plan(
    db,
    plan_id: str,
    get_pending_plans_fn=get_pending_trade_plans,
    get_orders_fn=get_trade_orders_for_plan,
    set_plan_status_fn=set_trade_plan_status,
    set_order_status_fn=set_trade_order_status,
) -> dict:
    return _apply_plan_status(
        db,
        plan_id,
        "approved",
        get_pending_plans_fn=get_pending_plans_fn,
        get_orders_fn=get_orders_fn,
        set_plan_status_fn=set_plan_status_fn,
        set_order_status_fn=set_order_status_fn,
    )


def reject_plan(
    db,
    plan_id: str,
    get_pending_plans_fn=get_pending_trade_plans,
    get_orders_fn=get_trade_orders_for_plan,
    set_plan_status_fn=set_trade_plan_status,
    set_order_status_fn=set_trade_order_status,
) -> dict:
    return _apply_plan_status(
        db,
        plan_id,
        "rejected",
        get_pending_plans_fn=get_pending_plans_fn,
        get_orders_fn=get_orders_fn,
        set_plan_status_fn=set_plan_status_fn,
        set_order_status_fn=set_order_status_fn,
    )


def _apply_order_status(
    db,
    order_id: str,
    status: str,
    get_order_fn=get_trade_order,
    set_order_status_fn=set_trade_order_status,
) -> dict:
    """
    Set a single order's status without touching its parent plan. Raises
    ReviewError if the order doesn't exist or isn't currently pending.

    order_id is user-supplied and may not even be a well-formed UUID -- the
    underlying Supabase/PostgREST query raises a raw APIError in that case
    rather than returning an empty result, so that's caught here and folded
    into the same clean "not found" message instead of propagating.
    """
    try:
        order = get_order_fn(db, order_id)
    except Exception:
        order = None
    if order is None or order.get("status") != "pending":
        raise ReviewError(f"trade order {order_id!r} not found or not pending")

    set_order_status_fn(db, order_id, status)
    return {"order_id": order_id, "status": status}


def approve_order(
    db,
    order_id: str,
    get_order_fn=get_trade_order,
    set_order_status_fn=set_trade_order_status,
) -> dict:
    return _apply_order_status(
        db, order_id, "approved", get_order_fn=get_order_fn, set_order_status_fn=set_order_status_fn
    )


def reject_order(
    db,
    order_id: str,
    get_order_fn=get_trade_order,
    set_order_status_fn=set_trade_order_status,
) -> dict:
    return _apply_order_status(
        db, order_id, "rejected", get_order_fn=get_order_fn, set_order_status_fn=set_order_status_fn
    )


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Review pending trade plans/orders and approve or reject them."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "list", help="List all pending trade plans and their pending orders."
    )

    for command in ("approve", "reject"):
        sub = subparsers.add_parser(
            command, help=f"{command.capitalize()} a trade plan or a single order."
        )
        group = sub.add_mutually_exclusive_group(required=True)
        group.add_argument(
            "--plan",
            metavar="PLAN_ID",
            help=f"Plan ID to {command} (cascades to its currently-pending orders).",
        )
        group.add_argument(
            "--order",
            metavar="ORDER_ID",
            help=f"Single order ID to {command} (does not touch the parent plan).",
        )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    db = get_client()

    if args.command == "list":
        print(list_pending(db))
        return 0

    try:
        if args.plan:
            result = (approve_plan if args.command == "approve" else reject_plan)(db, args.plan)
            print(
                f"Plan {result['plan_id']} set to '{result['status']}'; "
                f"{result['orders_updated']} order(s) cascaded to '{result['status']}'."
            )
        else:
            result = (approve_order if args.command == "approve" else reject_order)(db, args.order)
            print(f"Order {result['order_id']} set to '{result['status']}'.")
    except ReviewError as exc:
        print(f"ERROR: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
