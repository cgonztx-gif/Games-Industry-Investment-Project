"""
Alpaca MCP servers for the Phase 5 portfolio agents (Claude Agent SDK migration).

Why a *custom* in-process SDK MCP server instead of the official alpacahq
`alpaca-mcp-server`:

- The official server exposes ~65 raw Trading/Market-Data tools, including a bare
  `place_order` that talks straight to Alpaca's REST API. Handing that tool to an
  LLM tool-use loop would let the model place an order without the Supabase
  `status = 'approved'` re-check running -- exactly the bypass CLAUDE.md rules 4
  ("Execution subagent has Alpaca tools only -- tool restriction is the primary
  safety guardrail") and 5 ("All trade execution requires status = 'approved',
  enforced inside the order-placement tool") forbid. We must NOT expose Alpaca's
  bare order API to any agent.
- The official server also needs `uvx`/Node tooling and its own credential/init
  flow, which is awkward to stand up (and pointless) for the two narrow tool
  surfaces these agents actually need.

So this module builds two minimal `create_sdk_mcp_server` servers whose tools wrap
the *existing* `alpaca_trading_client` functions -- the guard logic in
`place_approved_order()` is reused verbatim, never re-implemented and never
bypassed:

- ``build_readonly_account_server`` -- one read-only ``get_account_state`` tool for
  the Portfolio Manager (account cash / buying power / positions). Read-only, so
  no approval concern.
- ``build_execution_server`` -- the guarded ``get_approved_orders`` and
  ``place_approved_order`` tools for the Execution Agent. The placement tool calls
  ``alpaca_trading_client.place_approved_order()``, which re-reads Supabase status
  and raises ``TradeNotApproved`` unless it is exactly ``'approved'``. This is the
  ONLY order-placement tool either agent is allowed to see, and it cannot place an
  unapproved order.

These servers are plain in-process config objects (`create_sdk_mcp_server` returns
a dict, and `@tool` handlers are ordinary awaitables) -- constructing them has zero
side effects and makes no network/CLI calls, so they are safe to build inside unit
tests and to invoke handler-by-handler without spawning the Agent SDK transport.
"""

from __future__ import annotations

import json

from claude_agent_sdk import create_sdk_mcp_server, tool

from agents.portfolio.alpaca_trading_client import (
    get_account_state,
    place_approved_order,
)
from database.db_client import get_approved_trade_orders

# Server names. The Agent SDK exposes each tool to an LLM as
# ``mcp__<servername>__<toolname>``; the Portfolio Manager's ``allowed_tools`` is
# pinned to exactly the read-only server's tool name (see manager.py).
READONLY_SERVER_NAME = "alpaca-readonly"
EXECUTION_SERVER_NAME = "alpaca-execution-guarded"

_ACCOUNT_UNAVAILABLE_NOTE = (
    "UNAVAILABLE -- Alpaca account state could not be fetched ({reason}). "
    "Current positions, cash, and buying power cannot be verified. Build this "
    "plan conservatively: do not assume any existing position or cash balance, "
    "keep target weights modest, favor 'watch' over 'buy' for anything whose "
    "sizing depends on unverifiable current exposure, and note this limitation "
    "in the relevant rationale fields."
)


def account_state_text(get_account_state_fn=get_account_state) -> str:
    """
    Render current Alpaca account state as the text a tool result should carry.

    Graceful degradation lives here rather than in the caller: on any failure
    (missing credentials, network error, non-2xx) we return the
    ``_ACCOUNT_UNAVAILABLE_NOTE`` string instead of raising, so an LLM that calls
    the ``get_account_state`` tool still receives an actionable instruction ("build
    conservatively") rather than a hard error. This preserves the exact
    graceful-degradation behaviour the pre-migration Portfolio Manager had when it
    pre-fetched account state into the prompt -- only now the note is delivered
    through the tool-result channel.
    """
    try:
        state = get_account_state_fn()
        return json.dumps(state, indent=2, default=str)
    except Exception as exc:  # noqa: BLE001 -- intentionally broad; see docstring
        return _ACCOUNT_UNAVAILABLE_NOTE.format(reason=exc)


