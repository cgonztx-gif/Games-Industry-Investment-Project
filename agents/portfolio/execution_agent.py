"""
Execution Agent - Phase 5 (Claude Agent SDK + MCP migration)

Places approved Alpaca paper-trading orders through a *guarded* MCP tool surface.

Design decision -- deterministic dispatch, no LLM reasoning step:

The pre-migration runner was a plain Python loop: "for each approved order, place
it." There is no judgment call in that loop -- which orders to place is already
decided (a human approved them via scripts/review_trade_plans.py), and how to place
one is fixed. Wrapping that in an LLM tool-use loop would add nothing but
nondeterminism and cost to a safety-critical path: an LLM could skip an approved
order, place one twice, or hallucinate an argument. So this agent keeps the
deterministic Python iteration and uses MCP only for the *tool-call boundary* --
it invokes the guarded ``place_approved_order`` MCP tool (agents/portfolio/
alpaca_mcp.py) in-process instead of calling the raw Python function directly.

How this satisfies the two non-negotiable CLAUDE.md rules:

- Rule 4 ("Execution subagent has Alpaca tools only -- tool restriction is the
  primary safety guardrail"): the agent's entire tool surface is the custom
  ``alpaca-execution-guarded`` MCP server, whose only tools are
  ``get_approved_orders`` and ``place_approved_order``. It never sees Alpaca's bare
  order API or any other tool. (And with no LLM in the loop, no model can be
  prompt-injected into calling anything at all.)
- Rule 5 ("All trade execution requires status='approved', enforced inside the
  order-placement tool"): the ``place_approved_order`` MCP tool wraps the existing
  ``alpaca_trading_client.place_approved_order()``, which re-reads the order's
  Supabase status and raises ``TradeNotApproved`` unless it is exactly
  ``'approved'``. The guard is reused verbatim and runs on every placement,
  whether the tool is called by this deterministic loop or (hypothetically) by an
  LLM later.

Model: none. Because there is no LLM call, no model-tier entry applies (this keeps
CLAUDE.md's "execution agents make no direct LLM calls" audit line true). If a
reasoning step were ever added here, Sonnet-class (claude-sonnet-4-6) would be the
correct tier -- thin tool execution, analogous to the data workers, not Opus-class
synthesis/portfolio reasoning.

The return shape is unchanged from the pre-migration ``run()``:
``orders_checked`` / ``orders_placed`` / ``error_count`` / ``placed`` / ``errors``.
"""

from __future__ import annotations

import asyncio
import json

from agents.portfolio.alpaca_mcp import build_execution_server
from agents.portfolio.alpaca_trading_client import place_approved_order
from database.db_client import get_approved_trade_orders, get_client


async def _run_async(db, get_approved_fn, place_fn) -> dict:
    # Build the guarded MCP server once; invoke its handlers in-process (no LLM).
    _server, tools = build_execution_server(
        db, place_fn=place_fn, get_approved_fn=get_approved_fn
    )
    get_approved_tool = tools["get_approved_orders"]
    place_tool = tools["place_approved_order"]

    approved_result = await get_approved_tool.handler({})
    approved = json.loads(approved_result["content"][0]["text"])

    placed = []
    errors = []
    for trade in approved:
        order_id = trade["order_id"]
        result = await place_tool.handler({"order_id": order_id})
        text = result["content"][0]["text"]
        if result.get("is_error"):
            # Guard rejections (TradeNotApproved) and Alpaca/API failures both land
            # here; one bad order never stops the batch.
            errors.append({"order_id": order_id, "error": text.removeprefix("ERROR: ")})
        else:
            payload = json.loads(text)
            placed.append(
                {"order_id": order_id, "alpaca_order_id": payload.get("alpaca_order_id")}
            )

    return {
        "orders_checked": len(approved),
        "orders_placed": len(placed),
        "error_count": len(errors),
        "placed": placed,
        "errors": errors,
    }


def run(
    db=None,
    get_approved_fn=get_approved_trade_orders,
    place_fn=place_approved_order,
) -> dict:
    """
    Fetch approved orders and place each through the guarded MCP tool.

    Injectable ``db`` / ``get_approved_fn`` / ``place_fn`` (defaults are the real
    Supabase client and guarded placement function) let tests exercise the batch
    logic, the guard, and per-order failure isolation with no live network/API
    calls. Wraps an async core in ``asyncio.run`` so callers keep the same
    synchronous ``run()`` -> dict signature as before.
    """
    active_db = db if db is not None else get_client()
    return asyncio.run(_run_async(active_db, get_approved_fn, place_fn))