def build_readonly_account_server(get_account_state_fn=get_account_state):
    """
    Build the read-only account MCP server for the Portfolio Manager.

    Returns ``(server_config, allowed_tools)`` where ``allowed_tools`` is the exact
    one-element allowlist the manager pins so the LLM can pull live account/position
    state itself and touch nothing else.
    """

    @tool(
        "get_account_state",
        "Fetch the current Alpaca paper-trading account state: cash, buying "
        "power, portfolio value, and open positions. Read-only. Call this once "
        "before sizing positions. If it returns an 'UNAVAILABLE' note, build the "
        "plan conservatively as that note instructs.",
        {},
    )
    async def get_account_state_tool(args):  # noqa: ANN001, ARG001
        return {
            "content": [
                {"type": "text", "text": account_state_text(get_account_state_fn)}
            ]
        }

    server = create_sdk_mcp_server(
        name=READONLY_SERVER_NAME,
        version="1.0.0",
        tools=[get_account_state_tool],
    )
    allowed_tools = [f"mcp__{READONLY_SERVER_NAME}__get_account_state"]
    return server, allowed_tools


def build_execution_server(
    db,
    place_fn=place_approved_order,
    get_approved_fn=get_approved_trade_orders,
):
    """
    Build the guarded execution MCP server for the Execution Agent.

    Two tools, and nothing else:

    - ``get_approved_orders`` -- read the ``trade_orders`` rows currently at
      ``status = 'approved'``.
    - ``place_approved_order`` -- place ONE order by ``order_id`` via the existing
      ``place_approved_order()``, which re-reads Supabase status and refuses
      (``TradeNotApproved``) anything not exactly ``'approved'``. The guard is NOT
      re-implemented here; this tool is a thin, restricted wrapper so the guard
      cannot be skipped.

    Returns ``(server_config, tools_by_name)``. ``tools_by_name`` maps each tool's
    bare name to its ``SdkMcpTool`` so the deterministic Execution Agent can invoke
    the guarded handlers in-process (``tool.handler(args)``) without an LLM -- see
    execution_agent.py for why an LLM reasoning step is deliberately omitted on the
    order-placement path.

    The placement handler returns machine-parseable JSON text:
    ``{"ok": true, "alpaca_order_id": ...}`` on success, or an ``is_error`` result
    carrying the failure message (including guard rejections) so the caller can
    aggregate results without exceptions crossing the tool boundary.
    """

    @tool(
        "get_approved_orders",
        "Return all trade_orders rows currently at status='approved'. Read-only.",
        {},
    )
    async def get_approved_orders_tool(args):  # noqa: ANN001, ARG001
        approved = get_approved_fn(db)
        return {
            "content": [{"type": "text", "text": json.dumps(approved, default=str)}]
        }

    @tool(
        "place_approved_order",
        "Place ONE Alpaca paper order by order_id. Re-reads the order's Supabase "
        "status and refuses to place anything that is not exactly 'approved'. This "
        "is the only order-placement tool available and the approval guard cannot "
        "be bypassed.",
        {"order_id": str},
    )
    async def place_approved_order_tool(args):  # noqa: ANN001
        order_id = args["order_id"]
        try:
            order = place_fn(db, order_id)
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {"ok": True, "alpaca_order_id": order.get("id")},
                            default=str,
                        ),
                    }
                ]
            }
        except Exception as exc:  # noqa: BLE001 -- surfaced as a tool error, not raised
            return {
                "content": [{"type": "text", "text": f"ERROR: {exc}"}],
                "is_error": True,
            }

    server = create_sdk_mcp_server(
        name=EXECUTION_SERVER_NAME,
        version="1.0.0",
        tools=[get_approved_orders_tool, place_approved_order_tool],
    )
    tools_by_name = {
        "get_approved_orders": get_approved_orders_tool,
        "place_approved_order": place_approved_order_tool,
    }
    return server, tools_by_name
